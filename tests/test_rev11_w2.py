"""Rev11 W2/W3 守卫：批次结论条 + 进度表耗时列 + pill alpha 预算。

对应计划：`计划书/UI-视觉重设计-Rev11.md` §4（V-09 / V-10 / V-11 / V-13 / V-16）。
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from gcm.ui.batch_conclusion import BatchConclusionBar  # noqa: E402
from gcm.ui.palettes import PALETTES  # noqa: E402
from gcm.ui.status_pill import StatusPillDelegate  # noqa: E402


def _pal(name: str = "deep") -> dict:
    return dict(PALETTES[name])


class TestBatchConclusionBar(unittest.TestCase):
    """V-09：三态叙事 + 空态不显示假 0。"""

    def setUp(self):
        self.bar = BatchConclusionBar()

    def test_idle_ready_list(self):
        self.bar.set_ready(clone=9, update=3, protected=0, blocked=0)
        self.assertEqual(self.bar.state(), "idle")
        self.assertIn("12", self.bar.metric_text())
        self.assertIn("9", self.bar.detail_lbl.text())
        self.assertIn("3", self.bar.detail_lbl.text())

    def test_idle_zero_total_shows_placeholder(self):
        """空态不许显示假 0（与 status_strip 同一纪律）。"""
        self.bar.set_ready(clone=0, update=0)
        self.assertEqual(self.bar.metric_text(), "—")

    def test_running_progress(self):
        self.bar.begin(4)
        self.assertEqual(self.bar.state(), "running")
        for st in ("success", "failed", "success", "conflict"):
            self.bar.note_result(st)
        self.assertEqual(self.bar.metric_text(), "100%")
        detail = self.bar.detail_lbl.text()
        self.assertIn("2", detail)  # 成功 2
        self.assertIn("2", detail)  # 失败 2（failed+conflict）

    def test_running_zero_total_no_crash(self):
        self.bar.begin(0)
        self.bar.note_result("success")
        self.assertEqual(self.bar.metric_text(), "0%")

    def test_finished_scorecard(self):
        self.bar.begin(4)
        for st in ("success", "success", "success", "failed"):
            self.bar.note_result(st)
        self.bar.finish()
        self.assertEqual(self.bar.state(), "finished")
        self.assertIn("75%", self.bar.metric_text())
        self.assertIn("1", self.bar.detail_lbl.text())  # 失败 1
        self.assertFalse(self.bar.review_btn.isHidden(),
                         "有失败必须出现「查看失败」入口（widget 未 show 时用 isHidden 判断）")

    def test_finished_no_failure_hides_review(self):
        self.bar.begin(2)
        self.bar.note_result("success")
        self.bar.note_result("success")
        self.bar.finish()
        self.assertTrue(self.bar.review_btn.isHidden())

    def test_motion_off_skips_animations(self):
        """V-13：ck_animations/prefers_reduced_motion 关闭时零动效。"""
        bar = BatchConclusionBar(motion=False)
        bar.begin(4)
        self.assertIsNone(bar._anim)
        bar.note_result("success")
        bar.finish()
        self.assertIsNone(bar._anim)
        self.assertIsNone(bar._opacity)

    def test_pulse_releases_effect(self):
        """V-13：终态脉冲的 opacity effect 必须被释放（不留常驻 effect）。"""
        bar = BatchConclusionBar(motion=True)
        bar.begin(1)
        bar.note_result("failed")
        bar.finish()
        # 动画完成前 effect 存在；手动触发清理路径验证可释放
        bar._drop_effect()
        self.assertIsNone(bar._opacity)


class TestPillBudget(unittest.TestCase):
    """V-05（W2 部分）：pill 底色 alpha 收敛。"""

    def test_pill_alpha_budget(self):
        import inspect
        src = inspect.getsource(StatusPillDelegate.paint)
        # 允许的 alpha 上限：底 18 / 描边 90（§2.3）
        self.assertNotIn("setAlpha(38)", src,
                         "pill 底 alpha 38 已超预算（§2.3 规定 ≤18）")


class TestContrastAfterWiring(unittest.TestCase):
    """新键落 QSS 后四主题仍达标。"""

    def test_text_on_panel_primary_all_themes(self):
        from gcm.ui.palettes import contrast_ratio
        for name, pal in PALETTES.items():
            with self.subTest(theme=name):
                floor = 7.0 if name == "hc" else 4.5
                self.assertGreaterEqual(
                    contrast_ratio(pal["text"], pal["panel_primary"]), floor)


class TestFormatDuration(unittest.TestCase):
    """V-16：耗时列唯一口径（实现位于 result_labels，progress_table 转发）。"""

    def test_zero_and_bad_are_dash(self):
        from gcm.ui.result_labels import format_duration
        for v in (None, 0, -5, "abc"):
            self.assertEqual(format_duration(v), "—", f"{v!r} 应显示 —")

    def test_seconds(self):
        from gcm.ui.result_labels import format_duration
        self.assertEqual(format_duration(12300), "12.3s")
        self.assertEqual(format_duration(59999), "60.0s")

    def test_minutes(self):
        from gcm.ui.result_labels import format_duration
        self.assertEqual(format_duration(60000), "1:00")
        self.assertEqual(format_duration(75400), "1:15")

    def test_progress_table_reuses_same_impl(self):
        """防两处真相：progress_table 的转发必须指向 result_labels 的实现。"""
        from gcm.ui import progress_table
        from gcm.ui.result_labels import format_duration
        self.assertIs(progress_table._format_duration, format_duration)


class TestEdgeRuler(unittest.TestCase):
    """V-11：左缘刻度尺。"""

    def test_status_color_semantics(self):
        from gcm.ui.edge_ruler import status_color
        pal = _pal()
        # 终态有色；pending/skipped 留白
        self.assertIsNotNone(status_color("success", pal))
        self.assertIsNotNone(status_color("failed", pal))
        self.assertIsNotNone(status_color("running", pal))
        self.assertIsNone(status_color("pending", pal))
        self.assertIsNone(status_color("", pal))
        # alpha 收敛在预算内（§2.3）
        self.assertLessEqual(status_color("success", pal).alpha(), 130)

    def test_delegate_does_not_touch_item_data(self):
        """与 StatusPillDelegate 同一约束：只绘制，不改文本/数据。"""
        from PyQt6.QtCore import Qt
        from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

        from gcm.ui.edge_ruler import EdgeRulerDelegate
        t = QTableWidget(1, 3)
        it = QTableWidgetItem("hello")
        it.setData(Qt.ItemDataRole.UserRole, "success")
        t.setItem(0, 0, it)
        st = QTableWidgetItem("成功")
        st.setData(Qt.ItemDataRole.UserRole, "success")
        t.setItem(0, 2, st)
        EdgeRulerDelegate(t)
        self.assertEqual(t.item(0, 0).text(), "hello")
        self.assertEqual(t.item(0, 0).data(Qt.ItemDataRole.UserRole), "success")

    def test_wired_to_progress_table(self):
        """生产调用点：刻度尺必须真的挂在进度表第 0 列（孤儿模块守卫口径）。"""
        from gcm.ui.edge_ruler import EdgeRulerDelegate
        from gcm.ui.progress_table import ProgressTable
        panel = ProgressTable(owner=None)
        self.assertIsInstance(panel._ruler_delegate, EdgeRulerDelegate)
        self.assertIs(panel.table.itemDelegateForColumn(0), panel._ruler_delegate)


class TestDurationColumnWiring(unittest.TestCase):
    """V-16：6 列表 + 耗时列生产接线。"""

    def test_table_has_six_columns(self):
        from gcm.ui.progress_table import ProgressTable
        panel = ProgressTable(owner=None)
        self.assertEqual(panel.table.columnCount(), 6)
        self.assertEqual(panel.table.horizontalHeaderItem(5).text(), "耗时")

    def test_result_writes_duration(self):
        from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus
        from gcm.ui.progress_table import ProgressTable

        class _Settings:
            animations = False

        class _UrlHistory:
            add = staticmethod(lambda *a: None)  # noqa: E704
        owner = type("O", (), {"settings": _Settings(), "tray": None,
                               "_emit_log": lambda *a, **k: None,
                               "url_history": _UrlHistory()})()
        panel = ProgressTable(owner=owner)
        panel._prepare_table(1)
        spec = RepoSpec(owner="a", repo="b",
                        url_https="https://github.com/a/b.git", folder_name="a__b")
        panel._add_table_row(0, spec)
        panel._on_worker_result(0, SyncResult(
            spec=spec, status=SyncStatus.SUCCESS, action=SyncAction.CLONED,
            message="ok", duration_ms=15300))
        self.assertEqual(panel.table.item(0, 5).text(), "15.3s")


if __name__ == "__main__":
    unittest.main()
