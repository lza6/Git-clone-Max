# -*- coding: utf-8 -*-
"""G46-9/G46-11：托盘菜单与使用说明对话框测试（离屏，mock parent）。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication, QWidget
_app = QApplication.instance() or QApplication([])


class _FakeParent(QWidget):
    """真实 QWidget 父（QTimer 需要 QObject），方法打桩可断言。"""

    def __init__(self):
        super().__init__()
        self.ck_autostart = mock.MagicMock()
        self.ck_autostart.isChecked.return_value = True
        self.show_statistics = mock.MagicMock()
        self.check_update_now = mock.MagicMock()
        self.showNormal = mock.MagicMock()
        self.show = mock.MagicMock()
        self.raise_ = mock.MagicMock()
        self.activateWindow = mock.MagicMock()


class TestHelpDialog(unittest.TestCase):
    """G46-11：使用说明对话框内容与结构。"""

    def test_help_html_contains_sections(self):
        from gcm.ui.help_dialog import help_html, HELP_SECTIONS
        html = help_html()
        for title, lines in HELP_SECTIONS:
            self.assertIn(title, html, f"缺少章节 {title}")
            for line in lines:
                self.assertIn(line, html, f"缺少内容 {line}")

    def test_dialog_title_and_browser(self):
        from gcm.ui.help_dialog import HelpDialog
        dlg = HelpDialog()
        try:
            self.assertEqual(dlg.windowTitle(), "使用说明")
            joined = dlg.browser.toPlainText() + dlg.browser.toHtml()
            self.assertIn("@v1.2.0", joined)
            self.assertIn("host=token", joined)
            self.assertIn("Ctrl+Alt+M", dlg.browser.toHtml())
        finally:
            dlg.close()


class TestTrayMenu(unittest.TestCase):
    """G46-9：托盘菜单动作与回调（mock parent）。"""

    def _make(self):
        from gcm.ui.tray import TrayController
        parent = _FakeParent()
        t = TrayController(parent=parent)
        return t, parent

    def test_menu_actions_present(self):
        t, parent = self._make()
        menu = t._build_menu()
        labels = [a.text() for a in menu.actions()]
        for want in ("显示主窗口", "打开统计中心", "检查更新", "开机自启（登录时启动）", "退出"):
            self.assertIn(want, labels)

    def test_show_stats_callback(self):
        t, parent = self._make()
        t._show_stats()
        parent.show_statistics.assert_called_once()

    def test_check_update_callback(self):
        t, parent = self._make()
        t._check_update()
        parent.check_update_now.assert_called_once()

    def test_autostart_toggle_links_checkbox(self):
        t, parent = self._make()
        t._toggle_autostart(False)
        parent.ck_autostart.setChecked.assert_called_once_with(False)

    def test_show_window_callback(self):
        t, parent = self._make()
        t._show_window()
        parent.showNormal.assert_called_once()
        parent.raise_.assert_called_once()
        parent.activateWindow.assert_called_once()

    def test_double_click_activation(self):
        from PyQt6.QtWidgets import QSystemTrayIcon
        t, parent = self._make()
        t._on_activated(QSystemTrayIcon.ActivationReason.Trigger)
        parent.showNormal.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)