# -*- coding: utf-8 -*-
"""G10-1 字号缩放测试：qss_for_scale 按比例替换基础字号；边界钳制。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestFontScale(unittest.TestCase):
    def test_qss_for_scale_normal(self):
        from gcm.ui.theme import qss_for_scale, FONT_BASE_PX
        q1 = qss_for_scale(1.0)
        self.assertIn(f"font-size: {FONT_BASE_PX}px;", q1)
        q2 = qss_for_scale(1.5)
        self.assertIn(f"font-size: {round(FONT_BASE_PX * 1.5)}px;", q2)
        self.assertNotIn(f"font-size: {FONT_BASE_PX}px;", q2)

    def test_qss_for_scale_clamped(self):
        from gcm.ui.theme import qss_for_scale, FONT_BASE_PX
        # 超范围被钳制到 [0.8, 1.6]
        lo = qss_for_scale(0.1)
        self.assertIn(f"font-size: {round(FONT_BASE_PX * 0.8)}px;", lo)
        hi = qss_for_scale(9.0)
        self.assertIn(f"font-size: {round(FONT_BASE_PX * 1.6)}px;", hi)


class TestFontScaleSetting(unittest.TestCase):
    def test_font_scale_persists(self):
        from gcm.db.settings import SettingsStore, Settings
        d = Path(tempfile.mkdtemp())
        st = SettingsStore(d / "settings.json")
        s = st.load()
        s.font_scale = 1.25
        st.save(s)
        self.assertAlmostEqual(st.load().font_scale, 1.25)
        self.assertEqual(Settings().font_scale, 1.0)


class TestMainWindowFontScale(unittest.TestCase):
    def test_zoom_actions(self):
        from PyQt6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        d = Path(tempfile.mkdtemp())
        win = MainWindow(data_dir=d, db=Database(d / "t.db"),
                         settings=SettingsStore(d / "settings.json"))
        win.zoom_in()
        self.assertAlmostEqual(win.settings.font_scale, 1.1)
        win.zoom_out()
        win.zoom_out()
        self.assertAlmostEqual(win.settings.font_scale, 0.9)
        win.zoom_reset()
        self.assertAlmostEqual(win.settings.font_scale, 1.0)
        win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)