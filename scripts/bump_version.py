"""一次改完所有版本载体（G84-4）。

**为什么需要**：版本号有 10 类载体（`gcm/__init__.py` 是唯一事实源，其余必须跟随）。
v9.17.0 那次 bump 实测**漏了 3 处**（`decisions.md` D-G83-3），因为人靠记忆逐处手改。
本脚本把它们变成一次原子改写 + 自证。

**设计约束（对齐本项目既有纪律）**
1. **不做全文盲替**：只按「载体表」里的上下文正则改，绝不碰 `gcm/_changelog_data.py`
   里的历史条目、`.beads/*` 里的议题标题、`docs/验证记录-*` 里的当轮记录。
2. **默认 dry-run**：不加 `--write` 只打印计划。改之前必须在干净工作区上
   （`--allow-dirty` 显式放行）——在脏树上 bump 会把别人的改动混进来，事后无法定位。
3. **改完自证**：`--write` 后自动跑 `tests.test_g66_patch.TestPackagingVersionSync`
   与版本覆盖守卫；不通过则以非零退出，并把改动回滚（写前快照原文）。
4. **不伪造 changelog**：`gcm.__changelog__` 里没有新版本条目时会**明确报错**并提示
   去跑 `python scripts/changelog.py new/build`，而不是替你编一条。

用法::

    python scripts/bump_version.py --bump patch              # 预览 9.17.0 → 9.17.1
    python scripts/bump_version.py --bump minor --write      # 写入并自证
    python scripts/bump_version.py --to 10.0.0 --write
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: 版本载体表：(相对路径, 正则, 替换模板)。
#: 每条都带足够上下文，保证只会命中"当前版本"这一个位置。
CARRIERS: tuple[tuple[str, str, str], ...] = (
    # 1. 唯一事实源
    ("gcm/__init__.py", r'(?m)^__version__ = "{old}"$', '__version__ = "{new}"'),
    # 2. 打包元数据
    ("pyproject.toml", r'(?m)^version = "{old}"$', 'version = "{new}"'),
    # 3. PyInstaller（filevers/prodvers 是元组，两个字符串结构体各一处）
    ("Git-clone-Max.spec",
     r"filevers=\({maj}, {min}, {pat}, 0\)",
     "filevers=({newmaj}, {newmin}, {newpat}, 0)"),
    ("Git-clone-Max.spec",
     r"prodvers=\({maj}, {min}, {pat}, 0\)",
     "prodvers=({newmaj}, {newmin}, {newpat}, 0)"),
    ("Git-clone-Max.spec",
     r"StringStruct\('FileVersion', '{old}'\)",
     "StringStruct('FileVersion', '{new}')"),
    ("Git-clone-Max.spec",
     r"StringStruct\('ProductVersion', '{old}'\)",
     "StringStruct('ProductVersion', '{new}')"),
    # 4. Inno Setup（版本宏 + 头部注释里的产物名）
    ("packaging/installer.iss",
     r'#define MyAppVersion "{old}"',
     '#define MyAppVersion "{new}"'),
    ("packaging/installer.iss",
     r"Git-clone-Max-setup-{old}\.exe",
     "Git-clone-Max-setup-{new}.exe"),
    # 5. 插件清单（Claude Code / agent-plugins.org 惯例）
    (".claude-plugin/plugin.json", r'"version": "{old}"', '"version": "{new}"'),
    # 6-7. 契约 schema（agent 拿到的 version 字段示例值）
    ("docs/contract/cli_json.schema.json", r'"version": "{old}"',
     '"version": "{new}"'),
    ("docs/contract/http_endpoints.schema.json", r'"version": "{old}"',
     '"version": "{new}"'),
    # 8. agent 契约文档（适用版本 + 两处示例响应）
    ("docs/agent-contract.md", r"适用版本：v{old}", "适用版本：v{new}"),
    ("docs/agent-contract.md", r'"version": "{old}"', '"version": "{new}"'),
    ("docs/agent-contract.md", r'"version":"{old}"', '"version":"{new}"'),
    # 9. winget 清单三件套
    ("packaging/winget/lza6.Git-clone-Max.yaml",
     r"PackageVersion: {old}", "PackageVersion: {new}"),
    ("packaging/winget/lza6.Git-clone-Max.installer.yaml",
     r"PackageVersion: {old}", "PackageVersion: {new}"),
    ("packaging/winget/lza6.Git-clone-Max.installer.yaml",
     r"releases/download/v{old}/", "releases/download/v{new}/"),
    ("packaging/winget/lza6.Git-clone-Max.locale.zh-CN.yaml",
     r"PackageVersion: {old}", "PackageVersion: {new}"),
    # 10. README（版本横幅 + 功能表/发布示例里出现的 vX.Y.Z）
    ("README.md", r"\*\*当前版本：v{old}\*\*", "**当前版本：v{new}**"),
    ("README.md", r"v{old}", "v{new}"),
)

#: 允许保留旧版本字面量的文件（历史记录 / 定版快照，机械改写会篡改事实）。
#: 新增排除项必须写清理由，否则守卫（tests/test_g84_bump_version.py）会拦。
ALLOWED_HISTORICAL = {
    "gcm/_changelog_data.py": "逐版本历史条目，改写等于篡改历史",
    "CLAUDE.md": "带日期的实测快照（'2026-11-03 v9.17.0 实测'），重测时才改",
    "test_ledger.md": "带日期的接缝台账抬头，重测时才改",
    "docs/architecture.md": "带日期的模块规模基线，重测时才改",
    "docs/agent-contract.md": "本文件另有 2 处必须跟随（见载体表），此处仅兜底",
    ".beads/issues.jsonl": "议题标题里的版本是历史记录",
}
#: 目录前缀排除（历史归档、验证记录、证据、工作流状态文件）。
ALLOWED_PREFIXES = (
    "docs/验证记录-", "docs/archive/", "docs/reports/", "evidence/",
    ".agent/", ".beads/", "dist/", "build/", "out/", "reports/",
)


@dataclass
class Change:
    path: str
    hits: int

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return f"{self.path}: {self.hits} 处"


def current_version() -> str:
    text = (ROOT / "gcm" / "__init__.py").read_text(encoding="utf-8")
    m = re.search(r'(?m)^__version__ = "([^"]+)"$', text)
    if not m:
        raise RuntimeError("gcm/__init__.py 里找不到 __version__")
    return m.group(1)


def compute_next(cur: str, bump: str | None, to: str | None) -> str:
    if to:
        if not re.fullmatch(r"\d+\.\d+\.\d+", to):
            raise RuntimeError(f"--to 需要 X.Y.Z 形式，收到 {to!r}")
        return to
    if not bump:
        raise RuntimeError("必须给 --bump {patch,minor,major} 或 --to X.Y.Z")
    maj, mi, pa = (int(x) for x in cur.split("."))
    if bump == "patch":
        return f"{maj}.{mi}.{pa + 1}"
    if bump == "minor":
        return f"{maj}.{mi + 1}.0"
    if bump == "major":
        return f"{maj + 1}.0.0"
    raise RuntimeError(f"未知 --bump 值：{bump}")


def _fmt(pattern: str, old: str, new: str) -> str:
    o = old.split(".")
    n = new.split(".")
    return pattern.format(old=old, new=new, maj=o[0], min=o[1], pat=o[2],
                          newmaj=n[0], newmin=n[1], newpat=n[2])


@dataclass
class Snapshot:
    """一份载体的原始字节 + 归一化文本 + 行尾约定。"""

    raw: bytes          # 原始字节（回滚用，保证逐字节还原）
    text: str           # 当前文本，统一按 \n
    new_text: str       # 改写后文本，仍按 \n
    newline: str        # 原文件行尾（"\n" 或 "\r\n"）


def _read_snapshot(path: Path) -> Snapshot:
    """读文件并记住行尾约定。

    **为什么不用 `read_text`**：Windows 工作区文件可能是 CRLF（`core.autocrlf`），
    `read_text` 会静默折成 LF，回滚时按 LF 写回就等于**顺手改了行尾**，在
    `git status` 里留下一堆「内容没变但被标记修改」的噪音。所以这里按字节读写。
    """
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return Snapshot(raw=raw, text=text.replace("\r\n", "\n"), new_text="",
                    newline=newline)


def plan(old: str, new: str) -> tuple[dict[Path, Snapshot], list[Change]]:
    """返回 (原文快照, 变更清单)。不改盘。"""
    snapshots: dict[Path, Snapshot] = {}
    changes: list[Change] = []
    for rel, pattern, repl in CARRIERS:
        path = ROOT / rel
        if not path.is_file():
            continue
        if path not in snapshots:
            snapshots[path] = _read_snapshot(path)
        snap = snapshots[path]
        rx = re.compile(_fmt(pattern, old, new))
        base = snap.new_text or snap.text
        new_text, hits = rx.subn(_fmt(repl, old, new), base)
        snap.new_text = new_text
        if hits:
            changes.append(Change(rel, hits))
    return snapshots, changes


def is_dirty() -> bool:
    out = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, text=True,
                         capture_output=True, encoding="utf-8", errors="replace")
    return bool(out.stdout.strip())


def verify(new: str) -> int:
    from gcm import __version__  # noqa: PLC0415 - 必须在改盘后重新导入

    if __version__ != new:
        print(f"✗ 自证失败：gcm.__version__ = {__version__}，期望 {new}")
        return 1
    rc = subprocess.run(
        [sys.executable, "-m", "unittest",
         "tests.test_g66_patch.TestPackagingVersionSync",
         "tests.test_g84_bump_version"],
        cwd=ROOT, text=True, encoding="utf-8", errors="replace").returncode
    if rc != 0:
        print("✗ 版本守卫未通过（见上）")
        return rc
    print("✓ 版本守卫通过")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="一次改完所有版本载体")
    ap.add_argument("--bump", choices=["patch", "minor", "major"])
    ap.add_argument("--to")
    ap.add_argument("--write", action="store_true", help="真正写盘（默认只预览）")
    ap.add_argument("--allow-dirty", action="store_true", help="允许在脏工作区执行")
    ap.add_argument("--no-verify", action="store_true", help="跳过改后自证")
    a = ap.parse_args(argv)

    old = current_version()
    try:
        new = compute_next(old, a.bump, a.to)
    except RuntimeError as e:
        print(f"✗ {e}")
        return 2
    if new == old:
        print(f"✗ 新版本与当前一致（{old}）")
        return 2

    snaps, changes = plan(old, new)
    print(f"版本：{old} → {new}")
    if not changes:
        print("✗ 载体表一处都没命中 —— 模板已漂移，先修 CARRIERS")
        return 1
    for c in changes:
        print(f"   {c}")

    # changelog 条目检查（不替你编历史）
    changelog = (ROOT / "gcm" / "_changelog_data.py").read_text(encoding="utf-8")
    if f"{new}:" not in changelog:
        print(f"! gcm.__changelog__ 里没有 {new}: 条目 —— 版本守卫会红。"
              f"\n  正确顺序：python scripts/changelog.py new --category … && "
              f"python scripts/changelog.py build --version {new}")

    if not a.write:
        print("\n（dry-run：未写盘。加 --write 执行）")
        return 0

    if is_dirty() and not a.allow_dirty:
        print("✗ 工作区有未提交改动 —— 拒绝在脏树上 bump（会把别人的改动混进来）。"
              "\n  先提交/暂存，或用 --allow-dirty 显式放行。")
        return 3

    written: list[Path] = []
    try:
        for path, snap in snaps.items():
            if snap.new_text != snap.text:
                body = snap.new_text.replace("\n", snap.newline)
                path.write_bytes(body.encode("utf-8"))
                written.append(path)
        if a.no_verify:
            return 0
        rc = verify(new)
        if rc != 0:
            raise RuntimeError("版本守卫未通过")
        return 0
    except Exception as e:  # noqa: BLE001 - 失败必须回滚，不能让仓库停在半改状态
        print(f"✗ {e}；回滚 {len(written)} 个文件")
        for path in written:
            path.write_bytes(snaps[path].raw)   # 逐字节还原（含行尾）
        return 1


if __name__ == "__main__":
    sys.exit(main())
