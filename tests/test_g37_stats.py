# -*- coding: utf-8 -*-
"""G37 数据能力 主控侧 单测（离屏 Qt）。

覆盖：
- G37-1 stats_daily：30 天聚合（补零/三序列/日期升序）
- G37-5 stats_usage：克隆/更新/新增提交聚合
- 统计中心对话框：趋势画布 + 用量标签渲染
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication

from gcm.db.repo_db import Database
from gcm.models import RepoSpec, SyncAction, SyncStatus
from gcm.ui.statistics_dialog import StatisticsDialog

_app = QApplication.instance() or QApplication(sys.argv)


def _mkdb(base: Path) -> Database:
    db = Database(base / "t.db")
    db.upsert_repo(RepoSpec(owner="o", repo="r", url_https="https://github.com/o/r",
                            folder_name="o__r"), local_path=str(base), host="github.com")
    rid = db.get_repo("o", "r", "github.com")["id"]
    return db, rid


class TestStatsDaily(unittest.TestCase):
    """G37-1 stats_daily 聚合。"""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.db, self.rid = _mkdb(self.base)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass

    def test_daily_pads_missing_days(self):
        """无数据的天补零；长度=30；日期升序。"""
        data = self.db.stats_daily(days=30)
        self.assertEqual(len(data), 30)
        dates = [d["date"] for d in data]
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(data[-1]["date"], datetime.date.today().strftime("%Y-%m-%d"))
        self.assertEqual(data[0]["success"], 0)

    def test_daily_aggregates_three_series(self):
        """今天写入 成功2/失败1/冲突1 → 最后一天序列正确。"""
        today = datetime.date.today().strftime("%Y-%m-%d") + " 12:00:00"
        for _ in range(2):
            self.db.add_sync_history(self.rid, SyncStatus.SUCCESS, SyncAction.CLONED,
                                     started_at=today)
        self.db.add_sync_history(self.rid, SyncStatus.FAILED, SyncAction.FAILED,
                                 started_at=today)
        self.db.add_sync_history(self.rid, SyncStatus.CONFLICT, SyncAction.CONFLICT,
                                 started_at=today)
        data = self.db.stats_daily(days=30)
        last = data[-1]
        self.assertEqual(last["success"], 2)
        self.assertEqual(last["failed"], 1)
        self.assertEqual(last["conflict"], 1)

    def test_daily_clamps_days(self):
        """days 越界被钳制到 1..90。"""
        self.assertEqual(len(self.db.stats_daily(days=0)), 1)
        self.assertEqual(len(self.db.stats_daily(days=999)), 90)


class TestStatsUsage(unittest.TestCase):
    """G37-5 stats_usage 用量聚合。"""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.db, self.rid = _mkdb(self.base)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass

    def test_usage_counts(self):
        """克隆2次/更新1次(提交5) → 数字正确。"""
        ts = datetime.date.today().strftime("%Y-%m-%d") + " 10:00:00"
        self.db.add_sync_history(self.rid, SyncStatus.SUCCESS, SyncAction.CLONED,
                                 started_at=ts)
        self.db.add_sync_history(self.rid, SyncStatus.SUCCESS, SyncAction.CLONED,
                                 started_at=ts)
        self.db.add_sync_history(self.rid, SyncStatus.SUCCESS, SyncAction.UPDATED,
                                 commits=5, started_at=ts)
        u = self.db.stats_usage()
        self.assertEqual(u["clones"], 2)
        self.assertEqual(u["updates"], 1)
        self.assertEqual(u["commits"], 5)

    def test_usage_empty(self):
        """无历史 → 全 0。"""
        u = self.db.stats_usage()
        self.assertEqual(u, {"clones": 0, "updates": 0, "commits": 0})


class TestStatsDialog(unittest.TestCase):
    """统计中心对话框渲染（趋势画布 + 用量标签）。"""

    def setUp(self):
        self.base = Path(tempfile.mkdtemp())
        self.db, self.rid = _mkdb(self.base)
        ts = datetime.date.today().strftime("%Y-%m-%d") + " 11:00:00"
        self.db.add_sync_history(self.rid, SyncStatus.SUCCESS, SyncAction.CLONED,
                                 started_at=ts)
        self.dlg = StatisticsDialog(db=self.db)

    def tearDown(self):
        try:
            self.dlg.close()
        except Exception:
            pass
        try:
            self.db.close()
        except Exception:
            pass

    def test_dialog_has_trend_canvas_with_data(self):
        """趋势画布存在且数据非空（30 项）。"""
        self.assertTrue(hasattr(self.dlg, "trend_canvas"))
        self.assertEqual(len(self.dlg.trend_canvas._data), 30)
        self.assertEqual(self.dlg.trend_canvas._data[-1]["success"], 1)

    def test_dialog_usage_label(self):
        """用量标签含「累计克隆」且数字正确。"""
        self.assertIn("累计克隆 1 次", self.dlg.lbl_usage.text())
        self.assertIn("累计更新 0 次", self.dlg.lbl_usage.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)