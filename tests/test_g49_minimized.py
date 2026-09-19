# -*- coding: utf-8 -*-
"""G49-6/7/8 设置与启动：start_hidden/should_auto_hide、设置持久化、Webhook 开关联动、打开数据目录。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _mk_window(tmp):
    from gcm.db.repo_db import Database
    from gcm.db.settings import SettingsStore
    from gcm.ui.main_window import MainWindow
    return MainWindow(data_dir=tmp, db=Database(tmp / "t.db"),
                      settings=SettingsStore(tmp / "s.json"))


class TestMinimizedStart(unittest.TestCase):
    def test_default_should_show(self):
        with tempfile.TemporaryDirectory() as d:
            w = _mk_window(Path(d))
            try:
                self.assertFalse(w.should_auto_hide())
            finally:
                w.close()

    def test_start_hidden_with_tray_should_hide(self):
        with tempfile.TemporaryDirectory() as d:
            w = _mk_window(Path(d))
            try:
                w.start_hidden = True
                w.tray.tray = object()  # 模拟托盘已安装
                self.assertTrue(w.should_auto_hide())
            finally:
                w.close()

    def test_start_hidden_without_tray_shows(self):
        with tempfile.TemporaryDirectory() as d:
            w = _mk_window(Path(d))
            try:
                w.start_hidden = True
                w.tray.tray = None
                self.assertFalse(w.should_auto_hide())
            finally:
                w.close()


class TestSettingsPersistence(unittest.TestCase):
    def test_language_save(self):
        with tempfile.TemporaryDirectory() as d:
            w = _mk_window(Path(d))
            try:
                w.lang_combo.setCurrentIndex(w.lang_combo.findData("en"))
                self.assertEqual(w.settings.language, "en")
            finally:
                w.close()

    def test_start_minimized_save(self):
        with tempfile.TemporaryDirectory() as d:
            w = _mk_window(Path(d))
            try:
                w.ck_start_min.setChecked(True)
                self.assertTrue(w.settings.start_minimized)
                w.ck_start_min.setChecked(False)
                self.assertFalse(w.settings.start_minimized)
            finally:
                w.close()

    def test_webhook_save_and_redact_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            w = _mk_window(Path(d))
            try:
                w.ck_notify.setChecked(True)
                w.edit_webhook_url.setText("https://example.com/hook")
                w.edit_webhook_token.setText("secret-token-123")
                w.settings_panel._save_notify_webhook()
                self.assertTrue(w.settings.notify_webhook_enabled)
                self.assertEqual(w.settings.notify_webhook_url,
                                 "https://example.com/hook")
                self.assertEqual(w.settings.notify_webhook_token,
                                 "secret-token-123")
                # 磁盘上 token 不得明文（G44 同策略）
                raw = (Path(d) / "s.json").read_text(encoding="utf-8")
                self.assertNotIn("secret-token-123", raw)
                # 重载恢复
                w2 = _mk_window(Path(d))
                try:
                    self.assertEqual(w2.settings.notify_webhook_token,
                                     "secret-token-123")
                    self.assertTrue(w2.settings.notify_webhook_enabled)
                finally:
                    w2.close()
            finally:
                w.close()

    def test_autostart_toggle_writes_task(self):
        from gcm.ui import main_window as _mw  # 触发模块导入（不真实写任务计划）
        with tempfile.TemporaryDirectory() as d:
            with mock.patch("gcm.app.autostart.autostart_enabled",
                            return_value=False), \
                 mock.patch("gcm.app.autostart.set_autostart",
                            return_value=(True, "ok")) as sa:
                w = _mk_window(Path(d))
                try:
                    self.assertFalse(w.ck_autostart.isChecked())  # 初始回填 False
                    w.ck_autostart.setChecked(True)
                    self.assertTrue(sa.called)
                    args = sa.call_args.args
                    self.assertTrue(args[0])  # enabled=True
                finally:
                    w.close()


class TestOpenDataDir(unittest.TestCase):
    def test_open_data_dir_calls_openUrl(self):
        with tempfile.TemporaryDirectory() as d:
            w = _mk_window(Path(d))
            try:
                with mock.patch("PyQt6.QtGui.QDesktopServices.openUrl") as o:
                    w.settings_panel._open_data_dir()
                o.assert_called_once()
            finally:
                w.close()
