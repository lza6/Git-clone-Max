"""G80-B7-3 / S1+S2 守卫：sync_queue 持久化 + 调度判定纯函数。

S1 不变量（schema v4）：
- 迁移幂等（重复打开同一库不炸、旧库升级后旧数据完好）；
- QueueStore 的 CRUD + 校验（空名/空 specs/非法 on_missed/非法时间 → ValueError）；
- mark_run 记「跑过」。

S2 不变量（纯函数，注入时钟）：
- 无时间窗 → 随时可跑；同日窗 / 跨零点窗 / 单边窗；
- days 过滤（ISO 1..7）；
- 补跑：非 run_once 永不补 / 当天已跑不补 / 窗口未开始不算错过。
"""
from __future__ import annotations

import datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path

os_kw = {}


class TestSchemaV4(unittest.TestCase):
    def test_fresh_db_gets_v4_and_queue_table(self):
        from gcm.db.repo_db import Database

        with tempfile.TemporaryDirectory() as td:
            db = Database(Path(td) / "repos.db")
            try:
                self.assertEqual(
                    db._conn.execute("PRAGMA user_version").fetchone()[0], 4)
                tables = {r[0] for r in db._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertIn("sync_queue", tables)
            finally:
                db.close()

    def test_v3_db_upgrade_keeps_old_rows(self):
        """造一个 v3 库（无 sync_queue）→ 打开后升级到 v4，旧数据完好。"""
        from gcm.db.repo_db import Database

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "repos.db"
            con = sqlite3.connect(path)
            con.executescript(
                """
                CREATE TABLE repos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT, repo TEXT,
                    folder_name TEXT, url TEXT, local_path TEXT, host TEXT,
                    tags TEXT DEFAULT '', favorite INTEGER DEFAULT 0,
                    excluded INTEGER DEFAULT 0, note TEXT DEFAULT '',
                    ssh_key TEXT DEFAULT '', created_at TEXT, updated_at TEXT
                );
                INSERT INTO repos (owner, repo, folder_name, url)
                    VALUES ('old', 'repo', 'old__repo', 'https://github.com/old/repo');
                PRAGMA user_version=3;
                """
            )
            con.commit()
            con.close()

            db = Database(path)
            try:
                self.assertEqual(db._conn.execute("PRAGMA user_version").fetchone()[0], 4)
                rows = db.list_repos()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["owner"], "old", "旧数据不得在迁移中丢失")
                self.assertIsNotNone(db.queue)
            finally:
                db.close()

    def test_reopen_is_idempotent(self):
        from gcm.db.repo_db import Database

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "repos.db"
            db = Database(path)
            db.close()
            db2 = Database(path)  # 第二次打开走迁移 early-return
            try:
                self.assertEqual(db2._conn.execute("PRAGMA user_version").fetchone()[0], 4)
            finally:
                db2.close()


class TestQueueStoreCRUD(unittest.TestCase):
    def setUp(self):
        from gcm.db.repo_db import Database

        self._td = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._td.name) / "repos.db")

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def test_add_and_list_roundtrip(self):
        specs = [{"owner": "a", "repo": "b", "url_https": "https://github.com/a/b",
                  "folder_name": "a__b"}]
        qid = self.db.queue.add_queue("夜间全量", specs, days="1,3,5",
                                      start="02:00", end="06:00", on_missed="run_once")
        rows = self.db.queue.list_queues()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["id"], qid)
        self.assertEqual(row["name"], "夜间全量")
        self.assertEqual(row["days_set"], [1, 3, 5])
        self.assertEqual(row["specs"][0]["owner"], "a")

    def test_validation_errors(self):
        specs = [{"owner": "a", "repo": "b"}]
        with self.assertRaises(ValueError):
            self.db.queue.add_queue("", specs)
        with self.assertRaises(ValueError):
            self.db.queue.add_queue("x", [])
        with self.assertRaises(ValueError):
            self.db.queue.add_queue("x", specs, on_missed="retry_forever")
        with self.assertRaises(ValueError):
            self.db.queue.add_queue("x", specs, start="25:00")

    def test_empty_days_means_everyday(self):
        self.db.queue.add_queue("x", [{"owner": "a", "repo": "b"}], days="")
        self.assertEqual(self.db.queue.list_queues()[0]["days_set"], [1, 2, 3, 4, 5, 6, 7])

    def test_mark_run_and_toggle(self):
        specs = [{"owner": "a", "repo": "b"}]
        qid = self.db.queue.add_queue("q", specs)
        self.db.queue.set_enabled(qid, False)
        self.assertEqual(self.db.queue.get_queue(qid)["enabled"], 0)
        self.db.queue.mark_run(qid, "success")
        row = self.db.queue.get_queue(qid)
        self.assertEqual(row["last_status"], "success")
        self.assertNotEqual(row["last_run_at"], "", "mark_run 必须记录「跑过」")


class TestSchedulerPureFunctions(unittest.TestCase):
    def setUp(self):
        from gcm.app.queue_scheduler import is_due, missed_run_allowed

        self.is_due = is_due
        self.missed = missed_run_allowed
        self.everyday = {1, 2, 3, 4, 5, 6, 7}

    def test_no_window_means_always_due(self):
        now = datetime.datetime(2026, 10, 25, 23, 59)
        self.assertTrue(self.is_due(self.everyday, "", "", now))

    def test_same_day_window(self):
        now = datetime.datetime(2026, 10, 25, 3, 0)
        self.assertTrue(self.is_due(self.everyday, "02:00", "06:00", now))
        self.assertFalse(self.is_due(self.everyday, "02:00", "06:00",
                                     datetime.datetime(2026, 10, 25, 7, 0)))

    def test_window_boundaries_are_half_open(self):
        # [02:00, 06:00)：02:00 算、06:00 不算
        self.assertTrue(self.is_due(self.everyday, "02:00", "06:00",
                                    datetime.datetime(2026, 10, 25, 2, 0)))
        self.assertFalse(self.is_due(self.everyday, "02:00", "06:00",
                                     datetime.datetime(2026, 10, 25, 6, 0)))

    def test_overnight_window(self):
        # 22:00–06:00：23:59 与 00:01 都算，12:00 不算
        late = datetime.datetime(2026, 10, 25, 23, 59)
        early = datetime.datetime(2026, 10, 26, 0, 1)
        noon = datetime.datetime(2026, 10, 26, 12, 0)
        self.assertTrue(self.is_due(self.everyday, "22:00", "06:00", late))
        self.assertTrue(self.is_due(self.everyday, "22:00", "06:00", early))
        self.assertFalse(self.is_due(self.everyday, "22:00", "06:00", noon))

    def test_day_filter(self):
        monday = datetime.datetime(2026, 10, 26, 3, 0)   # ISO 周一=1
        sunday = datetime.datetime(2026, 11, 1, 3, 0)    # ISO 周日=7
        self.assertTrue(self.is_due({1}, "02:00", "06:00", monday))
        self.assertFalse(self.is_due({1}, "02:00", "06:00", sunday))

    def test_missed_run_rules(self):
        # 非 run_once → 永不补
        self.assertFalse(self.missed(self.everyday, "02:00", "skip", None,
                                     datetime.datetime(2026, 10, 25, 9, 0)))
        # 窗口还没开始 → 不算错过
        self.assertFalse(self.missed(self.everyday, "23:00", "run_once", None,
                                     datetime.datetime(2026, 10, 25, 9, 0)))
        # 窗口已过、今天没跑 → 补
        self.assertTrue(self.missed(self.everyday, "02:00", "run_once", None,
                                    datetime.datetime(2026, 10, 25, 9, 0)))
        # 今天已经跑过 → 不再补（防重复）
        self.assertFalse(self.missed(self.everyday, "02:00", "run_once",
                                     "2026-10-25 08:00:00",
                                     datetime.datetime(2026, 10, 25, 9, 0)))

    def test_invalid_hhmm_treated_as_no_bound(self):
        """脏时间串按「无界」处理（宁可多跑一次，不可整队列失效）。"""
        now = datetime.datetime(2026, 10, 25, 12, 0)
        self.assertTrue(self.is_due(self.everyday, "oops", "06:00", now))


if __name__ == "__main__":
    unittest.main()
