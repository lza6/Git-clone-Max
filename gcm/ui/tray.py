# -*- coding: utf-8 -*-
"""系统托盘：最小化挂机时的图标、进度摘要与通知。"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon


def ensure_supported() -> bool:
    # offscreen 平台插件下 QSystemTrayIcon.isSystemTrayAvailable() 在 Windows 会
    # 触发访问违规（Qt 环境问题，非代码 bug）。offscreen 本就没有系统托盘 → 提前返回 False。
    if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
        return False
    return QSystemTrayIcon.isSystemTrayAvailable()


class TrayController:
    """封装系统托盘生命周期。独立于 MainWindow，便于测试注入。"""

    def __init__(self, parent=None, notify=None):
        self.parent = parent
        self._notify = notify or _default_notify
        self.tray = None

    def install(self, title: str = "Git-clone-Max"):
        if not ensure_supported():
            return None
        tray = QSystemTrayIcon(self.parent)
        tray.setIcon(self._app_icon())
        tray.setToolTip(title)
        menu = QMenu()
        act_show = menu.addAction("显示主窗口")
        act_quit = menu.addAction("退出")
        menu.addSeparator()
        act_show.triggered.connect(lambda: self._show_window())
        act_quit.triggered.connect(QApplication.instance().quit)
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_activated)
        tray.show()
        self.tray = tray
        return tray

    def _app_icon(self):
        from PyQt6.QtGui import QPixmap, QPainter, QColor
        pm = QPixmap(64, 64)
        pm.fill(QColor("#0d1117"))
        p = QPainter(pm)
        p.setPen(QColor("#58a6ff"))
        p.setFont(p.font())  # 默认字体
        p.drawText(pm.rect(), Qt.AlignmentFlag.AlignCenter, "G")
        p.end()
        return QIcon(pm)

    def _show_window(self):
        if self.parent is not None:
            self.parent.showNormal()
            self.parent.raise_()
            self.parent.activateWindow()

    def _on_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._show_window()

    def notify(self, title: str, message: str):
        if self.tray is not None:
            self.tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)
        elif self._notify:
            self._notify(title, message)


def _default_notify(title: str, message: str):
    print(f"[通知] {title}: {message}")