"""S-08 后台执行器守卫：DB 写迁出 GUI 线程。

- DbWriteExecutor：单线程保序、关停后降级原地执行、任务异常不外溢；
- 引擎接线：_on_result 的落库走 executor；discard/shutdown 冲刷。
"""
from __future__ import annotations

import os
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402


class TestDbWriteExecutor(unittest.TestCase):
    def test_submitted_tasks_run_in_order(self):
        from gcm.app.db_executor import DbWriteExecutor
        ex = DbWriteExecutor()
        seen = []
        done = threading.Event()

        def job():
            seen.append(1)

        def job2():
            seen.append(2)
            done.set()

        ex.submit(job)
        ex.submit(job2)
        self.assertTrue(done.wait(5.0))
        ex.shutdown(wait=True)
        self.assertEqual(seen, [1, 2])  # 保序
        self.assertIsNone(ex._pool)     # 已关停

    def test_submit_after_shutdown_runs_inline(self):
        from gcm.app.db_executor import DbWriteExecutor
        ex = DbWriteExecutor()
        ex.shutdown(wait=True)
        marker = []

        def job():
            marker.append("ran")

        fut = ex.submit(job)
        self.assertIsNone(fut)          # 降级路径无 Future
        self.assertEqual(marker, ["ran"])

    def test_task_exception_does_not_break_queue(self):
        from gcm.app.db_executor import DbWriteExecutor
        ex = DbWriteExecutor()
        done = threading.Event()

        def bad():
            raise RuntimeError("boom")

        def good():
            done.set()

        ex.submit(bad)
        ex.submit(good)
        self.assertTrue(done.wait(5.0))
        ex.shutdown(wait=True)


class TestEngineExecutorWiring(unittest.TestCase):
    def test_engine_uses_executor_and_flushes_on_discard(self):
        import tempfile
        from gcm.app.engine import SyncEngine
        from gcm.models import RepoSpec, SyncResult, SyncStatus
        from gcm.db.repo_db import Database
        d = Path(tempfile.mkdtemp())
        db = Database(d / "repos.db")
        eng = SyncEngine(d / "clones", concurrency=2, db=db)
        spec = RepoSpec(owner="o", repo="r", url_https="https://github.com/o/r")
        res = SyncResult(spec=spec, status=SyncStatus.SUCCESS,
                         action=__import__("gcm.models", fromlist=["SyncAction"]).SyncAction.CLONED,
                         message="ok", duration_ms=10)
        eng._on_result(0, res)
        # discard 必须冲刷 executor（shutdown 后 _pool 为 None）
        eng.discard()
        self.assertIsNone(eng._db_executor._pool)
        # 冲刷后结果应已落库（executor 保序完成）
        rows = eng.db.list_repos()
        self.assertEqual(len(rows), 1)
        eng.db.close()


if __name__ == "__main__":
    unittest.main()
