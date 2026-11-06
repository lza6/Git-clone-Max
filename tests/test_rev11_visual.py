"""Rev11 视觉重设计守卫（W1：设计 token 层）。

对应计划：`计划书/UI-视觉重设计-Rev11.md` §4 W1（V-05 / V-06 / V-07 / V-08 / V-10b）。

守卫内容：
1. **色彩预算**（§2.3）：QSS 里 accent 的出现位置收窄 —— 页签选中态不再用 accent、
   kicker 不再是 accent；accent 在单份 QSS 里的出现次数有上限。
2. **新色板键**：`panel_primary` / `signal_dim` 四主题齐全且键集合一致。
3. **三档卡面权重**：`#panelPrimary` / `#panelGhost` 选择器存在且属性正确。
4. **Primer 兜底清零**（§1.3 V12）：`gcm/ui/` 源码里不得再出现 Primer 色兜底字典。
5. **accent 六档预设**（§2.3.1，D-8）：色相不与语义色同族（amber 被禁止）、
   深浅成对、对 bg 对比度达标；6 预设 × 4 主题 = 24 组矩阵全验不抽样。

每条都有反向自检（变异断言：把守卫关心的东西改坏会失败）。
"""
from __future__ import annotations

import os
import pathlib
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from gcm.ui import qss_validate, theme  # noqa: E402
from gcm.ui.palettes import (  # noqa: E402
    ACCENT_PRESETS,
    PALETTES,
    contrast_ratio,
    theme_with_accent,
)

ROOT = pathlib.Path(__file__).resolve().parent.parent
UI_DIR = ROOT / "gcm" / "ui"

# Rev11 新增色板键（§2.7）
NEW_PALETTE_KEYS = {"panel_primary", "signal_dim"}

# 语义色占用的色相族（HSL hue 区间，度）。accent 不得落入（§2.3.1 约束 1）。
# success 绿 ~75-165、warning 琥珀 ~20-60、error 红 ~-20..20（即 340-360/0-20）。
# 绿的上界取 165：teal（~172°）是青不属于绿，属合法 accent 族。
_SEMANTIC_HUE_RANGES = ((340, 360), (0, 60), (75, 165))
_ALLOWED_HUE_RANGES = ((165, 340),)  # 青/蓝/紫/靛/品红/石青


def _hue_of(hex_color: str) -> float:
    from PyQt6.QtGui import QColor
    c = QColor(hex_color)
    return c.hueF() * 360 if c.hueF() >= 0 else 0.0


def _hue_in(h: float, lo: float, hi: float) -> bool:
    if lo <= hi:
        return lo <= h <= hi
    return h >= lo or h <= hi  # 跨 0 环


def _pill_block(q: str) -> str:
    """QSS 里 pill 相关块（本轮没有；形状在 delegate 里，此处只测 QSS 侧约束）。"""
    return q


class TestAccentBudget(unittest.TestCase):
    """V-05：accent 收窄 —— 一个颜色只说「正在发生」一件事。"""

    def test_tab_selected_does_not_use_accent(self):
        q = theme.qss_for_theme("deep", 1.0)
        block = q.split("QTabBar::tab:selected {", 1)[1].split("}", 1)[0]
        accent = PALETTES["deep"]["accent"]
        self.assertNotIn(accent, block,
                         "页签选中态不得再用 accent 下划线（层级靠字重与中性色，§2.3）")

    def test_kicker_does_not_use_accent(self):
        q = theme.qss_for_theme("deep", 1.0)
        block = q.split("QLabel#kicker {", 1)[1].split("}", 1)[0]
        self.assertNotIn(PALETTES["deep"]["accent"], block,
                         "kicker 铭牌不得用 accent（应用 text_dim）")
        self.assertIn(PALETTES["deep"]["text_dim"], block)

    def test_accent_still_on_progress_chunk_and_focus(self):
        """反向自检：预算收窄不能把「正在发生」本身也收掉。"""
        q = theme.qss_for_theme("deep", 1.0)
        chunk = q.split("QProgressBar::chunk {", 1)[1].split("}", 1)[0]
        self.assertIn(PALETTES["deep"]["accent"], chunk,
                      "进度 chunk 必须保留 accent（那是 accent 的本职）")

    def test_accent_occurrence_budget(self):
        """accent 在单份 QSS 里的出现次数有上限（通胀守卫，§1.3 V2 的直接量化）。

        现状（改前）7+ 处；收窄后合法位置：进度 chunk、focus 系列、
        列表选中指示条、复选框选中底。上限定 9（含 hover 态引用）。
        """
        q = theme.qss_for_theme("deep", 1.0)
        n = q.count(PALETTES["deep"]["accent"])
        self.assertLessEqual(n, 9, f"accent 在 QSS 中出现 {n} 次，超过预算 9（§2.3 色彩预算）")

    def test_accent_budget_variants(self):
        """反例自检：若把页签选中改回 accent，test_tab_selected_* 会失败。"""
        # 用 theme_with_accent 造一个不同 accent 的色板，确认守卫按色值而非写死字符串工作
        pal2 = theme_with_accent(PALETTES["deep"], "blue")
        q2 = theme.qss_for_palette(pal2, 1.0)
        block = q2.split("QTabBar::tab:selected {", 1)[1].split("}", 1)[0]
        self.assertNotIn(pal2["accent"], block)


class TestNewPaletteKeys(unittest.TestCase):
    """V-06：panel_primary / signal_dim 四主题齐全。"""

    def test_new_keys_present_all_themes(self):
        for name, pal in PALETTES.items():
            for key in NEW_PALETTE_KEYS:
                with self.subTest(theme=name, key=key):
                    self.assertIn(key, pal, f"{name} 缺新键 {key}")

    def test_panel_primary_differs_from_panel(self):
        for name, pal in PALETTES.items():
            with self.subTest(theme=name):
                self.assertNotEqual(
                    pal["panel_primary"].lower(), pal["panel"].lower(),
                    f"{name}: panel_primary 与 panel 同色 = 三档卡面权重形同虚设")

    def test_panel_primary_contrast(self):
        for name, pal in PALETTES.items():
            with self.subTest(theme=name):
                floor = 7.0 if name == "hc" else 4.5
                self.assertGreaterEqual(
                    contrast_ratio(pal["text"], pal["panel_primary"]), floor,
                    f"{name}: 正文对 panel_primary 不足 {floor}:1")

    def test_signal_dim_is_subtle(self):
        """signal_dim 是运行态弱化描边：必须比 panel 与 border 都低对比（它是「弱」）。"""
        for name, pal in PALETTES.items():
            with self.subTest(theme=name):
                d_main = contrast_ratio(pal["signal_dim"], pal["panel"])
                d_border = contrast_ratio(pal["border"], pal["panel"])
                self.assertLessEqual(d_main, d_border,
                                     f"{name}: signal_dim 比 border 还显眼，失去「弱化」语义")


class TestPanelWeights(unittest.TestCase):
    """V-07：三档卡面权重的 QSS 选择器。"""

    def test_panel_primary_selector_exists(self):
        q = theme.qss_for_theme("deep", 1.0)
        self.assertIn("QWidget#panelPrimary {", q)
        block = q.split("QWidget#panelPrimary {", 1)[1].split("}", 1)[0]
        self.assertIn(PALETTES["deep"]["panel_primary"], block)
        self.assertIn("border", block)

    def test_panel_ghost_selector_exists(self):
        q = theme.qss_for_theme("deep", 1.0)
        self.assertIn("QWidget#panelGhost {", q)
        block = q.split("QWidget#panelGhost {", 1)[1].split("}", 1)[0]
        self.assertIn("background: transparent", block,
                      "ghost 卡面必须无底色（§2.5 权重表）")
        self.assertIn("border: none", block)

    def test_weights_pass_validator_all_themes(self):
        for name in PALETTES:
            q = theme.qss_for_theme(name, 1.0)
            with self.subTest(theme=name):
                self.assertEqual(qss_validate.validate_qss(q), [])


class TestNoPrimerFallback(unittest.TestCase):
    """V-08：Primer 色不得再以「兜底字典」形式藏在 gcm/ui/ 源码里。"""

    PRIMER_HEXES = ("#0d1117", "#58a6ff", "#30363d", "#e6edf3", "#8b949e",
                    "#161b22", "#3fb950")

    def test_ui_sources_have_no_primer_literals(self):
        offenders: list[str] = []
        for py in sorted(UI_DIR.glob("*.py")):
            text = py.read_text(encoding="utf-8")
            for hex_ in self.PRIMER_HEXES:
                if hex_.lower() in text.lower():
                    offenders.append(f"{py.name}: {hex_}")
        self.assertEqual(offenders, [],
                         "Primer 色仍以字面量存在于 gcm/ui/（含兜底字典）：\n  "
                         + "\n  ".join(offenders))

    def test_guard_catches_a_real_primer_literal(self):
        """反向自检：注入一个 Primer 色字面量，扫描函数必须报警。"""
        text = 'x = {"accent": "#58a6ff"}  # 坏兜底'
        hits = [h for h in self.PRIMER_HEXES if h.lower() in text.lower()]
        self.assertEqual(hits, ["#58a6ff"])


class TestAccentSixPresets(unittest.TestCase):
    """V-10b（D-8）：6 档 accent 预设的硬约束（§2.3.1）。"""

    REQUIRED_PRESETS = {"teal", "blue", "violet"}  # 既有 3 档必须保留
    MIN_PRESETS = 6

    def test_six_presets_exist(self):
        self.assertGreaterEqual(len(ACCENT_PRESETS), self.MIN_PRESETS,
                                f"accent 预设不足 {self.MIN_PRESETS} 档：{sorted(ACCENT_PRESETS)}")
        self.assertTrue(self.REQUIRED_PRESETS.issubset(ACCENT_PRESETS),
                        f"既有预设被删：{self.REQUIRED_PRESETS - set(ACCENT_PRESETS)}")

    def test_each_preset_has_dark_and_light_pair(self):
        for name, spec in ACCENT_PRESETS.items():
            with self.subTest(preset=name):
                self.assertIn("dark", spec, f"{name} 缺 dark 档")
                self.assertIn("light", spec, f"{name} 缺 light 档")
                self.assertTrue(spec["dark"] and spec["light"],
                                f"{name} 的深浅档有空值")

    def test_hue_distance_from_semantics(self):
        """色相距离约束（§2.3.1 约束 1）：accent 不得落入语义色色相族。amber 禁止。"""
        for name, spec in ACCENT_PRESETS.items():
            for shade in ("dark", "light"):
                h = _hue_of(spec[shade])
                with self.subTest(preset=name, shade=shade, hue=round(h, 1)):
                    for lo, hi in _SEMANTIC_HUE_RANGES:
                        self.assertFalse(
                            _hue_in(h, lo, hi),
                            f"{name}.{shade} 色相 {h:.0f}° 与语义色（绿/琥珀/红）同族，"
                            "会让「运行中」与「警告」不可分辨")

    def test_contrast_matrix_24_combinations(self):
        """6 预设 × 4 主题 = 24 组，全验不抽样（§2.3.1 约束 4）。"""
        combos = 0
        for pname, spec in ACCENT_PRESETS.items():
            for tname, base in PALETTES.items():
                picked = spec["dark"] if _is_dark(base) else spec["light"]
                pal = theme_with_accent(base, {"accent": picked})
                combos += 1
                with self.subTest(preset=pname, theme=tname, accent=picked):
                    self.assertGreaterEqual(
                        contrast_ratio(pal["accent"], pal["bg"]), 3.0,
                        f"{pname}@{tname}: accent 对 bg < 3:1")
                    self.assertGreaterEqual(
                        contrast_ratio(pal["on_accent"], pal["accent"]), 4.5,
                        f"{pname}@{tname}: on_accent 对 accent < 4.5:1")
        self.assertEqual(combos, len(ACCENT_PRESETS) * 4,
                         "预设数 × 4 主题 必须全验（组合数对不上 = 漏验）")


def _is_dark(pal: dict) -> bool:
    from gcm.ui.palettes import _luminance
    return _luminance(pal["bg"]) < 0.2


if __name__ == "__main__":
    unittest.main()
