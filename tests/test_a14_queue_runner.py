"""A-1-4（覆盖率收口）：QueueRunner 全链路测试。

queue_runner.py 是 sync_queue → 引擎批次的编排层，此前 0% 覆盖
（test_g80_queue 只测了 queue_db / queue_scheduler 两层）。
本文件补齐：due 判定 / tick 启动 / busy 让路 / 失败隔离 / 开关注册。
"""
from __future__ import annotations

import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app import background_jobs
from gcm.app.queue_runner import JOB_SYNC_QUEUE, QueueRunner
from gcm.db.repo_db import Database

NOW = datetime.datetime(2026, 10, 31, 3, 0)  # 周六 03:00


def _row(store, name="夜间全量", specs=None, *, empty=False, **kw):
    if empty:
        specs = []
        qid = store.add_queue("empty", [{"owner": "a", "repo": "b"}], **kw)
        # add_queue 拒绝空 specs（guard：specs 必须非空），此处模拟"反序列化后为空"
        with store._lock:
            store._conn.execute("UPDATE sync_queue SET specs_json='[]' WHERE id=?", (qid,))
            store._conn.commit()
    else:
        qid = store.add_queue(name, specs or [{"owner": "a", "repo": "b"}], **kw)
    return qid, store.get_queue(qid)


class TestQueueRunner(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "t.db")
        self.store = self.db.queue
        self.launched: list[list[dict]] = []
        self.busy = False
        self.now = NOW

    def tearDown(self):
        self.db.close()
        background_jobs.registry().reset()

    def _runner(self, **kw):
        defaults = dict(
            store=self.store,
            get_settings=lambda: {},
            launch=self.launched.append,
            is_busy=lambda: self.busy,
            now_fn=lambda: self.now,
            tick_interval_sec=60.0,
        )
        defaults.update(kw)
        return QueueRunner(**defaults)

    # ---------------------------------------------------------------- due 判定
    def test_disabled_row_never_due(self):
        _, row = _row(self.store)
        r = self._runner()
        self.store.set_enabled(row["id"], False)
        self.assertEqual(r.due_queues(), [])

    def test_enabled_but_wrong_day_not_due(self):
        # NOW 是周六(6)，只排周一
        _row(self.store, days="1")
        r = self._runner()
        self.assertEqual(r.due_queues(), [])

    def test_due_within_window(self):
        _row(self.store, days="1,2,3,4,5,6,7", start="02:00",
             end="06:00")
        r = self._runner()
        due = r.due_queues()
        self.assertEqual(len(due), 1)
        self.assertEqual(due[0]["name"], "夜间全量")

    def test_catch_up_once_after_window(self):
        # 窗口 00:00-01:00 已过，on_missed=run_once 且今天没跑 → 补跑
        _row(self.store, days="1,2,3,4,5,6,7", start="00:00",
             end="01:00", on_missed="run_once")
        r = self._runner()
        self.assertEqual(len(r.due_queues()), 1)
        # 跑过后当天不再补（mark_run 落 DB 真实时钟，与注入假时钟错位，
        # 故直接写 last_run_at 为假时钟今天 —— 判定只看日期前缀）
        with self.store._lock:
            self.store._conn.execute(
                "UPDATE sync_queue SET last_run_at=? WHERE id=?",
                (self.now.strftime("%Y-%m-%d %H:%M"), 1))
            self.store._conn.commit()
        self.assertEqual(r.due_queues(), [])

    # ---------------------------------------------------------------- tick
    def test_tick_disabled_returns_zero(self):
        _row(self.store)
        self.assertEqual(self._runner().tick(), 0)

    def test_tick_busy_yields(self):
        _row(self.store)
        self.busy = True
        r = self._runner()
        r.set_enabled(True)
        self.assertEqual(r.tick(), 0)
        self.assertEqual(self.launched, [])

    def test_tick_busy_exception_treated_as_busy(self):
        _row(self.store)
        r = self._runner(is_busy=mock.Mock(side_effect=RuntimeError("boom")))
        r.set_enabled(True)
        self.assertEqual(r.tick(), 0)
        self.assertEqual(self.launched, [])

    def test_tick_launches_and_marks_run(self):
        qid, _ = _row(self.store)
        r = self._runner()
        r.set_enabled(True)
        self.assertEqual(r.tick(), 1)
        self.assertEqual(len(self.launched[0]), 1)
        self.assertEqual(self.store.get_queue(qid)["last_status"], "dispatched")

    def test_tick_empty_specs_skipped(self):
        qid, _ = _row(self.store, empty=True)
        r = self._runner()
        r.set_enabled(True)
        self.assertEqual(r.tick(), 0)
        self.assertEqual(self.store.get_queue(qid)["last_status"], "")

    def test_tick_launch_failure_isolated_and_marked(self):
        qid_ok, _ = _row(self.store, name="ok")
        qid_bad, _ = _row(self.store, name="bad")
        order = [qid_ok, qid_bad]  # due 按 id 升序
        seen = []

        def launch(specs):
            seen.append(specs)
            if len(seen) == 1:  # 第一个（ok）抛错
                raise RuntimeError("boom")
            self.launched.append(specs)

        r = self._runner(launch=launch)
        r.set_enabled(True)
        self.assertEqual(r.tick(), 1)
        self.assertIn("error:", self.store.get_queue(qid_ok)["last_status"])
        self.assertEqual(self.store.get_queue(qid_bad)["last_status"],
                         "dispatched")

    def test_attach_timer_start_stop(self):
        _row(self.store)
        timer = mock.Mock()
        r = self._runner()
        r.attach_timer(timer)
        r.set_enabled(True)
        timer.start.assert_called_once()
        r.set_enabled(False)
        timer.stop.assert_called_once()
        self.assertFalse(r.enabled)


if __name__ == "__main__":
    unittest.main()
