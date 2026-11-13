"""Rev13 R13-01/02/03 守卫：矮窗口的工具行折叠 + 字号下限单一来源 + 窄窗列折叠。

**这三条防的都是「已经真实发生过」的事，不是假想**：

1. `test_ui_compact_visibility` 在 1020×560 下报 `btn_open` 只剩 27/42px ——
   根因是新增的菜单栏（18px）把顶部区挤掉了。修法有两层：窗口最小高度已扣菜单栏
   （Rev12 U-10），以及本文件的折叠兜底（900×480 / 911×512 这类真放不下的屏幕）。
2. `typography.FONT_SCALE_MIN_UI = 0.9` 声明了「UI 层字号下限」但**全库零强制**，
   `Ctrl+-` 可直达 0.8 —— 两套口径并存。现在 `zoom_out()` 用同一个常量。
3. `_apply_layout_for_width` 用了 `QTableView.horizontalHeaderItem()`（不存在），
   每次都抛 `AttributeError` 被吞 —— 窄窗折叠从来没生效过。真实模型回归在
   `test_g57_responsive.TestNarrowFoldOnRealModel`，本文件只测折叠态的可达性。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

#: 折叠后仍必须**完整可见**的常驻控件（主操作 + 清单行）。
_ALWAYS_VISIBLE = (
    "btn_start", "btn_cancel", "btn_clear", "btn_pause_batch",
    "btn_save_list", "btn_load_list", "btn_star_import", "combo_load_list",
)
#: 折叠时被收进「更多 ▸」的四个工具入口。
_FOLDABLE = ("btn_open", "btn_failures", "btn_retry_queue", "btn_diag_bundle")


class TestToolsFold(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        # 离屏默认屏是 800×800 → `effective_minimum()` 会把最小高度夹到 584，
        # `resize(900, 480)` 根本到不了 480，折叠条件永远不成立（假绿）。
        # 这里把「屏幕工作区」固定成真机场景 1366×768@150% 的 911×512，
        # 让最小窗落到 (900, 504) —— 这才是折叠真正要覆盖的那类机器。
        self._orig_avail = MainWindow._available_screen_size
        MainWindow._available_screen_size = lambda self: (911, 512)
        self._tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(self._tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        store.save(s)
        self.win = MainWindow(data_dir=self._tmp)
        self.win.show()

    def tearDown(self):
        from gcm.ui.main_window import MainWindow
        MainWindow._available_screen_size = self._orig_avail
        try:
            self.win.close()
            self.win.deleteLater()
        except Exception:
            pass

    def _unclipped(self, names) -> list[str]:
        bad = []
        for n in names:
            w = getattr(self.win, n, None)
            if w is None:
                bad.append(f"{n}(缺失)")
                continue
            if not w.isVisible():
                bad.append(f"{n}(不可见)")
                continue
            vr = w.visibleRegion().boundingRect()
            if vr.size() != w.rect().size():
                bad.append(f"{n}({vr.width()}x{vr.height()} of {w.width()}x{w.height()})")
        return bad

    def test_fold_height_threshold_is_monotonic_in_scale(self):
        """字号越大，折叠阈值越高（大字需要更多高度）。"""
        from gcm.ui.layout import tools_fold_height
        self.assertLess(tools_fold_height(1.0), tools_fold_height(1.3))
        self.assertLess(tools_fold_height(1.3), tools_fold_height(1.6))
        # 实测首次不裁切的高度。Rev13 首测 580/600/620；
        # Rev14 F1 重测 720/740/760（下载位置/克隆模式常驻 +70）；
        # **Rev16 B3 重测 790/810/830**（快捷填充行常驻再 +42）。
        #         # 扫描方法：关掉折叠后逐 10px 找「首个零裁切高度」。允许 ±20 取整余量。
        self.assertAlmostEqual(tools_fold_height(1.0), 790, delta=20)
        self.assertAlmostEqual(tools_fold_height(1.6), 830, delta=20)

    def test_short_window_folds_and_keeps_entries_reachable(self):
        """900×480（真机 911×512@150% 的最小窗）必须折叠，且四个入口仍可点。"""
        from PyQt6.QtTest import QTest
        self.win.resize(900, 480)
        QTest.qWait(600)

        self.assertTrue(getattr(self.win, "_tools_folded", False),
                        "极矮窗口必须折叠工具行（否则四个按钮被裁）")
        more = self.win.btn_more_tools
        self.assertTrue(more.isVisible(), "折叠后「更多 ▸」必须可见")
        vr = more.visibleRegion().boundingRect()
        # 1px 舍入容差（不同 DPI 下 QSS padding 的取整差异）
        self.assertGreaterEqual(vr.height(), more.rect().height() - 1,
                                "「更多 ▸」被竖向裁切")
        self.assertLessEqual(vr.width(), more.rect().width(),
                             "「更多 ▸」横向越界")

        menu = more.menu()
        self.assertIsNotNone(menu, "折叠后必须有菜单")
        texts = [a.text() for a in menu.actions()]
        for name in ("打开克隆目录", "失败汇总", "重试队列", "诊断包"):
            self.assertIn(name, texts, f"菜单里缺少入口：{name}")
        for a in menu.actions():
            self.assertTrue(a.isEnabled(), f"菜单项不可用：{a.text()}")

        for name in _FOLDABLE:
            self.assertFalse(getattr(self.win, name).isVisible(),
                             f"折叠态下 {name} 应被收起（否则仍占高度）")
        self.assertEqual(self._unclipped(_ALWAYS_VISIBLE), [],
                         "折叠后常驻控件仍必须完整可见")

    def test_tall_window_unfolds_everything(self):
        """高窗口必须恢复成两行（折叠不是常驻状态）。"""
        from PyQt6.QtTest import QTest
        self.win.resize(1440, 900)
        QTest.qWait(600)
        self.assertFalse(getattr(self.win, "_tools_folded", True),
                         "标准窗口不应折叠")
        self.assertTrue(self.win.btn_more_tools.isHidden(),
                        "展开态下「更多 ▸」必须隐藏")
        self.assertEqual(self._unclipped(_FOLDABLE + _ALWAYS_VISIBLE), [],
                         "展开态下两行都必须完整可见")

    def test_fold_to_unfold_roundtrip_is_stable(self):
        """反复折/展不得累积高度误差（折叠会改 minimumHeight）。"""
        from PyQt6.QtTest import QTest
        for _ in range(3):
            self.win.resize(900, 480)
            QTest.qWait(700)
            self.assertEqual(self._unclipped(_ALWAYS_VISIBLE), [],
                             "折叠态下常驻控件被裁")
            self.win.resize(1440, 900)
            QTest.qWait(700)
            self.assertEqual(self._unclipped(_FOLDABLE), [],
                             "展开态下工具行被裁（折叠改了 minimumHeight 没还原？）")

    def test_font_scale_change_reevaluates_fold(self):
        """字号调大 → 需求高度变高 → 同一窗口高度下必须转入折叠态。

        **真事故**：`_apply_font_scale()` 原先只重刷 QSS、不重算布局，
        折叠阈值依赖字号却没人重算 —— 字号调到 1.6 后 584 高的窗口仍按 1.0 的
        阈值判定（不折叠），工具行又被裁。这里锁死「字号变化会重新判定」。
        """
        from PyQt6.QtTest import QTest
        # Rev14 F1 重测阈值：1.0 → 720，1.6 → 760。取 740 这个高度跨越两个阈值 ——
        # 字号 1.0 放得下（不折叠），字号 1.6 放不下（必须折叠）。
        self.win.resize(1020, 820)
        QTest.qWait(700)
        self.assertFalse(getattr(self.win, "_tools_folded", True),
                         "前提：字号 1.0 时 820 高应放得下、不折叠")

        self.win.settings.font_scale = 1.6
        self.win._apply_font_scale()
        QTest.qWait(400)
        self.assertTrue(getattr(self.win, "_tools_folded", False),
                        "字号 1.6 时同一高度放不下，必须自动折叠")
        self.assertTrue(self.win.btn_more_tools.isVisible(),
                        "折叠后「更多 ▸」必须出现")
        self.assertEqual(self._unclipped(_ALWAYS_VISIBLE), [],
                         "字号 1.6 + 折叠后常驻控件仍必须完整可见")
        # 现场还原：这个窗口是类级共享的，字号留在 1.6 会让后续用例在
        # 「放大后的需求高度」下跑，失败信息指向错误的原因。
        self.win.settings.font_scale = 1.0
        self.win._apply_font_scale()
        QTest.qWait(400)


class TestFontScaleFloorSingleSource(unittest.TestCase):
    """字号下限只能有一个来源：`typography.FONT_SCALE_MIN_UI`。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_zoom_out_never_goes_below_ui_floor(self):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        from gcm.ui.typography import FONT_SCALE_MIN_UI

        d = Path(tempfile.mkdtemp())
        win = MainWindow(data_dir=d, db=Database(d / "t.db"),
                         settings=SettingsStore(d / "settings.json"))
        try:
            for _ in range(20):          # 压到底
                win.zoom_out()
            self.assertAlmostEqual(win.settings.font_scale, FONT_SCALE_MIN_UI,
                                   msg="zoom_out 必须停在 FONT_SCALE_MIN_UI，不能到 0.8")
        finally:
            win.close()
            win.deleteLater()

    def test_ui_floor_is_actually_reachable_and_above_theme_clamp(self):
        """FONT_SCALE_MIN_UI 必须 ≥ QSS 层的钳制下限 0.8，否则口径互斥。"""
        from gcm.ui.typography import FONT_SCALE_MIN_UI
        from gcm.ui.theme import qss_for_scale
        self.assertEqual(FONT_SCALE_MIN_UI, 0.9)
        # QSS 层仍能渲染 0.8（历史兼容），但 UI 不允许走到那里
        self.assertIn("font-size", qss_for_scale(0.8))


if __name__ == "__main__":
    unittest.main()