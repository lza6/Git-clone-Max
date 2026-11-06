"""Rev14 适配守卫：四档断点、多显示器纯函数、L 档真实差异、几何里的屏名。

**为什么这些必须是测试而不是"目检"**：断点与屏幕推导的失败形态是**静默**的
（窗口能开、控件在、只是某一档行为与另一档完全相同）。Rev13 R4 记的
「`wide` 是死枚举」正是这样活了很久：`layout_mode()` 一直返回 `"wide"`，
但没有任何代码消费它，肉眼完全看不出区别。
"""
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

from gcm.ui.layout import (  # noqa: E402
    BREAKPOINT_L,
    BREAKPOINT_M,
    BREAKPOINT_S,
    BREAKPOINT_XS,
    breakpoint_name,
    coerce_int,
    has_touchscreen,
    is_compact_bp,
    layout_mode,
    minimum_for_screen,
    resolve_screen,
)


class TestBreakpoints(unittest.TestCase):
    def test_table_matches_measured_anchors(self):
        """档位锚点来自实测尺寸，不是拍的：900×480 / 1240×729 / 1440×900 / 1600×1000。"""
        cases = {
            (900, 480): BREAKPOINT_XS,      # 1366×768@150% 工作区夹回后的真实最小窗
            (911, 512): BREAKPOINT_XS,
            (1020, 560): BREAKPOINT_XS,     # 声明的最小高度之下（h < 580）
            (1020, 584): BREAKPOINT_S,      # 被最小高度抬到 584 之后
            (1240, 729): BREAKPOINT_S,      # 1366×768 笔记本 clamp 后的实际尺寸
            (1366, 768): BREAKPOINT_S,      # 高 768 < 820
            (1440, 900): BREAKPOINT_M,      # 高 900 < 1000
            (1440, 1000): BREAKPOINT_L,
            (1600, 1000): BREAKPOINT_L,
            (1920, 1080): BREAKPOINT_L,
        }
        for size, expect in cases.items():
            with self.subTest(size=size):
                self.assertEqual(breakpoint_name(*size), expect)

    def test_compact_flag_covers_xs_and_s(self):
        self.assertTrue(is_compact_bp(BREAKPOINT_XS))
        self.assertTrue(is_compact_bp(BREAKPOINT_S))
        self.assertFalse(is_compact_bp(BREAKPOINT_M))
        self.assertFalse(is_compact_bp(BREAKPOINT_L))

    def test_breakpoint_is_monotonic_in_width(self):
        """宽度递增时档位不得回退（S → M → L 单调，允许跳过）。"""
        order = {BREAKPOINT_XS: 0, BREAKPOINT_S: 1, BREAKPOINT_M: 2, BREAKPOINT_L: 3}
        prev = -1
        for w in range(900, 2000, 40):
            rank = order[breakpoint_name(w, 1080)]
            self.assertGreaterEqual(rank, prev, f"{w}px 处档位回退了")
            prev = rank

    def test_bad_input_does_not_raise(self):
        """坏入参不得抛异常（断点计算在每次 resize 的路径上）。"""
        for bad in (None, "", "abc", 12.5, [], {}):
            self.assertIn(breakpoint_name(bad, bad),
                          (BREAKPOINT_XS, BREAKPOINT_S, BREAKPOINT_M, BREAKPOINT_L))

    def test_layout_mode_still_backward_compatible(self):
        """`layout_mode()` 是既有 API，返回值不得变（有 5 处既有断言依赖它）。"""
        self.assertEqual(layout_mode(1019, 700), "unsupported")
        self.assertEqual(layout_mode(1280, 900), "standard")
        self.assertEqual(layout_mode(1440, 1000), "wide")

    def test_coerce_int(self):
        self.assertEqual(coerce_int("1240", 0), 1240)
        self.assertEqual(coerce_int(12.0, 0), 12)
        self.assertEqual(coerce_int(12.5, 7), 7)
        self.assertEqual(coerce_int(None, 5), 5)
        self.assertEqual(coerce_int(True, 9), 1)


class TestScreenResolution(unittest.TestCase):
    SCREENS = [
        {"name": "PRIMARY", "available": (1920, 1040)},
        {"name": "SECOND", "available": (1366, 728)},
    ]

    def test_known_screen_is_used(self):
        got = resolve_screen(self.SCREENS, "SECOND")
        self.assertEqual(got["name"], "SECOND")
        self.assertEqual(got["available"], (1366, 728))
        self.assertFalse(got["fell_back"])

    def test_missing_screen_falls_back_to_first_and_flags(self):
        got = resolve_screen(self.SCREENS, "UNPLUGGED")
        self.assertEqual(got["name"], "PRIMARY")
        self.assertTrue(got["fell_back"], "拔屏必须被标记，调用方要记 WARN")

    def test_empty_screen_list(self):
        got = resolve_screen([], "ANY")
        self.assertEqual(got["available"], (0, 0))
        self.assertFalse(got["fell_back"])

    def test_minimum_follows_screen(self):
        self.assertEqual(minimum_for_screen(self.SCREENS, "PRIMARY"), (1020, 560))
        # 小屏也至少给到 ABSOLUTE_MIN，不会返回 0 尺寸
        tiny = [{"name": "T", "available": (960, 500)}]
        w, h = minimum_for_screen(tiny, "T")
        self.assertGreaterEqual(w, 900)
        self.assertGreaterEqual(h, 480)

    def test_offscreen_platform_never_reports_touchscreen(self):
        """离屏后端会伪造一个 TouchScreen 设备 —— 不排除会让所有测试跑在触摸档。"""
        from PyQt6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        self.assertFalse(has_touchscreen())


class TestWideBreakpointHasRealBehaviour(unittest.TestCase):
    """L 档与 M 档必须有**可测量**差异，否则 `WIDE_WIDTH/HEIGHT` 又是死常量。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def _window(self):
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        store.save(s)
        return MainWindow(data_dir=tmp)

    def test_row_height_differs_between_L_and_M(self):
        from gcm.ui.palettes import TOKENS
        win = self._window()
        try:
            win.show()
            for _ in range(4):
                self._app.processEvents()
            win.resize(1440, 900)          # M
            win._apply_layout_mode()
            self._app.processEvents()
            m_h = win.progress_panel.table.verticalHeader().defaultSectionSize()
            win.resize(1600, 1000)         # L
            win._apply_layout_mode()
            self._app.processEvents()
            l_h = win.progress_panel.table.verticalHeader().defaultSectionSize()
            self.assertEqual(m_h, int(TOKENS["row"]["default"]))
            self.assertEqual(l_h, int(TOKENS["row"]["wide"]))
            self.assertGreater(l_h, m_h, "L 档必须真的更宽松，否则宽屏判定没有意义")
        finally:
            win.close()
            win.deleteLater()
            self._app.processEvents()


class TestGeometryRemembersScreen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def _window(self):
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        store.save(s)
        return MainWindow(data_dir=tmp)

    def test_screen_name_suffix_roundtrip(self):
        win = self._window()
        try:
            win.resize(1280, 800)
            win.move(40, 50)
            # 离屏后端的屏名是空串（实测 `QApplication.primaryScreen().name() == ""`），
            # 所以这里注入一个屏名来验证「有名字就写进几何串」这条契约本身。
            with mock.patch.object(win, "_current_screen_name",
                                   return_value="SCREEN-A"):
                raw = win._serialize_geometry()
            self.assertIn("1280", raw)
            self.assertIn("800", raw)
            self.assertTrue(raw.endswith("@SCREEN-A"),
                            f"几何串必须带屏名，实际 {raw!r}")
            win.settings.geometry = raw
            win._restore_geometry()
            self.assertEqual(win.width(), 1280)
            self.assertEqual(win.height(), 800)
        finally:
            win.close()
            win.deleteLater()
            self._app.processEvents()

    def test_unplugged_screen_falls_back_to_primary(self):
        """几何串里的屏已经不在了 → 回主屏居中，并留一条 WARN 日志。"""
        win = self._window()
        try:
            win.settings.geometry = "1280x800+40+50@A-SCREEN-THAT-IS-GONE"
            win._restore_geometry()
            self.assertGreaterEqual(win.width(), 1020)
            joined = win.log.to_plain_text()
            self.assertIn("不可用", joined, "拔屏必须留下可搜的 WARN 文案")
        finally:
            win.close()
            win.deleteLater()
            self._app.processEvents()

    def test_old_format_without_screen_still_parses(self):
        win = self._window()
        try:
            win.settings.geometry = "1100x700+20+30"     # 旧格式：没有 @屏名
            win._restore_geometry()
            self.assertGreaterEqual(win.width(), 1020)
        finally:
            win.close()
            win.deleteLater()
            self._app.processEvents()


if __name__ == "__main__":
    unittest.main()
