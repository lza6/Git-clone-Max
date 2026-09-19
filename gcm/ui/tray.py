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
        self._base_title = "Git-clone-Max"
        # G22-4：进度计数内存态 + 节流合并（32 并发高频信号不刷爆 tooltip）
        self._counts = {"running": 0, "ok": 0, "bad": 0}
        from PyQt6.QtCore import QTimer
        self._tip_timer = QTimer(self.parent)
        self._tip_timer.setSingleShot(True)
        self._tip_timer.setInterval(300)
        self._tip_timer.timeout.connect(self._flush_tooltip)

    def install(self, title: str = "Git-clone-Max"):
        if not ensure_supported():
            return None
        self._base_title = title
        tray = QSystemTrayIcon(self.parent)
        tray.setIcon(self._app_icon())
        tray.setToolTip(title)
        menu = self._build_menu()
        tray.setContextMenu(menu)
        tray.activated.connect(self._on_activated)
        tray.show()
        self.tray = tray
        return tray

    # ------------------------------------------------------------ G46-9 托盘菜单
    def _build_menu(self) -> QMenu:
        """构建托盘右键菜单（离屏可测：QMenu 构造不弹窗）。

        G46-9：显示主窗 / 打开统计中心 / 检查更新 / 开机自启(勾选) / 退出。
        回调经 hasattr 守卫，测试可用 mock parent 注入；开机自启勾选项与设置页
        ck_autostart（任务计划写入）联动。
        """
        menu = QMenu()
        act_show = menu.addAction("显示主窗口")
        act_stats = menu.addAction("打开统计中心")
        act_update = menu.addAction("检查更新")
        act_auto = menu.addAction("开机自启（登录时启动）")
        act_auto.setCheckable(True)
        try:
            if self.parent is not None and hasattr(self.parent, "ck_autostart"):
                act_auto.setChecked(bool(self.parent.ck_autostart.isChecked()))
        except Exception:
            pass
        menu.addSeparator()
        act_quit = menu.addAction("退出")
        act_show.triggered.connect(lambda: self._show_window())
        act_stats.triggered.connect(lambda: self._show_stats())
        act_update.triggered.connect(lambda: self._check_update())
        act_auto.toggled.connect(self._toggle_autostart)
        # G21-4：「退出」必须走主窗 closeEvent，统一优雅收尾
        # （engine.drain + 落盘 + 锁释放），不能直接 QApplication.quit 硬退
        act_quit.triggered.connect(self._quit_gracefully)
        return menu

    def _show_stats(self):
        """打开统计中心（parent.show_statistics）。"""
        try:
            if self.parent is not None and hasattr(self.parent, "show_statistics"):
                self.parent.show_statistics()
        except Exception:
            pass

    def _check_update(self):
        """检查更新（parent.check_update_now）。"""
        try:
            if self.parent is not None and hasattr(self.parent, "check_update_now"):
                self.parent.check_update_now()
        except Exception:
            pass

    def _toggle_autostart(self, checked: bool):
        """开机自启勾选：联动设置页 ck_autostart，触发任务计划写入逻辑。"""
        try:
            if self.parent is not None and hasattr(self.parent, "ck_autostart"):
                self.parent.ck_autostart.setChecked(bool(checked))
        except Exception:
            pass

    # ------------------------------------------------------------ G22-4 进度
    def update_counts(self, running_delta: int = 0, ok_delta: int = 0, bad_delta: int = 0,
                      reset: bool = False):
        """引擎信号桥接入口：增减计数并节流刷新 tooltip。"""
        if reset:
            self._counts = {"running": 0, "ok": 0, "bad": 0}
        self._counts["running"] = max(0, self._counts["running"] + running_delta)
        self._counts["ok"] = max(0, self._counts["ok"] + ok_delta)
        self._counts["bad"] = max(0, self._counts["bad"] + bad_delta)
        if self.tray is not None and not self._tip_timer.isActive():
            self._tip_timer.start()

    def _flush_tooltip(self):
        if self.tray is None:
            return
        c = self._counts
        if c["running"] > 0:
            tip = f"{self._base_title} · 运行中 {c['running']} · 完成 {c['ok']} · 失败 {c['bad']}"
        elif c["ok"] or c["bad"]:
            tip = f"{self._base_title} · 上轮完成 {c['ok']} · 失败 {c['bad']}"
        else:
            tip = self._base_title
        try:
            self.tray.setToolTip(tip)
        except Exception:
            pass

    def _quit_gracefully(self):
        """G21-4：先关主窗（触发 closeEvent 优雅收尾），事件循环随之自然退出。"""
        try:
            if self.parent is not None:
                self.parent.close()
                return
        except Exception:
            pass
        QApplication.instance().quit()

    def _app_icon(self):
        from PyQt6.QtGui import QColor, QPainter, QPixmap
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
