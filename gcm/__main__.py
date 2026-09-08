# -*- coding: utf-8 -*-
"""应用入口：数据目录解析 + 主窗口启动。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

def get_data_dir() -> Path:
    """数据目录：默认与可执行文件同级；可通过 GCM_DATA_DIR 覆盖。"""
    env = os.environ.get("GCM_DATA_DIR")
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).resolve().parent.parent  # Git-clone-Max/
    d = base / "data"
    d.mkdir(parents=True, exist_ok=True)
    return d


def main() -> int:
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Git-clone-Max")

    from gcm.db.repo_db import Database
    from gcm.db.settings import SettingsStore
    from gcm.ui.main_window import MainWindow

    data_dir = get_data_dir()
    db = Database(data_dir / "repos.db")
    settings = SettingsStore(data_dir / "settings.json")
    win = MainWindow(data_dir=data_dir, db=db, settings=settings)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
