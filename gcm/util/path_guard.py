"""B3：删除类操作的系统路径护栏。

为什么需要：`prune-untouched` 会对数据库里记录的 `local_path` 直接
`shutil.rmtree`。路径通常由本应用写入，但本项目有过 G69-1「无条件裸 rmtree」
的实测数据销毁事故 —— 一旦记录被迁移、手改或外部工具污染，代价不可逆。
这里只做**确定的灾难性路径拒绝**（盘根 / 系统目录 / 用户主目录及其受保护
子目录 / UNC / 相对路径），不做「只允许克隆根之下」的白名单限制：
自定义根目录、移动硬盘、网络盘上的仓库照常可清理。

判定只读环境变量与路径本身，不触网、不写盘；`deletion_refusal` 返回
中文原因（沿用 prune 模块既有 skipped_reason 的中文字面量风格），None = 放行。
"""
from __future__ import annotations

import os
from pathlib import Path

from ..i18n import tr

__all__ = ["deletion_refusal"]

# 盘符根下一级目录命中即拒绝（小写；Windows 系统级目录必然位于此层）。
# 审查修正：只匹配第一级 —— 深层的同名目录（`D:\work\boot\repo`）是普通
# 工作目录，按任意层级匹配会误拒合法清理。
_FORBIDDEN_COMPONENTS = frozenset({
    "windows", "program files", "program files (x86)", "programdata",
    "$recycle.bin", "system volume information", "recovery", "perflogs",
    "boot", "bootmgr", "documents and settings", "config.msi", "$sysreset",
    "$windows.~bt", "$windows.~ws", "$winreagent", "system32", "syswow64",
    "windows.old", "windows10upgrade", "$getcurrent",
})

# POSIX 顶层目录：仅当「整个路径就是该目录」时拒绝（/usr 拒；/usr/local/repo 放）
_POSIX_TOPLEVEL = frozenset({
    "bin", "boot", "dev", "etc", "home", "lib", "lib64", "media", "mnt",
    "opt", "proc", "root", "run", "sbin", "srv", "sys", "tmp", "usr", "var",
    "lost+found",
})

# 用户主目录下「本身即受保护」的子目录（等值命中才拒绝；其内部仓库照常放行）
_HOME_SUBDIRS = frozenset({
    "desktop", "documents", "downloads", "music", "pictures", "videos",
    "onedrive", "appdata",
    "桌面", "文档", "下载", "图片", "视频", "音乐",
})


def _norm(component: str) -> str:
    """大小写无关 + 去 Windows 尾随点/空格（`Windows.` 是 `Windows` 的别名）。"""
    return component.strip().rstrip(". ").lower()


def deletion_refusal(path: str | Path | None) -> str | None:
    """返回拒绝删除的原因；None 表示可以删除。绝不抛异常。"""
    try:
        raw = str(path or "").strip()
        if not raw or "\x00" in raw:
            return tr("路径为空或含空字节")
        p = Path(raw)
        if not p.is_absolute():
            return tr("不是绝对路径")
        if os.name == "nt" and p.anchor.startswith(("\\\\", "//")):
            return tr("UNC/网络共享路径")
        try:
            rp = p.resolve()
        except OSError:
            rp = p
        if len(rp.parts) <= 1:
            return tr("盘符根目录/文件系统根")
        comps = [_norm(x) for x in rp.parts[1:]]
        if comps and comps[0] in _FORBIDDEN_COMPONENTS:
            return tr("系统目录（{}）").format(comps[0])
        if os.name != "nt" and len(comps) == 1 and comps[0] in _POSIX_TOPLEVEL:
            return tr("系统目录（{}）").format("/" + comps[0])
        home = Path.home()
        try:
            rh = home.resolve()
        except OSError:
            rh = home
        home_checks_ok = True
        try:
            if rp == rh:
                return tr("用户主目录")
            if rh != rp and rh.is_relative_to(rp):
                return tr("用户主目录的上级目录")
        except (OSError, ValueError):
            home_checks_ok = False  # 主目录相关判定失败：跳过这两条（其余规则仍生效）
        if home_checks_ok:
            try:
                if rp.parent == rh and _norm(rp.name) in _HOME_SUBDIRS:
                    return tr("用户受保护文件夹")
            except (OSError, ValueError):
                return None  # 受保护子目录判定失败：不额外拒绝（保守方向相反会误伤）
        return None
    except Exception:  # noqa: BLE001 —— 护栏自身绝不把 prune 拖崩；保守拒绝
        return tr("路径判定失败（保守拒绝）")
