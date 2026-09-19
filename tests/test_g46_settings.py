# -*- coding: utf-8 -*-
"""G46 Phase4：设置字段 / 强调色 / reduced-motion / 恢复默认 / 帮助入口 / 打开数据目录。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gcm.db.repo_db import Database
from gcm.ui.main_window import MainWindow


def _make_window():
    tmp = Path(tempfile.mkdtemp(prefix="g46_set_"))
    db = Database(tmp / "t.db")
    w = MainWindow(data_dir=tmp, db=db, settings=None)
    return tmp, db, w


class TestSettingsFields(unittest.TestCase):
    """G46-7/G46-8：新设置字段持久化。"""

    def test_new_fields_defaults(self):
        from gcm.db.settings import Settings
        s = Settings()
        self.assertFalse(s.prefers_reduced_motion)
        self.assertEqual(s.accent_preset, "blue")

    def test_new_fields_persist(self):
        from gcm.db.settings import SettingsStore
        d = Path(tempfile.mkdtemp())
        st = SettingsStore(d / "s.json")
        s = st.load()
        s.prefers_reduced_motion = True
        s.accent_preset = "violet"
        st.save(s)
        s2 = st.load()
        self.assertTrue(s2.prefers_reduced_motion)
        self.assertEqual(s2.accent_preset, "violet")


class TestAccentAndMotion(unittest.TestCase):
    """G46-7/G46-8：强调色即时生效；reduced-motion 保存。"""

    def test_accent_preset_applies_qss(self):
        tmp, db, w = _make_window()
        try:
            panel = w.settings_panel
            idx = w.accent_combo.findData("violet")
            panel._save_accent_preset(idx)
            self.assertEqual(w.settings.accent_preset, "violet")
            qss = w.styleSheet()
            self.assertIn("#8250df", qss, "强调色应出现在 QSS")
        finally:
            db.close()

    def test_reduced_motion_saves(self):
        tmp, db, w = _make_window()
        try:
            w.settings_panel._save_reduced_motion(True)
            self.assertTrue(w.settings.prefers_reduced_motion)
        finally:
            db.close()


class TestResetDefaults(unittest.TestCase):
    """G46-10：恢复默认保留 token/geometry。"""

    def test_reset_keeps_token(self):
        tmp, db, w = _make_window()
        try:
            w.settings.token = "secret123"
            w.settings.concurrency = 32
            w.settings.theme = "nord"
            w.settings_store.save(w.settings)
            w.settings_panel._reset_defaults()
            loaded = w.settings_store.load()
            self.assertEqual(loaded.token, "secret123", "token 应保留")
            self.assertEqual(loaded.geometry, w.settings.geometry)
            self.assertEqual(loaded.concurrency, 8, "并发应回默认")
            self.assertEqual(loaded.theme, "deep", "主题应回默认")
        finally:
            db.close()


class TestHelpAndDataDir(unittest.TestCase):
    """G46-11 / 打开数据目录入口。"""

    def test_open_help_opens_dialog(self):
        tmp, db, w = _make_window()
        try:
            with mock.patch("gcm.ui.help_dialog.HelpDialog") as m_h:
                m_h.return_value.exec.return_value = 0
                w.settings_panel._open_help()
                m_h.assert_called_once()
                m_h.return_value.exec.assert_called_once()
        finally:
            db.close()

    def test_open_data_dir_calls_desktop(self):
        tmp, db, w = _make_window()
        try:
            with mock.patch("PyQt6.QtGui.QDesktopServices.openUrl") as m_open:
                w.settings_panel._open_data_dir()
                m_open.assert_called_once()
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)