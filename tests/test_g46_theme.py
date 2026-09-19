# -*- coding: utf-8 -*-
"""G46 主题设计系统测试：token 层 / 状态 QSS / HC 对比度 / 强调色 / reduced-motion / 字体栈。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestDesignTokens(unittest.TestCase):
    """G46-1：token 表完整且三主题共用布局键。"""

    def test_tokens_present(self):
        from gcm.ui.theme import TOKENS
        for group in ("radius", "spacing", "border", "shadow"):
            self.assertIn(group, TOKENS, f"token 组缺失 {group}")

    def test_all_palettes_share_layout(self):
        from gcm.ui.theme import PALETTES
        keys = {"bg", "panel", "panel2", "border", "accent", "accent2",
                "warning", "error", "text", "text_dim", "console", "console_fg"}
        for name, pal in PALETTES.items():
            self.assertEqual(set(pal.keys()), keys, f"主题 {name} 色键不一致")


class TestStateQss(unittest.TestCase):
    """G46-3：控件状态选择器存在；G46-6：focus 环；G46-5：字体栈。"""

    def _qss(self):
        from gcm.ui.theme import qss_for_theme
        return qss_for_theme("deep")

    def test_hover_focus_disabled_pressed_selectors(self):
        q = self._qss()
        for sel in ("QPushButton:hover", "QPushButton:focus", "QPushButton:disabled",
                    "QPushButton:pressed", "QLineEdit:focus", "QSpinBox:focus",
                    "QComboBox:focus", "QComboBox:disabled"):
            self.assertIn(sel, q, f"缺少状态选择器 {sel}")

    def test_focus_border_uses_accent(self):
        from gcm.ui.theme import qss_for_theme
        q = qss_for_theme("deep")
        self.assertIn("border: 2px solid #58a6ff;", q, "focus 环未用 accent 色")

    def test_font_stack(self):
        from gcm.ui.theme import qss_for_theme, FONT_STACK
        q = qss_for_theme("deep")
        self.assertIn("Microsoft YaHei UI", q)
        self.assertIn("PingFang SC", q)
        self.assertIn("Noto Sans CJK SC", q)
        self.assertIn(FONT_STACK.split(",")[0].strip('"'), q)


class TestHcTheme(unittest.TestCase):
    """G46-7：HC 主题存在且对比度 ≥7:1（WCAG AAA）。"""

    def test_hc_palette_exists(self):
        from gcm.ui.theme import PALETTES, THEMES
        self.assertIn("hc", PALETTES)
        self.assertIn("hc", THEMES)

    def test_hc_contrast_aaa(self):
        from gcm.ui.theme import hc_contrast_ratio, PALETTES
        self.assertGreaterEqual(hc_contrast_ratio(PALETTES["hc"]), 7.0,
                                "HC 文本/背景对比度应 ≥7:1")

    def test_accent_presets(self):
        from gcm.ui.theme import ACCENT_PRESETS, theme_with_accent
        self.assertEqual(len(ACCENT_PRESETS), 3)
        deep = __import__("gcm.ui.theme", fromlist=["PALETTES"]).PALETTES["deep"]
        pal = theme_with_accent(deep, "violet")
        self.assertEqual(pal["accent"], ACCENT_PRESETS["violet"]["accent"])
        # 直接传 dict 也可
        pal2 = theme_with_accent(deep, {"accent": "#123456"})
        self.assertEqual(pal2["accent"], "#123456")


class TestReducedMotion(unittest.TestCase):
    """G46-8：motion=False 时 QSS 去掉 hover/pressed 过渡样式。"""

    def test_motion_off_differs(self):
        from gcm.ui.theme import qss_for_scale
        on = qss_for_scale(1.0, motion=True)
        off = qss_for_scale(1.0, motion=False)
        self.assertNotEqual(on, off)
        self.assertIn("QPushButton:hover", on)
        self.assertNotIn("QPushButton:hover", off)


class TestFontScaleFloor(unittest.TestCase):
    """G46-5：UI 层字号下限常量 0.9（底层 qss_for_scale 保持 [0.8,1.6] 兼容）。"""

    def test_ui_min_constant(self):
        from gcm.ui.theme import FONT_SCALE_MIN_UI
        self.assertEqual(FONT_SCALE_MIN_UI, 0.9)

    def test_theme_qss_scale(self):
        from gcm.ui.theme import qss_for_theme, FONT_BASE_PX
        q = qss_for_theme("deep", 1.5)
        self.assertIn(f"font-size: {round(FONT_BASE_PX * 1.5)}px;", q)


if __name__ == "__main__":
    unittest.main(verbosity=2)