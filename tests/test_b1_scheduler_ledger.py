# -*- coding: utf-8 -*-
"""B1 调度账本测试：半开边界 / 补跑宽限 / 确定性槽位 / INSERT OR IGNORE / 迁移 v9。"""
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

from gcm.app.queue_runner import QueueRunner
from gcm.app.queue_scheduler import (already_ran_in_window_today,
                                     missed_run_allowed, window_slot)
from gcm.db.repo_db import Database

NOW = datetime.datetime(2026, 10, 31, 3, 0)  # 周六 03:00


class TestBoundaryAndGrace(unittest.TestCase):
    def test_already_ran_is_half_open(self):
        """恰在 end 时刻的启动属于窗外 —— 与 is_due 的 [start, end) 对齐。"""
        now = datetime.datetime(2026, 11, 8, 12, 0)
        self.assertFalse(already_ran_in_window_today(
            {7}, "09:00", "11:00", "2026-11-08 11:00:00", now),
            "11:00 记录不应算作本窗已跑（半开区间）")
        self.assertTrue(already_ran_in_window_today(
            {7}, "09:00", "11:00", "2026-11-08 10:59:00", now))

    def test_missed_run_grace_upper_bound(self):
        # 窗口 00:00-01:00；03:00 距窗口结束 2h → 允许补
        self.assertTrue(missed_run_allowed(
            {6}, "00:00", "run_once", "", NOW, end="01:00"),
            "窗口结束 2 小时内应允许补跑")
        # 14:00 距结束 13h → 超过 6h 宽限 → 丢弃
        late = datetime.datetime(2026, 10, 31, 14, 0)
        self.assertFalse(missed_run_allowed(
            {6}, "00:00", "run_once", "", late, end="01:00"),
            "超过补跑宽限应判为过期计划")

    def test_missed_run_without_end_keeps_legacy(self):
        self.assertTrue(missed_run_allowed(
            {6}, "02:00", "run_once", "", NOW))

    def test_cross_midnight_catchup_within_grace(self):
        """22:00–06:00 的窗口在 07:00 应允许补跑（R2：按开始日=周五归属）。"""
        morning = datetime.datetime(2026, 10, 31, 7, 0)  # 周六
        self.assertTrue(missed_run_allowed(
            {5}, "22:00", "run_once", "", morning, end="06:00"),
            "跨零点窗（周五夜）结束后 1h 内应允许补跑")
        afternoon = datetime.datetime(2026, 10, 31, 13, 0)
        self.assertFalse(missed_run_allowed(
            {5}, "22:00", "run_once", "", afternoon, end="06:00"),
            "跨零点窗结束 7h 后超过宽限应丢弃")
        inside = datetime.datetime(2026, 10, 31, 5, 0)
        self.assertFalse(missed_run_allowed(
            {5}, "22:00", "run_once", "", inside, end="06:00"),
            "仍在窗内时不属于补跑（由 is_due 负责启动）")
        # 周几按窗口开始日算：周六凌晨属于周五夜，days={6}（周六）不补
        self.assertFalse(missed_run_allowed(
            {6}, "22:00", "run_once", "", morning, end="06:00"),
            "窗口开始日是周五 → days={6} 不含它")

    def test_overnight_days_follow_start_day(self):
        """R2 MAJOR：跨零点窗的周几按开始日算（周六 01:00 属于周五夜）。"""
        from gcm.app.queue_scheduler import is_due
        self.assertTrue(is_due({5}, "22:00", "06:00",
                               datetime.datetime(2026, 10, 31, 1, 0)),
                        "周六凌晨属于周五(5)夜窗口")
        self.assertFalse(is_due({6}, "22:00", "06:00",
                                datetime.datetime(2026, 10, 30, 1, 0)),
                         "周五凌晨属于周四夜，不在周六集合")
        self.assertTrue(is_due({6}, "22:00", "06:00",
                               datetime.datetime(2026, 10, 31, 22, 30)),
                        "周六晚上开始的窗口属于周六")

    def test_window_slot_after_end_belongs_to_previous_day(self):
        """R2 MAJOR：07:00 属于昨晚窗口 —— 槽位日期必须是昨天，否则 07:00
        的补跑会占用当晚 22:00 窗口的槽位，把整个夜间窗口静默跳过。"""
        key, scheduled = window_slot(3, "22:00", "06:00",
                                     datetime.datetime(2026, 10, 31, 7, 0))
        self.assertTrue(key.startswith("3:2026-10-30:"), key)
        self.assertIn("2026-10-30", scheduled)
        noon_key, _ = window_slot(3, "22:00", "06:00",
                                  datetime.datetime(2026, 10, 31, 12, 0))
        self.assertEqual(key, noon_key, "窗后白天仍归最近一次（昨晚）窗口")

    def test_window_slot_deterministic_within_window(self):
        a = window_slot(7, "02:00", "06:00", datetime.datetime(2026, 10, 31, 3, 0))
        b = window_slot(7, "02:00", "06:00", datetime.datetime(2026, 10, 31, 5, 59))
        self.assertEqual(a, b, "同一窗口内不同 tick 必须得到同一槽位键")
        self.assertEqual(a[1], "2026-10-31T02:00:00")

    def test_window_slot_overnight_belongs_to_previous_day(self):
        # 22:00–06:00 的凌晨 01:00 属于「前一天开启的窗口」
        early = window_slot(3, "22:00", "06:00",
                            datetime.datetime(2026, 11, 1, 1, 0))
        late = window_slot(3, "22:00", "06:00",
                           datetime.datetime(2026, 10, 31, 23, 0))
        self.assertEqual(early[0], late[0],
                         "跨零点窗的凌晨与当晚必须同槽位（否则重复启动）")
        self.assertIn("2026-10-31", early[1])

    def test_window_slot_no_window_uses_day(self):
        key, scheduled = window_slot(5, "", "", NOW)
        self.assertIn("2026-10-31", key)
        self.assertEqual(scheduled, "2026-10-31T00:00:00")


class TestLedgerStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="b1_ledger_")
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.addCleanup(self.db.close)
        self.store = self.db.queue

    def test_schema_v9_applied(self):
        ver = self.db._conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(ver, 9)
        cols = [r[1] for r in self.db._conn.execute(
            "PRAGMA table_info(scheduled_runs)")]
        self.assertIn("slot_key", cols)

    def test_claim_is_idempotent_and_releasable(self):
        self.assertTrue(self.store.claim_slot(1, "k1", "2026-10-31T02:00:00", "window"))
        self.assertFalse(self.store.claim_slot(1, "k1", "2026-10-31T02:00:00", "window"),
                         "同一槽位第二次抢占必须失败")
        self.assertEqual(len(self.store.list_slots(1)), 1)
        self.store.release_slot(1, "k1")
        self.assertTrue(self.store.claim_slot(1, "k1", "2026-10-31T02:00:00", "window"),
                        "释放后允许重试")

    def test_reopen_keeps_version(self):
        self.db.close()
        again = Database(Path(self.tmp.name) / "t.db")
        try:
            ver = again._conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(ver, 9)
        finally:
            again.close()


class TestRunnerLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="b1_runner_")
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "t.db")
        self.addCleanup(self.db.close)
        self.store = self.db.queue
        self.launched: list[list] = []
        self.now = NOW

    def _runner(self, launch=None):
        return QueueRunner(
            store=self.store, get_settings=lambda: {},
            launch=launch or (lambda specs: self.launched.append(specs)),
            is_busy=lambda: False, now_fn=lambda: self.now)

    def test_catchup_claims_single_slot_across_ticks(self):
        """补跑路径：连续 tick 只能启动一次（槽位主键拦死，不靠 last_run 反推）。"""
        self.store.add_queue(
            "q", [{"url": "https://github.com/o/r", "folder_name": "r"}],
            days="6", start="00:00", end="01:00", on_missed="run_once")
        r = self._runner()
        r.set_enabled(True)
        self.assertEqual(r.tick(), 1)
        self.assertEqual(r.tick(), 0, "补跑过同一窗口后不得再启动")
        self.assertEqual(len(self.launched), 1)
        slots = self.store.list_slots(1)
        self.assertEqual(len(slots), 1)
        self.assertEqual(slots[0]["trigger"], "catchup")
        self.assertEqual(slots[0]["state"], "done", "成功启动后槽位状态应推进")
        self.assertTrue(slots[0]["run_id"], "run_id 应真实落库（审计可定位）")

    def test_launch_failure_blocks_window_without_storm(self):
        """启动失败：保留槽位并按「本窗口不再重试」处理（MAJOR-3 定案）。

        真正拦截重试的是**账本槽位主键**（R2 实测：`mark_run` 写真实时钟，
        与注入时钟不同源，本用例里它拦不住重跑；把槽位行删掉重跑，launch
        会再次被调用 → 本用例的 calls 断言在「代码改回 release_slot+重试」
        时变红，机制被锁定）。
        """
        self.store.add_queue(
            "q", [{"url": "https://github.com/o/r", "folder_name": "r"}],
            days="6", start="00:00", end="01:00", on_missed="run_once")
        calls = {"n": 0}

        def launch(specs):
            calls["n"] += 1
            raise RuntimeError("boom")

        r = self._runner(launch=launch)
        r.set_enabled(True)
        self.assertEqual(r.tick(), 0, "启动失败不计数")
        self.assertIn("error:",
                      self.store.get_queue(1)["last_status"])
        slots = self.store.list_slots(1)
        self.assertEqual(len(slots), 1, "失败也保留槽位（可审计）")
        self.assertEqual(slots[0]["state"], "claimed", "未成功则状态停在 claimed")
        self.assertEqual(r.tick(), 0, "同一窗口失败后不得每 tick 重试（防风暴）")
        self.assertEqual(calls["n"], 1, "launch 只应被调用一次（改回重试会变 2）")

    def test_catchup_does_not_block_tonight_window(self):
        """R2 MAJOR 场景 A：07:00 补跑昨夜窗口后，今晚 22:00 必须照常发车。"""
        self.store.add_queue(
            "q", [{"url": "https://github.com/o/r", "folder_name": "r"}],
            days="1,2,3,4,5,6,7", start="22:00", end="06:00",
            on_missed="run_once")
        r = self._runner()
        r.set_enabled(True)
        self.now = datetime.datetime(2026, 10, 31, 7, 0)
        self.assertEqual(r.tick(), 1, "07:00 应补跑昨晚窗口")
        self.now = datetime.datetime(2026, 10, 31, 22, 0)
        self.assertEqual(r.tick(), 1, "今晚窗口不得被补跑槽位挡掉")
        self.now = datetime.datetime(2026, 11, 1, 1, 0)
        self.assertEqual(r.tick(), 0, "同一夜窗已发车，凌晨不得重复")
        self.assertEqual(len(self.store.list_slots(1)), 2,
                         "两个窗口实例 = 两个槽位（补跑记昨天）")

    def test_early_morning_run_does_not_block_tonight(self):
        """R2 MAJOR 场景 B：凌晨 01:00 跑过（昨夜窗口尾部），当晚 22:00 照常。"""
        self.store.add_queue(
            "q", [{"url": "https://github.com/o/r", "folder_name": "r"}],
            days="1,2,3,4,5,6,7", start="22:00", end="06:00",
            on_missed="run_once")
        self.db._conn.execute(
            "UPDATE sync_queue SET last_run_at='2026-11-01 01:00:00' WHERE id=1")
        self.db._conn.commit()
        r = self._runner()
        r.set_enabled(True)
        self.now = datetime.datetime(2026, 11, 1, 22, 0)
        self.assertEqual(r.tick(), 1,
                         "凌晨跑过不等于今晚窗口已跑（旧实现会误拦）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
