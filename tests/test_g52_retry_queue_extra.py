"""G52-1 重试队列边界分支补充测试 + G52-4 进度治理 force/阈值分支。"""
import json
import tempfile
import unittest
from pathlib import Path

from gcm.app.retry_queue import RetryQueue
from gcm.db import progress_gc
from gcm.models import RepoSpec


def _spec(owner="o", repo="r") -> RepoSpec:
    return RepoSpec(owner=owner, repo=repo,
                    url_https=f"https://github.com/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


class TestRetryQueueExtra(unittest.TestCase):
    def test_disabled_not_recorded(self):
        q = RetryQueue(path=None, enabled=False)
        self.assertFalse(q.record_failure(_spec(), "Connection timed out"))

    def test_non_network_not_recorded(self):
        q = RetryQueue(path=None, enabled=True)
        self.assertFalse(q.record_failure(_spec(), "Repository not found"))

    def test_max_retries_dropped(self):
        with tempfile.TemporaryDirectory() as td:
            q = RetryQueue(Path(td) / "rq.json", enabled=True, max_retries=1)
            self.assertTrue(q.record_failure(_spec(), "Connection timed out"))
            self.assertFalse(q.record_failure(_spec(), "Connection timed out"))  # 超限
            self.assertEqual(q.count(), 0)
            data = q.load()
            self.assertEqual(len(data["dropped"]), 1)

    def test_due_tasks_sorted(self):
        with tempfile.TemporaryDirectory() as td:
            q = RetryQueue(Path(td) / "rq.json", enabled=True,
                           backoff_base_sec=30, now=lambda: 1000.0)
            q.record_failure(_spec("a", "r1"), "Connection timed out")  # next=1000+30
            q.record_failure(_spec("b", "r2"), "Connection timed out")
            due = q.due_tasks(now=1030.0)
            self.assertEqual(len(due), 2)
            self.assertEqual(due[0]["key"], "a/r1")

    def test_to_spec_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            q = RetryQueue(Path(td) / "rq.json", enabled=True)
            q.record_failure(_spec(), "Connection timed out")
            rec = q.list_entries()[0]
            sp = q.to_spec(rec)
            self.assertEqual(sp.folder_name, "o__r")

    def test_to_spec_invalid_key(self):
        q = RetryQueue(path=None)
        with self.assertRaises(ValueError):
            q.to_spec({"key": "bad"})

    def test_mark_success_idempotent(self):
        q = RetryQueue(path=None, enabled=True)
        q.record_failure(_spec(), "Connection timed out")
        q.mark_success("o/r")
        q.mark_success("o/r")  # 幂等
        self.assertEqual(q.count(), 0)

    def test_clear_keeps_dropped(self):
        with tempfile.TemporaryDirectory() as td:
            q = RetryQueue(Path(td) / "rq.json", enabled=True, max_retries=0)
            q.record_failure(_spec(), "Connection timed out")
            self.assertEqual(q.count(), 0)
            q.record_failure(_spec(), "Connection timed out")
            q.clear()
            self.assertEqual(len(q.load()["dropped"]), 2)

    def test_attempts_and_set_enabled(self):
        with tempfile.TemporaryDirectory() as td:
            q = RetryQueue(Path(td) / "rq.json", enabled=True)
            q.record_failure(_spec(), "Connection timed out")
            self.assertEqual(q.attempts_for("o/r"), 1)
            q.set_enabled(False)
            self.assertFalse(q.enabled)


class TestProgressGcExtra(unittest.TestCase):
    def test_force_archives_all(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "progress.json"
            p.write_text(json.dumps({"finished": [
                {"key": f"o/r{i}", "status": "success", "message": "ok",
                 "time": "2026-09-25 00:00:00"} for i in range(5)],
                "in_progress": {}, "failed": []}), encoding="utf-8")
            st = progress_gc.rotate_progress(p, force=True)
            self.assertEqual(st["trimmed"], 5)
            self.assertEqual(json.loads(p.read_text(encoding="utf-8"))["finished"], [])

    def test_size_threshold_trim(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "progress.json"
            rows = [{"key": f"o/r{i}", "status": "success", "message": "x" * 200,
                     "time": "2026-09-25 00:00:00"} for i in range(300)]
            p.write_text(json.dumps({"finished": rows, "in_progress": {}, "failed": []}),
                         encoding="utf-8")
            st = progress_gc.rotate_progress(p, max_finished=200, max_bytes=1)
            # 超阈值 → 强裁
            self.assertGreaterEqual(st["trimmed"], 100)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertLessEqual(len(data["finished"]), 200)

    def test_archive_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "progress.json"
            p.write_text(json.dumps({"finished": [
                {"key": "o/r", "status": "success", "message": "ok",
                 "time": "2026-09-25 00:00:00"}], "in_progress": {}, "failed": []}),
                encoding="utf-8")
            arch = progress_gc.archive_progress(p)
            self.assertTrue(arch.exists())
            payload = json.loads(arch.read_text(encoding="utf-8"))
            self.assertIn("summary", payload)


if __name__ == "__main__":
    unittest.main()
