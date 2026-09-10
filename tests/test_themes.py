# -*- coding: utf-8 -*-
"""G05-1 多主题测试：Deep/Light/Nord 三套主题切换、持久化、UI 应用。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestThemePalettes(unittest.TestCase):
    def test_all_theme_keys(self):
        from gcm.ui.theme import THEMES, PALETTES
        self.assertIn("deep", PALETTES)
        self.assertIn("light", PALETTES)
        self.assertIn("nord", PALETTES)
        # 每套主题都有核心色键
        for name, pal in PALETTES.items():
            for k in ("bg", "panel", "text", "accent", "border"):
                self.assertIn(k, pal, f"主题 {name} 缺色键 {k}")

    def test_themes_are_different(self):
        from gcm.ui.theme import PALETTES
        self.assertNotEqual(PALETTES["deep"]["bg"], PALETTES["light"]["bg"])
        self.assertNotEqual(PALETTES["nord"]["accent"], PALETTES["light"]["accent"])


class TestApplyTheme(unittest.TestCase):
    def test_apply_theme_sets_palette(self):
        from gcm.ui.theme import apply_theme, current_palette
        apply_theme("light")
        self.assertEqual(current_palette()["bg"], "#ffffff")
        apply_theme("deep")
        self.assertEqual(current_palette()["bg"], "#0d1117")
        # 未知主题回退 deep
        apply_theme("nope")
        self.assertEqual(current_palette()["bg"], "#0d1117")


class TestSettingsTheme(unittest.TestCase):
    def test_theme_setting_persists(self):
        from gcm.db.settings import SettingsStore, Settings
        d = Path(tempfile.mkdtemp())
        st = SettingsStore(d / "settings.json")
        s = st.load()
        s.theme = "nord"
        st.save(s)
        s2 = st.load()
        self.assertEqual(s2.theme, "nord")
        # 默认 deep
        s3 = Settings()
        self.assertEqual(s3.theme, "deep")


class TestMainWindowTheme(unittest.TestCase):
    def test_window_has_theme_selector(self):
        from PyQt6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        d = Path(tempfile.mkdtemp())
        win = MainWindow(data_dir=d, db=Database(d / "t.db"),
                         settings=SettingsStore(d / "settings.json"))
        self.assertTrue(hasattr(win, "theme_combo"), "设置页应有主题下拉")
        items = [win.theme_combo.itemText(i) for i in range(win.theme_combo.count())]
        self.assertIn("深色 (Deep)", items)
        self.assertIn("浅色 (Light)", items)
        win.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestThemeLogAndHighlight(unittest.TestCase):
    """补齐 theme.py 遗漏行：LogModel 批量/裁剪、highlighter、maybe_validate_url。"""

    def test_log_model_append_many_and_trim(self):
        from gcm.ui.theme import LogModel, LogLevel, LogEvent
        m = LogModel(max_entries=5)
        m.append_many([LogEvent(f"t{i}", LogLevel.INFO, f"msg{i}")
                       for i in range(10)])
        self.assertEqual(len(m._entries), 5)  # 裁剪到 max
        self.assertGreater(m._dropped, 0)
        # snapshot 返回副本
        snap = m.snapshot()
        self.assertEqual(len(snap), 5)

    def test_log_model_trim_return(self):
        from gcm.ui.theme import LogModel, LogLevel
        m = LogModel(max_entries=3)
        for i in range(5):
            m.append(f"t{i}", LogLevel.INFO, f"m{i}")
        self.assertEqual(len(m._entries), 3)
        self.assertEqual(m._dropped, 2)

    def test_make_highlighter(self):
        from PyQt6.QtWidgets import QApplication, QPlainTextEdit
        _app = QApplication.instance() or QApplication([])
        from gcm.ui.theme import make_highlighter
        ed = QPlainTextEdit()
        hl = make_highlighter(ed.document())
        self.assertIsNotNone(hl)

    def test_maybe_validate_url(self):
        from gcm.ui.theme import maybe_validate_url
        self.assertEqual(maybe_validate_url(""), "地址为空")
        self.assertEqual(maybe_validate_url("not a url"), "不是有效的 GitHub 仓库地址")
        self.assertIsNone(maybe_validate_url("https://github.com/a/b"))
