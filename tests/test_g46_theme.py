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
        required = {"bg", "panel", "panel2", "border", "border_strong", "accent",
                    "accent2", "on_accent", "success", "warning", "error",
                    "text", "text_dim", "console", "console_fg"}
        key_set = None
        for name, pal in PALETTES.items():
            self.assertTrue(required.issubset(set(pal)), f"主题 {name} 缺语义色键")
            if key_set is None:
                key_set = set(pal.keys())
            else:
                self.assertEqual(set(pal.keys()), key_set,
                                 f"主题 {name} 色键与其它主题不一致")


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
        from gcm.ui.theme import PALETTES, qss_for_theme
        q = qss_for_theme("deep")
        # G75：不再硬编码历史色值（#58a6ff 是 GitHub Primer 的蓝，已随 Ink 色板退场）；
        # 口径改为"焦点环必须用当前色板的 accent"，比钉死某个十六进制更强。
        self.assertIn(f"border: 2px solid {PALETTES['deep']['accent']};", q,
                      "focus 环未用 accent 色")

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
        from gcm.ui.theme import ACCENT_PRESETS, PALETTES, contrast_ratio, theme_with_accent
        self.assertEqual(set(ACCENT_PRESETS), {"teal", "blue", "violet"})
        deep = PALETTES["deep"]
        # G75：预设改成"深底/浅底成对"——同一十六进制不可能同时满足深底够亮、浅底够暗
        for name, spec in ACCENT_PRESETS.items():
            with self.subTest(preset=name):
                self.assertEqual(set(spec), {"dark", "light"})
                pal_dark = theme_with_accent(PALETTES["deep"], name)
                pal_light = theme_with_accent(PALETTES["light"], name)
                self.assertEqual(pal_dark["accent"], spec["dark"])
                self.assertEqual(pal_light["accent"], spec["light"])
                # on_accent 必须重算到 ≥4.5:1（G70 修过的缺陷，不能被预设绕过）
                for pal in (pal_dark, pal_light):
                    self.assertGreaterEqual(
                        contrast_ratio(pal["on_accent"], pal["accent"]), 4.5)
        # 直接传 dict 的路径保持可用（自定义配色 / 测试）
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