"""S-06：定时同步队列窗口内不得重复启动（Rev10 W2 P1）。

旧行为：`due_queues` 的到点分支只看 `is_due`（周几+时间窗），完全不看
`last_run_at` —— tick 默认 60 秒一次，窗口几小时 → 同一批在窗口内被
重复启动几十次（Rev10 审计判定，本文件真实时钟复现）。
修法：到点分支加 `already_ran_in_window_today` 闸（上次启动时刻落在本窗内
→ 今天不再 due）。
"""
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from gcm.app.queue_runner import QueueRunner
from gcm.app.queue_scheduler import already_ran_in_window_today
from gcm.db.queue_db import QueueStore, migrate_to_v4


def _mkstore(tmp: Path) -> tuple[QueueStore, sqlite3.Connection]:
    conn = sqlite3.connect(tmp / "q.db")
    conn.row_factory = sqlite3.Row
    migrate_to_v4(conn)
    return QueueStore(conn, threading.RLock()), conn


class _StoreShim:
    def __init__(self, store):
        self.store = store

    def list_queues(self):
        return self.store.list_queues()

    def mark_run(self, *a, **k):
        self.store.mark_run(*a, **k)


class TestNoDoubleLaunch(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="s06_")
        self.addCleanup(self._tmp.cleanup)
        self.store, self._conn = _mkstore(Path(self._tmp.name))
        self.addCleanup(self._conn.close)
        self.launched = []
        self.runner = QueueRunner(
            store=_StoreShim(self.store), get_settings=lambda: None,
            launch=lambda specs: self.launched.append(specs) or 1,
            is_busy=lambda: False)
        self.runner._enabled = True

    def test_same_window_no_relaunch(self):
        """真实时钟：窗口覆盖全天，tick 两次，第二次必须 0。"""
        self.store.add_queue(
            "q", [{"url": "https://github.com/o/r", "folder_name": "r"}],
            days="1,2,3,4,5,6,7", start="00:00", end="23:59")
        self.assertEqual(self.runner.tick(), 1)
        self.assertEqual(self.runner.tick(), 0,
                         "同窗第二次 tick 不得重复启动（S-06）")
        self.assertEqual(len(self.launched), 1)

    def test_different_window_still_launches(self):
        """跑过上午窗不挡下午窗（跨零点窗语义）：helper 只认本窗内记录。"""
        import datetime as _dt
        # 用固定 now_fn 避免依赖真实时钟
        now = _dt.datetime(2026, 11, 8, 10, 0)
        self.runner._now_fn = lambda: now
        self.store.add_queue(
            "q", [{"url": "https://github.com/o/r", "folder_name": "r"}],
            days="1,2,3,4,5,6,7", start="09:00", end="11:00")
        # 模拟「今天 09:30 跑过一次」：last_run_at 在本窗内
        self.store.mark_run(1, "dispatched")
        # mark_run 写的是真实时钟；把行改到 09:30
        self._conn.execute(
            "UPDATE sync_queue SET last_run_at='2026-11-08 09:30:00' WHERE id=1")
        self._conn.commit()
        self.assertEqual(self.runner.tick(), 0, "同窗已跑过 → 不再启动")
        # 现在切到第二个窗（13:00-14:00）：上次 09:30 不在这个窗里 → 允许跑
        self._conn.execute(
            "UPDATE sync_queue SET start_time='09:59', end_time='23:59' WHERE id=1")
        self._conn.commit()
        self.assertEqual(self.runner.tick(), 1,
                         "上次启动不在当前窗内 → 允许启动（跨窗不误伤）")

    def test_helper_boundary(self):
        self.assertTrue(already_ran_in_window_today(
            {1}, "09:00", "11:00", "2026-11-08 10:00:00",
            __import__("datetime").datetime(2026, 11, 8, 10, 1)))
        self.assertFalse(already_ran_in_window_today(
            {1}, "09:00", "11:00", "2026-11-08 07:49:00",
            __import__("datetime").datetime(2026, 11, 8, 10, 0)),
            "07:49 不在 09-11 窗内，不算已跑")
        self.assertFalse(already_ran_in_window_today(
            {1}, "09:00", "11:00", "2026-11-07 10:00:00",
            __import__("datetime").datetime(2026, 11, 8, 10, 0)),
            "昨天的记录不算")


if __name__ == "__main__":
    unittest.main()
