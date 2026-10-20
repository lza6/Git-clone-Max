# -*- coding: utf-8 -*-
"""G53-1 多维评分卡雷达图：数据裁剪、空态绘制、统计中心接线。

回归点：`RadarChart` 定义了但统计中心没挂载 = 用户看不到（本项目反复出现的
「写了但不可达」模式）。因此这里既测控件本身，也测统计对话框里真的有它、
且刷新后拿到了 6 维平均值。
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

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from gcm.ui.radar_chart import DEFAULT_LABELS, RadarChart  # noqa: E402


class TestRadarData(unittest.TestCase):
    def test_missing_keys_default_to_zero(self):
        w = RadarChart()
        w.set_values({"success_rate": 80})
        values = w.values()
        self.assertEqual(set(values), set(DEFAULT_LABELS))
        self.assertEqual(values["success_rate"], 80)
        self.assertEqual(values["freshness"], 0)

    def test_values_are_clamped(self):
        w = RadarChart()
        w.set_values({"success_rate": 120, "freshness": -5,
                      "conflicts": "bad", "size_score": None})
        v = w.values()
        self.assertEqual(v["success_rate"], 100)
        self.assertEqual(v["freshness"], 0)
        self.assertEqual(v["conflicts"], 0)
        self.assertEqual(v["size_score"], 0)

    def test_values_returns_copy(self):
        w = RadarChart()
        w.set_values({"success_rate": 50})
        got = w.values()
        got["success_rate"] = 1
        self.assertEqual(w.values()["success_rate"], 50)


class TestRadarPaint(unittest.TestCase):
    def test_empty_state_paints_without_crash(self):
        w = RadarChart()
        w.resize(360, 300)
        pm = w.grab()
        self.assertFalse(pm.isNull())
        self.assertEqual((pm.width(), pm.height()), (360, 300))

    def test_paint_with_values(self):
        w = RadarChart()
        w.resize(360, 280)
        w.set_values({k: 60 for k in DEFAULT_LABELS})
        pm = w.grab()
        self.assertFalse(pm.isNull())

    def test_all_zero_values_are_real_data_not_empty_state(self):
        """六维全 0 是真实评分，不应显示成「暂无数据」。"""
        from gcm.ui.radar_chart import DEFAULT_LABELS
        w = RadarChart()
        w.resize(300, 240)
        w.set_values({k: 0 for k in DEFAULT_LABELS})
        self.assertTrue(w._has_data)
        self.assertFalse(w.grab().isNull())

    def test_tiny_size_does_not_crash(self):
        w = RadarChart()
        w.resize(20, 20)
        self.assertFalse(w.grab().isNull())


class TestStatisticsDialogWiring(unittest.TestCase):
    def _db(self):
        from gcm.db.repo_db import Database
        from gcm.models import RepoSpec, SyncAction, SyncStatus
        d = Path(tempfile.mkdtemp(prefix="gcm_radar_"))
        db = Database(d / "t.db")
        db.upsert_repo(RepoSpec(owner="a", repo="b",
                                url_https="https://github.com/a/b.git",
                                folder_name="a__b"),
                       local_path=str(d / "a__b"), host="github.com")
        rec = db.get_repo("a", "b", "github.com")
        db.add_sync_history(rec["id"], SyncStatus.SUCCESS, SyncAction.CLONED,
                            message="ok", commits=1, duration_ms=1200)
        return db

    def test_dialog_has_radar_and_values(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        dlg = StatisticsDialog(self._db())
        self.addCleanup(dlg.close)
        self.assertTrue(hasattr(dlg, "radar"), "统计中心必须挂载雷达图")
        self.assertIsInstance(dlg.radar, RadarChart)
        values = dlg.radar.values()
        self.assertEqual(set(values), set(DEFAULT_LABELS))
        self.assertTrue(any(v > 0 for v in values.values()),
                        f"有同步历史时评分不应全 0：{values}")

    def test_dialog_without_repos_shows_empty_state(self):
        from gcm.db.repo_db import Database
        from gcm.ui.statistics_dialog import StatisticsDialog
        d = Path(tempfile.mkdtemp(prefix="gcm_radar_empty_"))
        dlg = StatisticsDialog(Database(d / "t.db"))
        self.addCleanup(dlg.close)
        self.assertEqual(set(dlg.radar.values()), set(DEFAULT_LABELS))
        self.assertEqual(sum(dlg.radar.values().values()), 0)

    def test_theme_switch_paints(self):
        from gcm.ui import theme as _th
        from gcm.ui.statistics_dialog import StatisticsDialog
        dlg = StatisticsDialog(self._db())
        self.addCleanup(dlg.close)
        try:
            _th.apply_theme("light")
            self.assertFalse(dlg.radar.grab().isNull())
            _th.apply_theme("nord")
            self.assertFalse(dlg.radar.grab().isNull())
        finally:
            _th.apply_theme("deep")


if __name__ == "__main__":
    unittest.main(verbosity=2)
