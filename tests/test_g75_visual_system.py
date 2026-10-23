"""G75 视觉系统守卫：字体阶梯 / 色板对比度 / 层级 / 状态形状 / 仪表齿条。

**为什么需要这组守卫**（不是"再写几个测试"）：
1. 本轮把默认主题从 GitHub Primer（`#0d1117`/`#58a6ff`）换成自建的 Ink/Paper，
   如果没有守卫，下一次"顺手改个色"就会悄悄退回去；
2. 四个主题 × WCAG 是既有承诺（`hc` 给低视力用户），改色必须逐主题验算对比度；
3. 状态从"纯文字着色"改成"形状编码"（G74 曾因字形缺失退回文字），
   形状必须互不相同，否则色盲用户仍然读不出差别；
4. 仪表齿条的读数口径（空态 `—`、用时格式化）是纯逻辑，必须与 UI 解耦后单测。

口径说明：字体**存在性**断言按平台条件化（CI 在 ubuntu/macos 没有 Bahnschrift /
Cascadia Mono），只断言 QSS 里配置的字族链 —— 见 `TestFontFamilies`。
"""
from __future__ import annotations

import os
import pathlib
import re
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from gcm.ui import qss_validate, theme  # noqa: E402
from gcm.ui.palettes import PALETTES, TOKENS  # noqa: E402
from gcm.ui.status_pill import (  # noqa: E402
    SHAPES,
    StatusPillDelegate,
    color_for,
    draw_shape,
    shape_for,
)
from gcm.ui.status_strip import (  # noqa: E402
    PLACEHOLDER,
    StatusStrip,
    format_count,
    format_elapsed,
    format_metrics,
)
from gcm.ui.typography import (  # noqa: E402
    FONT_BASE_PX,
    FONT_STACK,
    FONT_STACK_DISPLAY,
    FONT_STACK_MONO,
    ROLE_ORDER,
    TYPE_SCALE,
    apply_type,
    role_stack,
    type_px,
)

REQUIRED_PALETTE_KEYS = {
    "bg", "panel", "panel2", "border", "border_strong", "accent", "accent2",
    "on_accent", "cta", "on_cta", "glow", "success", "warning", "error",
    "text", "text_dim", "console", "console_fg",
}


class TestTypeScale(unittest.TestCase):
    """A1：七级字号阶梯真实存在且严格递减（不是"假装有层级"）。"""

    def test_seven_roles_present(self):
        self.assertEqual(set(TYPE_SCALE), set(ROLE_ORDER))
        self.assertEqual(len(ROLE_ORDER), 7)

    def test_scale_is_strictly_descending(self):
        sizes = [TYPE_SCALE[r]["px"] for r in ROLE_ORDER]
        self.assertEqual(sizes, sorted(sizes, reverse=True),
                         f"字号阶梯必须递减，实际 {sizes}")
        self.assertEqual(len(set(sizes)), len(sizes),
                         f"字号必须互不相同，否则不是阶梯：{sizes}")

    def test_base_font_px_is_unchanged(self):
        # 基体 13px 是历史口径（既有断言 font-size: round(13*scale) 依赖它）
        self.assertEqual(FONT_BASE_PX, 13)
        self.assertEqual(TYPE_SCALE["body"]["px"], FONT_BASE_PX)

    def test_type_px_clamps_scale(self):
        self.assertEqual(type_px("body", 0.1), round(FONT_BASE_PX * 0.8))
        self.assertEqual(type_px("body", 9.0), round(FONT_BASE_PX * 1.6))

    def test_qss_emits_all_scale_levels(self):
        q = theme.qss_for_theme("deep", 1.0)
        for role in ("display", "h1", "h2", "metric", "mono", "small"):
            px = type_px(role, 1.0)
            self.assertIn(f"font-size: {px}px;", q, f"QSS 缺少 {role} 级字号")

    def test_apply_type_sets_pixel_size_and_spacing(self):
        from PyQt6.QtWidgets import QLabel
        lbl = QLabel("x")
        apply_type(lbl, "h1", 1.0)
        self.assertEqual(lbl.font().pixelSize(), TYPE_SCALE["h1"]["px"])
        # 字距是 QSS 做不到的部分，只能靠 QFont —— 必须真的设上了
        self.assertNotEqual(lbl.font().letterSpacing(), 0.0)

    def test_apply_type_tolerates_non_widget(self):
        # 非 QWidget（无 setFont）不得抛异常
        apply_type(object(), "body", 1.0)


class TestFontFamilies(unittest.TestCase):
    """A2：三字族链正确（Windows 缺失时按栈回退）。"""

    def test_three_stacks_are_distinct(self):
        self.assertEqual(len({FONT_STACK, FONT_STACK_DISPLAY, FONT_STACK_MONO}), 3)
        self.assertEqual(role_stack("body"), FONT_STACK)
        self.assertEqual(role_stack("h1"), FONT_STACK_DISPLAY)
        self.assertEqual(role_stack("mono"), FONT_STACK_MONO)

    def test_stacks_have_fallbacks(self):
        self.assertIn("Bahnschrift", FONT_STACK_DISPLAY)
        self.assertTrue(FONT_STACK_DISPLAY.strip().endswith("sans-serif"))
        self.assertIn("Cascadia Mono", FONT_STACK_MONO)
        self.assertTrue(FONT_STACK_MONO.strip().endswith("monospace"))
        # 中文必须始终有回退（Bahnschrift/Cascadia 都没有中文字形）
        for stack in (FONT_STACK_DISPLAY, FONT_STACK_MONO):
            self.assertTrue("YaHei" in stack or "PingFang" in stack, stack)

    def test_mono_stack_used_by_console_and_pane(self):
        q = theme.qss_for_theme("deep", 1.0)
        for sel in ("QTextEdit#console", "QTextEdit#pane", "QLabel#monoLabel"):
            block = q.split(sel, 1)[1].split("}", 1)[0]
            self.assertIn("Cascadia Mono", block, f"{sel} 未使用等宽字族")

    def test_family_existence_is_platform_conditional(self):
        from PyQt6.QtGui import QFontDatabase
        families = set(QFontDatabase.families())
        wanted = ("Bahnschrift", "Cascadia Mono", "Microsoft YaHei UI")
        present = [f for f in wanted if f in families]
        if not present:
            self.skipTest("本机/CI 无目标字族（Linux/macOS 常态）：只校验 QSS 字族链")
        for family in present:
            self.assertTrue(any(family in s for s in
                                (FONT_STACK, FONT_STACK_DISPLAY, FONT_STACK_MONO)))


class TestPaletteContrast(unittest.TestCase):
    """A3：四主题语义色齐全 + 逐对对比度达标（hc 走 AAA）。"""

    def test_palettes_share_identical_keys(self):
        for name, pal in PALETTES.items():
            self.assertTrue(REQUIRED_PALETTE_KEYS.issubset(set(pal)),
                            f"{name} 缺键：{REQUIRED_PALETTE_KEYS - set(pal)}")
            self.assertEqual(set(pal), set(PALETTES["deep"]),
                             f"{name} 与 deep 色键不一致")

    def test_text_contrast(self):
        for name, pal in PALETTES.items():
            with self.subTest(theme=name):
                floor = 7.0 if name == "hc" else 4.5
                self.assertGreaterEqual(
                    theme.contrast_ratio(pal["text"], pal["bg"]), floor,
                    f"{name}: 正文对比度低于 {floor}:1")

    def test_dim_text_contrast(self):
        for name, pal in PALETTES.items():
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    theme.contrast_ratio(pal["text_dim"], pal["panel"]), 4.5,
                    f"{name}: 次要文本对比度不足 4.5:1")

    def test_semantic_colors_on_control_surface(self):
        for name, pal in PALETTES.items():
            for key in ("success", "warning", "error"):
                with self.subTest(theme=name, key=key):
                    self.assertGreaterEqual(
                        theme.contrast_ratio(pal[key], pal["panel2"]), 4.5,
                        f"{name}: {key} 对控件底色不足 4.5:1")

    def test_border_strong_non_text(self):
        for name, pal in PALETTES.items():
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    theme.contrast_ratio(pal["border_strong"], pal["panel2"]), 3.0,
                    f"{name}: 输入框边界不足 3:1")

    def test_cta_block_contrast(self):
        for name, pal in PALETTES.items():
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    theme.contrast_ratio(pal["on_cta"], pal["cta"]), 4.5,
                    f"{name}: 主按钮文字对反相块不足 4.5:1")

    def test_accent_visible_on_background(self):
        for name, pal in PALETTES.items():
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    theme.contrast_ratio(pal["accent"], pal["bg"]), 3.0,
                    f"{name}: 焦点环/进度色在背景上看不清（<3:1）")


class TestAntiGenericAesthetics(unittest.TestCase):
    """A4：反 AI 通用美学的硬守卫 —— 不许悄悄退回 GitHub Primer。"""

    def test_deep_is_not_github_primer(self):
        deep = PALETTES["deep"]
        self.assertNotEqual(deep["bg"].lower(), "#0d1117")
        self.assertNotEqual(deep["accent"].lower(), "#58a6ff")
        self.assertNotEqual(deep["success"].lower(), "#3fb950")

    def test_themes_are_visibly_different(self):
        bgs = {name: pal["bg"].lower() for name, pal in PALETTES.items()}
        self.assertEqual(len(set(bgs.values())), 4, f"四套主题底色重复：{bgs}")

    def test_primary_button_is_inverse_block_not_accent(self):
        q = theme.qss_for_theme("deep", 1.0)
        block = q.split("QPushButton#primary {", 1)[1].split("}", 1)[0]
        pal = PALETTES["deep"]
        self.assertIn(pal["cta"], block, "主按钮必须是反相块")
        self.assertNotIn(f"background: {pal['accent']}", block,
                         "主按钮不得再用强调色填充（那是通用模板的做法）")


class TestQssLayers(unittest.TestCase):
    """A5：层级/底色/状态选择器 + 校验器全绿 + reduced-motion 真生效。"""

    def test_root_gradient_exists(self):
        q = theme.qss_for_theme("deep", 1.0)
        self.assertIn("QWidget#rootBg", q)
        self.assertIn("qlineargradient", q)

    def test_groupbox_has_inner_highlight(self):
        q = theme.qss_for_theme("deep", 1.0)
        block = q.split("QGroupBox {", 1)[1].split("}", 1)[0]
        self.assertIn("border-top", block, "卡面缺少内嵌高光（Qt 没有 box-shadow）")
        self.assertIn(PALETTES["deep"]["glow"], block)

    def test_tab_selected_uses_accent(self):
        q = theme.qss_for_theme("deep", 1.0)
        block = q.split("QTabBar::tab:selected {", 1)[1].split("}", 1)[0]
        self.assertIn(PALETTES["deep"]["accent"], block)
        self.assertIn("font-weight: 700", block)

    def test_default_button_is_ghost(self):
        q = theme.qss_for_theme("deep", 1.0)
        block = q.split("QPushButton {", 1)[1].split("}", 1)[0]
        self.assertIn("background: transparent", block,
                      "次级按钮应为幽灵样式（层级靠主按钮立起来）")

    def test_reduced_motion_removes_hover_blocks(self):
        on = theme.qss_for_scale(1.0, motion=True)
        off = theme.qss_for_scale(1.0, motion=False)
        self.assertIn("QPushButton:hover", on)
        self.assertNotIn("QPushButton:hover", off)
        self.assertNotIn("QSplitter::handle:hover", off)

    def test_every_theme_and_scale_passes_validator(self):
        for name in PALETTES:
            for scale in (0.8, 1.0, 1.5):
                for motion in (True, False):
                    with self.subTest(theme=name, scale=scale, motion=motion):
                        errs = qss_validate.validate_qss(
                            theme.qss_for_theme(name, scale, motion))
                        self.assertEqual(errs, [])

    def test_row_tokens_exist(self):
        self.assertIn("row", TOKENS)
        self.assertGreaterEqual(TOKENS["row"]["default"], 24)


class TestStatusShapeEncoding(unittest.TestCase):
    """A6：形状编码互不相同（色盲可辨），且与字体字形无关。"""

    STATUSES = ("success", "failed", "conflict", "skipped", "cancelled",
                "running", "pending")

    def test_each_status_has_distinct_shape(self):
        shapes = [shape_for(s) for s in self.STATUSES]
        self.assertEqual(len(set(shapes)), len(shapes),
                         f"形状必须互不相同，实际 {dict(zip(self.STATUSES, shapes, strict=True))}")
        for shape in shapes:
            self.assertIn(shape, SHAPES)

    def test_unknown_status_falls_back(self):
        self.assertEqual(shape_for("nope"), "hollow_square")
        self.assertEqual(shape_for(""), "hollow_square")

    def test_color_comes_from_palette(self):
        pal = PALETTES["deep"]
        self.assertEqual(color_for("success", pal), pal["success"])
        self.assertEqual(color_for("failed", pal), pal["error"])
        self.assertEqual(color_for("conflict", pal), pal["warning"])
        self.assertEqual(color_for("running", pal), pal["accent"])

    def test_draw_shape_actually_paints(self):
        from PyQt6.QtGui import QColor, QPainter, QPixmap
        for shape in SHAPES:
            with self.subTest(shape=shape):
                pm = QPixmap(16, 16)
                pm.fill(QColor(0, 0, 0, 0))
                p = QPainter(pm)
                draw_shape(p, pm.rect(), shape, QColor("#E8E3DA"))
                p.end()
                img = pm.toImage()
                painted = sum(
                    1 for x in range(16) for y in range(16)
                    if img.pixelColor(x, y).alpha() > 0)
                self.assertGreater(painted, 4, f"{shape} 没有画出任何像素")


class TestStatusStripReadouts(unittest.TestCase):
    """A7：仪表读数口径（空态 `—`、用时格式）+ 计时器生命周期。"""

    def test_format_elapsed(self):
        self.assertEqual(format_elapsed(None), PLACEHOLDER)
        self.assertEqual(format_elapsed(0), "00:00")
        self.assertEqual(format_elapsed(59), "00:59")
        self.assertEqual(format_elapsed(61), "01:01")
        self.assertEqual(format_elapsed(3661), "1:01:01")
        self.assertEqual(format_elapsed(-5), "00:00")

    def test_format_count(self):
        self.assertEqual(format_count(None), PLACEHOLDER)
        self.assertEqual(format_count(0), "0")
        self.assertEqual(format_count(210), "210")

    def test_format_metrics_empty_state(self):
        text = format_metrics(concurrency=8)
        for part in ("并行线程 8", "活跃 —", "成功 —", "失败 —", "用时 —"):
            self.assertIn(part, text)

    def test_widget_lifecycle(self):
        strip = StatusStrip(concurrency=4)
        try:
            # 空态：全是 —，且不跑定时器（空闲不浪费唤醒）
            self.assertFalse(strip.is_running())
            self.assertEqual(strip.active_lbl.text(), "活跃 —")
            self.assertEqual(strip.elapsed_lbl.text(), "用时 —")
            strip.begin(3)
            self.assertTrue(strip.is_running())
            self.assertEqual(strip.success_lbl.text(), "成功 0")
            strip.note_active(2)
            self.assertEqual(strip.active_lbl.text(), "活跃 2")
            strip.note_result("success")
            strip.note_result("failed")
            self.assertEqual(strip.success_lbl.text(), "成功 1")
            self.assertEqual(strip.failed_lbl.text(), "失败 1")
            # 失败>0 时用错误色（二次编码；文本本身已写"失败"）
            self.assertIn(PALETTES["deep"]["error"],
                          strip.failed_lbl.styleSheet().replace("#", "#"))
            strip.end()
            self.assertFalse(strip.is_running())
            frozen = strip.elapsed_seconds()
            self.assertIsNotNone(frozen)
            strip.reset()
            self.assertEqual(strip.success_lbl.text(), "成功 —")
            self.assertIsNone(strip.elapsed_seconds())
        finally:
            strip.deleteLater()

    def test_elapsed_uses_injected_clock(self):
        now = [1000.0]
        strip = StatusStrip(concurrency=2, clock=lambda: now[0])
        try:
            strip.begin(1)
            now[0] += 65.0
            strip._tick()
            self.assertEqual(strip.elapsed_lbl.text(), "用时 01:05")
            strip.end()
            now[0] += 999.0
            self.assertEqual(strip.elapsed_seconds(), 65.0, "结束后用时必须冻结")
        finally:
            strip.deleteLater()

    def test_concurrency_setting_updates_label(self):
        strip = StatusStrip(concurrency=8)
        try:
            strip.set_concurrency(16)
            self.assertEqual(strip.thread_lbl.text(), "并行线程 16")
        finally:
            strip.deleteLater()


class TestMainWindowVisualWiring(unittest.TestCase):
    """A8：接线闭环 —— 齿条在窗口里、pill delegate 挂上了、导航去代号。"""

    @classmethod
    def setUpClass(cls):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        cls._tmp = tempfile.mkdtemp(prefix="gcm_g75_")
        d = pathlib.Path(cls._tmp)
        cls.win = MainWindow(data_dir=d, db=Database(d / "t.db"),
                             settings=SettingsStore(d / "s.json"))
        _app.processEvents()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.win.close()
            cls.win.deleteLater()
        except Exception:
            pass

    def test_status_strip_is_wired(self):
        self.assertTrue(hasattr(self.win, "status_strip"))
        self.assertIs(self.win.thread_lbl, self.win.status_strip.thread_lbl,
                      "thread_lbl 必须指向齿条内的标签（settings_panel 会直接改它）")
        # 加进 central 的布局后 parent 会变成 central（Qt 的 reparent 行为）
        self.assertTrue(self.win.centralWidget().isAncestorOf(
            self.win.status_strip), "齿条必须挂在主窗中央控件里")
        self.assertEqual(self.win.centralWidget().objectName(), "rootBg")

    def test_status_pill_delegate_registered(self):
        table = self.win.progress_panel.table
        self.assertIsInstance(table.itemDelegateForColumn(2), StatusPillDelegate)
        self.assertGreaterEqual(table.verticalHeader().defaultSectionSize(),
                                TOKENS["row"]["default"])

    def test_status_text_stays_plain(self):
        """pill 只负责绘制：单元格文本不得出现字形符号（G74 的 tofu 教训）。"""
        from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus
        panel = self.win.progress_panel
        spec = RepoSpec(owner="a", repo="b",
                        url_https="https://github.com/a/b.git", folder_name="a__b")
        panel._prepare_table(1)
        panel._add_table_row(0, spec)
        panel._on_worker_result(0, SyncResult(
            spec=spec, status=SyncStatus.SUCCESS, action=SyncAction.CLONED,
            message="克隆完成"))
        text = panel.table.item(0, 2).text()
        self.assertIn("成功", text)
        for bad in ("✓", "✕", "⚠", "⊘", "●", "◧"):
            self.assertNotIn(bad, text)

    def test_settings_nav_is_numbered_without_codenames(self):
        nav = self.win.settings_nav
        self.assertGreaterEqual(nav.count(), 3)
        for i in range(nav.count()):
            text = nav.item(i).text()
            self.assertRegex(text, r"^\d{2}  \S", f"导航项缺编号：{text!r}")
            self.assertNotIn("（", text)
            self.assertIsNone(re.search(r"G\d+-?\d*", text),
                              f"导航项暴露内部代号：{text!r}")

    def test_theme_switch_keeps_strip_colors_in_sync(self):
        self.win.settings.theme = "light"
        self.win.setStyleSheet(self.win._themed_qss())
        for label in (self.win.status_strip.active_lbl,
                      self.win.status_strip.success_lbl):
            self.assertEqual(label.objectName(), "monoLabel")
        self.win.settings.theme = "deep"
        self.win.setStyleSheet(self.win._themed_qss())


if __name__ == "__main__":
    unittest.main(verbosity=2)
