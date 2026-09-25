"""G53-4/5 数据洞察测试：历史保留参数化 / 定时报表。"""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from gcm.app.sched_reports import ReportScheduler
from gcm.db.repo_db import Database
from gcm.models import RepoSpec, SyncAction, SyncStatus


class TestHistoryRetention(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_g53_ret_"))
        self.db = Database(self.tmp / "repos.db")
        self.repo_id = self.db.upsert_repo(
            RepoSpec("a", "b", "https://github.com/a/b.git", folder_name="a__b"),
            local_path=str(self.tmp / "a__b"), host="github.com")

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass

    def test_default_90(self):
        self.assertEqual(self.db.get_history_retention_days(), 90)

    def test_set_get_retention(self):
        self.db.set_history_retention_days(30)
        self.assertEqual(self.db.get_history_retention_days(), 30)
        self.db.set_history_retention_days(99999)
        self.assertEqual(self.db.get_history_retention_days(), 3650)
        self.db.set_history_retention_days("bad")
        self.assertEqual(self.db.get_history_retention_days(), 90)

    def test_prune_history(self):
        for i in range(220):
            self.db.add_sync_history(self.repo_id, SyncStatus.SUCCESS, SyncAction.CLONED, "ok",
                                     commits=1, duration_ms=10)
        before = len(self.db.history(self.repo_id, 1000))
        self.assertGreater(before, 200)
        deleted = self.db.prune_history(90)
        self.assertGreaterEqual(deleted, before - 200)
        after = len(self.db.history(self.repo_id, 1000))
        self.assertLessEqual(after, 200)

    def test_maybe_prune_force(self):
        n = self.db._maybe_prune_history(force=True)
        self.assertIsInstance(n, int)


class TestReportScheduler(unittest.TestCase):
    def test_run_once_csv(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            called = {}
            def report_fn(dest, fmt):
                dest.write_text("a,b\n1,2\n", encoding="utf-8")
                called["dest"] = dest
                return 2
            sched = ReportScheduler(report_fn, interval_min=1, out_dir=td / "out",
                                    fmt="csv", now_fn=lambda: datetime(2026, 9, 26, 10, 0, 0))
            out = sched.run_once()
            self.assertIsNotNone(out)
            self.assertTrue(out.exists())
            self.assertIn(".csv", out.name)

    def test_run_once_markdown(self):
        with tempfile.TemporaryDirectory() as td:
            def report_fn(dest, fmt):
                dest.write_text("| a |\n", encoding="utf-8")
                return 1
            sched = ReportScheduler(report_fn, interval_min=1, out_dir=Path(td) / "o",
                                    fmt="md")
            out = sched.run_once()
            self.assertIn(".md", out.name)

    def test_failure_returns_none(self):
        def bad(dest, fmt):
            raise RuntimeError("boom")
        sched = ReportScheduler(bad, interval_min=1)
        self.assertIsNone(sched.run_once())


if __name__ == "__main__":
    unittest.main()
