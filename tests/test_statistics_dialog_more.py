# -*- coding: utf-8 -*-
"""G50-7 统计中心补充覆盖：_TrendCanvas 绘制、汇总/趋势/用量数据、异常回退。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gcm.db.repo_db import Database
from gcm.models import RepoSpec, SyncAction, SyncStatus


def _spec(owner, repo):
    return RepoSpec(owner, repo, f"https://github.com/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


def _db_with_history() -> Database:
    d = Path(tempfile.mkdtemp())
    db = Database(d / "t.db")
    rid1 = db.upsert_repo(_spec("o1", "a"), "/x/o1__a")
    rid2 = db.upsert_repo(_spec("o2", "b"), "/x/o2__b")
    today = date.today()
    db.add_sync_history(rid1, SyncStatus.SUCCESS, SyncAction.CLONED, "ok",
                        commits=2, duration_ms=100,
                        started_at=(today - timedelta(days=1)).strftime("%Y-%m-%d 10:00:00"))
    db.add_sync_history(rid1, SyncStatus.SUCCESS, SyncAction.UPDATED, "ok",
                        commits=3, duration_ms=200,
                        started_at=today.strftime("%Y-%m-%d 11:00:00"))
    db.add_sync_history(rid2, SyncStatus.FAILED, SyncAction.FAILED, "bad",
                        duration_ms=300,
                        started_at=today.strftime("%Y-%m-%d 12:00:00"))
    db.add_sync_history(rid1, SyncStatus.CONFLICT, SyncAction.CONFLICT, "conf",
                        duration_ms=50,
                        started_at=today.strftime("%Y-%m-%d 13:00:00"))
    return db


class TestTrendCanvas(unittest.TestCase):
    def _canvas(self):
        from gcm.ui.statistics_dialog import _TrendCanvas
        c = _TrendCanvas()
        c.resize(600, 200)
        return c

    def test_empty_data_placeholder(self):
        from gcm.ui.statistics_dialog import _TrendCanvas
        c = self._canvas()
        c.set_data(None)
        self.assertEqual(c._data, [])
        c.set_data([])
        self.assertEqual(c._data, [])
        with mock.patch("gcm.ui.statistics_dialog.QPainter") as mp:
            painter = mp.return_value
            c.paintEvent(None)
        texts = [str((a.args[2] if len(a.args) >= 3 else (a.kwargs.get("text", "")))) for a in painter.drawText.call_args_list]
        self.assertTrue(any("暂无同步数据" in t for t in texts))
        painter.fillRect.assert_called_once()
        painter.drawLine.assert_not_called()

    def test_with_data_paints_bars_and_axis(self):
        c = self._canvas()
        data = [
            {"date": "2026-09-16", "success": 3, "failed": 1, "conflict": 0},
            {"date": "2026-09-17", "success": 0, "failed": 0, "conflict": 2},
            {"date": "2026-09-18", "success": 2, "failed": 0, "conflict": 1},
        ]
        c.set_data(data)
        self.assertIs(c._data, data)
        with mock.patch("gcm.ui.statistics_dialog.QPainter") as mp:
            painter = mp.return_value
            c.paintEvent(None)
        fills = painter.fillRect.call_args_list
        self.assertEqual(len(fills), 6)  # 背景 1 + 正数柱 5（success 3/2、failed 1、conflict 2/1）
        painter.drawLine.assert_called_once()

    def test_all_zero_data_draws_axis_only(self):
        c = self._canvas()
        c.set_data([{"success": 0, "failed": 0, "conflict": 0},
                    {"success": -1, "failed": 0, "conflict": 0}])
        with mock.patch("gcm.ui.statistics_dialog.QPainter") as mp:
            painter = mp.return_value
            c.paintEvent(None)
        self.assertEqual(len(painter.fillRect.call_args_list), 1)  # 仅背景
        painter.drawLine.assert_called_once()

    def test_real_render_pixmap(self):
        from PyQt6.QtGui import QPixmap
        c = self._canvas()
        c.set_data([{"success": 5, "failed": 2, "conflict": 1}])
        pm = QPixmap(c.size())
        c.render(pm)
        self.assertFalse(pm.isNull())
        c.set_data([])
        pm2 = QPixmap(c.size())
        c.render(pm2)
        self.assertFalse(pm2.isNull())


class TestStatisticsDialogMore(unittest.TestCase):
    def test_dialog_trend_and_usage_data(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        db = _db_with_history()
        try:
            dlg = StatisticsDialog(db=db)
            self.assertEqual(len(dlg.trend_canvas._data), 30)
            last = dlg.trend_canvas._data[-1]
            self.assertEqual((last["success"], last["failed"], last["conflict"]), (1, 1, 1))
            prev = dlg.trend_canvas._data[-2]
            self.assertEqual((prev["success"], prev["failed"], prev["conflict"]), (1, 0, 0))
            dates = [d["date"] for d in dlg.trend_canvas._data]
            self.assertEqual(dates, sorted(dates))
            self.assertIn("累计克隆 1 次", dlg.lbl_usage.text())
            self.assertIn("累计更新 1 次", dlg.lbl_usage.text())
            self.assertIn("累计新增提交 3 个", dlg.lbl_usage.text())  # commits 仅统计 updated action
            self.assertIn("github.com", dlg.lbl_hosts.text())
            self.assertIn("同步总次数：4", dlg.lbl_summary.text())
        finally:
            db.close()

    def test_refresh_button_reloads(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        db = _db_with_history()
        try:
            dlg = StatisticsDialog(db=db)
            self.assertIn("同步总次数：4", dlg.lbl_summary.text())
            rid3 = db.upsert_repo(_spec("o3", "c"), "/x/o3__c")
            db.add_sync_history(rid3, SyncStatus.SUCCESS, SyncAction.CLONED, "ok",
                                commits=1, duration_ms=10,
                                started_at=date.today().strftime("%Y-%m-%d 14:00:00"))
            dlg.btn_refresh.click()
            self.assertIn("同步总次数：5", dlg.lbl_summary.text())
            self.assertIn("仓库总数：3", dlg.lbl_summary.text())
        finally:
            db.close()

    def test_overview_exception_fallback(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        db = mock.Mock()
        db.stats_overview.side_effect = RuntimeError("boom")
        db.stats_usage.return_value = {"clones": 0, "updates": 0, "commits": 0}
        db.stats_daily.return_value = []
        dlg = StatisticsDialog(db=db)
        self.assertIn("仓库总数：0", dlg.lbl_summary.text())
        self.assertIn("平台分布：（暂无仓库）", dlg.lbl_hosts.text())
        self.assertEqual(dlg.trend_canvas._data, [])

    def test_usage_exception_fallback(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        db = mock.Mock()
        db.stats_overview.return_value = {"total_repos": 2, "total_syncs": 3,
                                          "success_syncs": 2, "failed_syncs": 1,
                                          "by_host": {"github.com": 2}}
        db.stats_usage.side_effect = RuntimeError("boom")
        db.stats_daily.return_value = []
        dlg = StatisticsDialog(db=db)
        self.assertIn("用量统计：（不可用）", dlg.lbl_usage.text())
        self.assertIn("平台分布：github.com: 2", dlg.lbl_hosts.text())

    def test_daily_exception_fallback(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        db = mock.Mock()
        db.stats_overview.return_value = {"total_repos": 1, "total_syncs": 1,
                                          "success_syncs": 1, "failed_syncs": 0,
                                          "by_host": {}}
        db.stats_usage.return_value = {"clones": 1, "updates": 0, "commits": 0}
        db.stats_daily.side_effect = RuntimeError("boom")
        dlg = StatisticsDialog(db=db)
        self.assertEqual(dlg.trend_canvas._data, [])

    def test_close_button_accepts(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        db = mock.Mock()
        db.stats_overview.return_value = {"total_repos": 0, "total_syncs": 0,
                                          "success_syncs": 0, "failed_syncs": 0,
                                          "by_host": {}}
        db.stats_usage.return_value = {"clones": 0, "updates": 0, "commits": 0}
        db.stats_daily.return_value = []
        dlg = StatisticsDialog(db=db)
        dlg.btn_close.click()
        self.assertEqual(dlg.result(), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
