# -*- coding: utf-8 -*-
"""G43-3/G43-7 专项测试：慢仓 Top10 + WAL checkpoint/autocheckpoint。"""
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


def _db_with_slow_history() -> Database:
    d = Path(tempfile.mkdtemp())
    db = Database(d / "t.db")
    r1 = db.upsert_repo(_spec("o1", "a"), "/x/o1__a")
    r2 = db.upsert_repo(_spec("o2", "b"), "/x/o2__b")
    today = date.today()
    base = today.strftime("%Y-%m-%d")
    db.add_sync_history(r1, SyncStatus.SUCCESS, SyncAction.UPDATED, "ok", duration_ms=5000,
                        started_at=f"{base} 10:00:00")
    db.add_sync_history(r1, SyncStatus.SUCCESS, SyncAction.UPDATED, "ok", duration_ms=7000,
                        started_at=f"{base} 11:00:00")
    db.add_sync_history(r2, SyncStatus.SUCCESS, SyncAction.UPDATED, "ok", duration_ms=9000,
                        started_at=f"{base} 12:00:00")
    return db


class TestStatsSlowRepos(unittest.TestCase):
    def test_topn_sorted_desc(self):
        db = _db_with_slow_history()
        try:
            rows = db.stats_slow_repos()
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["owner_repo"], "o2/b")
            self.assertGreater(rows[0]["avg_ms"], rows[1]["avg_ms"])
            self.assertEqual(rows[1]["owner_repo"], "o1/a")
            self.assertEqual(rows[1]["count"], 2)
            self.assertAlmostEqual(rows[1]["avg_ms"], 6000.0)
            self.assertIn("last_sync", rows[0])
        finally:
            db.close()

    def test_limit_param(self):
        db = _db_with_slow_history()
        try:
            rows = db.stats_slow_repos(limit=1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["owner_repo"], "o2/b")
        finally:
            db.close()

    def test_empty_db(self):
        d = Path(tempfile.mkdtemp())
        db = Database(d / "t.db")
        try:
            self.assertEqual(db.stats_slow_repos(), [])
        finally:
            db.close()

    def test_exception_fallback_ui(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        db = mock.Mock()
        db.stats_overview.return_value = {"total_repos": 0, "total_syncs": 0,
                                          "success_syncs": 0, "failed_syncs": 0, "by_host": {}}
        db.stats_usage.return_value = {"clones": 0, "updates": 0, "commits": 0}
        db.stats_daily.return_value = []
        db.stats_slow_repos.side_effect = RuntimeError("boom")
        dlg = StatisticsDialog(db=db)
        self.assertIn("暂无同步数据", dlg.lbl_slow.text())

    def test_ui_shows_top10_lines(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        db = _db_with_slow_history()
        try:
            dlg = StatisticsDialog(db=db)
            txt = dlg.lbl_slow.text()
            self.assertIn("o2/b", txt)
            self.assertIn("9.0s", txt)
            self.assertIn("o1/a", txt)
            self.assertIn("6.0s", txt)
        finally:
            db.close()

    def test_ui_empty_placeholder(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        d = Path(tempfile.mkdtemp())
        db = Database(d / "t.db")
        try:
            dlg = StatisticsDialog(db=db)
            self.assertIn("暂无同步数据", dlg.lbl_slow.text())
        finally:
            db.close()


class TestWalCheckpoint(unittest.TestCase):
    def _db(self) -> Database:
        d = Path(tempfile.mkdtemp())
        return Database(d / "t.db")

    def test_checkpoint_structure(self):
        db = self._db()
        try:
            r = db.wal_checkpoint()
            self.assertIn("busy", r)
            self.assertIn("log", r)
            self.assertIn("checkpointed", r)
            self.assertIsInstance(r["busy"], bool)
        finally:
            db.close()

    def test_checkpoint_repeatable(self):
        db = self._db()
        try:
            r1 = db.wal_checkpoint()
            r2 = db.wal_checkpoint()
            self.assertEqual(set(r1.keys()), set(r2.keys()))
        finally:
            db.close()

    def test_autocheckpoint_set_and_read(self):
        db = self._db()
        try:
            db.wal_autocheckpoint(2000)
            row = db._conn.execute("PRAGMA wal_autocheckpoint").fetchone()
            self.assertEqual(int(row[0]), 2000)
        finally:
            db.close()

    def test_autocheckpoint_idempotent(self):
        db = self._db()
        try:
            db.wal_autocheckpoint(1000)
            db.wal_autocheckpoint(1000)
            row = db._conn.execute("PRAGMA wal_autocheckpoint").fetchone()
            self.assertEqual(int(row[0]), 1000)
        finally:
            db.close()

    def test_checkpoint_no_throw_on_closed(self):
        db = self._db()
        db.close()
        r = db.wal_checkpoint()  # 不应抛异常（连接已关 → 异常被吞）
        self.assertIsInstance(r, dict)


if __name__ == "__main__":
    unittest.main(verbosity=2)
