"""应用入口：数据目录解析 + 主窗口/CLI 启动（G45-3 / G49-2 / G49-3 / G49-6）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _default_base() -> Path:
    """可执行文件（打包）/源码根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def get_data_dir(portable: bool = False) -> Path:
    """数据目录解析（G49-2）：

    1. GCM_DATA_DIR 环境变量 → 直接用（最高优先级，已有行为）；
    2. --portable（portable=True）→ <exe/源码>/data（现状默认，随程序移动）；
    3. 默认 → %APPDATA%/Git-clone-Max；但若 <exe/源码>/data 已存在
       （旧版本用户升级）则继续沿用，保证数据不丢、无缝升级；
       无 APPDATA 的极简环境退回 <源码>/data。
    """
    env = os.environ.get("GCM_DATA_DIR")
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    base = _default_base()
    legacy = base / "data"
    if portable:
        legacy.mkdir(parents=True, exist_ok=True)
        return legacy
    if legacy.exists():
        return legacy
    for var in ("APPDATA", "XDG_DATA_HOME"):
        ap = os.environ.get(var)
        if ap:
            d = Path(ap) / "Git-clone-Max"
            d.mkdir(parents=True, exist_ok=True)
            return d
    legacy.mkdir(parents=True, exist_ok=True)
    return legacy


def _parse_flags(argv=None) -> dict:
    """解析 CLI 旗标：--portable / --minimized / --cli。"""
    args = list(sys.argv[1:] if argv is None else argv)
    return {
        "portable": "--portable" in args,
        "minimized": "--minimized" in args,
        "cli": "--cli" in args,
    }


def main(argv=None) -> int:
    flags = _parse_flags(argv)

    # G49-3：CLI 批量克隆模式（离屏，不创建 GUI 窗口）
    if flags["cli"]:
        from gcm.cli import main as cli_main
        return cli_main(argv)

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    data_dir = get_data_dir(portable=flags["portable"])
    # G06-2 single-instance：第二个实例被拦截并聚焦首个窗口
    from gcm.app.single_instance import SingleInstanceLock
    lock = SingleInstanceLock(data_dir / ".instance.lock")
    if not lock.try_acquire():
        print("Git-clone-Max 已在运行，本实例将退出。")
        return 0

    # G06-3 全局异常兜底：未捕获异常写 data/error.log
    from gcm.app.exc_dump import install_excepthook
    install_excepthook(data_dir / "error.log")

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Git-clone-Max")

    from gcm.db.repo_db import Database
    from gcm.db.settings import SettingsStore
    from gcm.ui.main_window import MainWindow

    db = Database(data_dir / "repos.db")
    settings = SettingsStore(data_dir / "settings.json")
    # G49-1：按 settings.language 初始化 i18n（设置页切换后重启生效）
    try:
        from gcm.i18n import set_language
        lang = str(getattr(settings.load(), "language", "zh") or "zh")
        set_language(lang)
    except Exception:
        pass

    # G49-6：--minimized 启动时若托盘可用则隐入托盘（不 show 主窗）
    win = MainWindow(data_dir=data_dir, db=db, settings=settings,
                     start_hidden=bool(flags["minimized"]))
    if not win.should_auto_hide():
        win.show()
    try:
        return app.exec()
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
