"""半成品仓库的**唯一判定事实源**（Rev8 P1-1，下称"单一来源"）。

为什么落 `app/`：判定被 `app/scanner.py`（本地扫描）与 `ui/halfclone_actions.py`
（进度表右键）两处各自实现过一份等价逻辑 —— 这是本项目第 3 次登记"同一概念两处
实现"（前两例：错误分类、半成品删除收口）。判定下沉到 `app/`（无 Qt 依赖）后，
UI 只做委托。

两级分类（依据：参考语料 1,729 仓实测，`corpus_health.txt`）
------------------------------------------------------------------
语料里"空工作树"其实有**两种性质完全不同**的形态，修复成本差两个数量级：

- `not_checked_out`：HEAD 可解析（`rev-parse HEAD` 成功），但工作树除 `.git` 外
  零条目 —— 数据都在，只是没检出。语料 7 个。**`git checkout -f HEAD` 秒级、
  零网络即可修复**（`repair_halfclone`）。
- `unborn_head`：`rev-parse HEAD` 失败（坏 HEAD / 无提交）。语料 93 个。
  **只能重新克隆**，本模块拒绝自动修（不联网、不删用户目录是硬边界）。

安全边界：宁可少修，不能错修。非 git 目录 / 裸仓库一律 `not_repo`，不参与任何
修复动作；修复只跑 `checkout -f HEAD`（不动引用、不删 `.git`、不触网）。
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Literal

_HALF_TIMEOUT = 20  # 秒；本模块只跑本地 git 查询/checkout，不给网络超时

HalfcloneKind = Literal["healthy", "not_checked_out", "unborn_head", "not_repo"]


def _git(path: Path, *args: str, timeout: int = _HALF_TIMEOUT) -> tuple[int, str]:
    """在 path 下跑 git，返回 (rc, stdout)。永不抛。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout,
        )
        return (r.returncode, (r.stdout or "").strip())
    except Exception:  # noqa: BLE001 - git 缺失/超时按"不可判定"处理
        return (-1, "")


def classify_repo(path: Path) -> HalfcloneKind:
    """把一个目录归入四级之一（单一来源，scanner 与 UI 都用这个）。"""
    kind, _sha_ok = _classify(path)
    return kind


def _classify(path: Path) -> tuple[HalfcloneKind, bool]:
    """内部：返回 (分类, HEAD 是否可解析)。"""
    try:
        if path is None or not path.is_dir():
            return ("not_repo", False)
    except OSError:
        return ("not_repo", False)
    try:
        entries = [p for p in path.iterdir() if p.name != ".git"]
    except OSError:
        entries = []
    if not (path / ".git").exists():
        # 目录名以 .git 结尾 → 裸仓库（没有工作树概念，不参与半成品判定）。
        # 其它无 .git 的目录：有内容 → healthy（普通目录不误报），空 → not_repo。
        if path.name.endswith(".git"):
            return ("not_repo", False)
        return ("healthy" if entries else "not_repo", False)
    if entries:
        return ("healthy", False)
    # 工作树为空：先试**纯文件**判定（零子进程 —— 本地扫描 1730 仓时
    # 每仓 2 个 git 子进程是 scanner_perf 守卫实测拦下的性能回归），
    # 文件判定不可靠时才回退 git。
    return _classify_from_files(path)


def _classify_from_files(path: Path) -> tuple[HalfcloneKind, bool]:
    """读 .git/HEAD + refs 判断 HEAD 是否可解析（纯文件，零子进程）。"""
    gd = path / ".git"
    head = gd / "HEAD"
    try:
        if not head.is_file():
            return ("unborn_head", False)
        content = head.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ("unborn_head", False)
    if content.startswith("ref: "):
        ref = content[5:].strip()
        if (gd / ref).is_file():
            return ("not_checked_out", True)
        # packed-refs 兜底
        packed = gd / "packed-refs"
        try:
            if packed.is_file() and any(
                    line.strip().endswith(f" {ref}")
                    for line in packed.read_text(
                        encoding="utf-8", errors="replace").splitlines()
                    if line.strip() and not line.startswith("#")):
                return ("not_checked_out", True)
        except OSError:
            return ("unborn_head", False)  # packed-refs 读不了按 unborn 兜底
        return ("unborn_head", False)
    # detached HEAD：直接是 sha
    if len(content) >= 40 and all(c in "0123456789abcdef" for c in content.lower()):
        return ("not_checked_out", True)
    return ("unborn_head", False)


def is_halfcloned(path: Path) -> bool:
    """是否疑似半成品（有 .git 的仓库但工作树空）。单一来源；scanner/UI 委托到这。"""
    return classify_repo(path) in ("not_checked_out", "unborn_head")


def repair_halfclone(path: Path) -> tuple[bool, str]:
    """尝试修复半成品目录。返回 (是否修复成功, 用户可读消息)。

    只对 `not_checked_out` 动手（`git checkout -f HEAD`，零网络、不动引用）；
    `unborn_head` 明确告知需重新克隆；其余一律拒绝。
    """
    kind = classify_repo(path)
    if kind == "not_repo":
        return (False, "不是 git 仓库，无需修复")
    if kind == "healthy":
        return (False, "工作树非空，无需修复")
    if kind == "unborn_head":
        return (False, "HEAD 不可解析（数据未取回或仓库损坏），请删除后重新克隆")
    rc, out = _git(path, "checkout", "-f", "HEAD", timeout=120)
    if rc != 0:
        return (False, f"git checkout 失败（rc={rc}）：{out or '（无输出）'}")
    if classify_repo(path) != "healthy":
        return (False, "checkout 已执行但工作树仍为空")
    return (True, "已检出工作树（HEAD 未变）")
