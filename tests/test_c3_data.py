"""C3 批守卫（Rev7 §5 C3）：数据层的失败路径。

为什么单独测「失败路径」：正常路径的 CRUD 早有覆盖，但**写事务失败后能不能自愈**
从来没人测过。C3-3 修的就是这个：原来 `upsert_repo` / `add_sync_history` /
`set_repo_head` 是 `BEGIN IMMEDIATE → execute → commit`，**没有 rollback** ——
一次失败会把事务悬在那里，之后的写入要等 `busy_timeout=30s` 才报错，
用户侧只表现为「越用越慢、偶尔卡住」。
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.repo_db import Database  # noqa: E402
from gcm.models import RepoSpec, SyncAction, SyncStatus  # noqa: E402


class _FlakyConn:
    """包一层 sqlite3.Connection：命中指定 SQL 时抛一次错（用于注入失败）。

    不能直接 `conn.execute = ...` —— `sqlite3.Connection.execute` 是 C 层只读属性
    （实测 `AttributeError: attribute 'execute' is read-only`）。
    """

    def __init__(self, conn, needle: str):
        self._conn = conn
        self._needle = needle
        self.fired = False

    def execute(self, sql, *args, **kwargs):
        if not self.fired and isinstance(sql, str) and self._needle in sql:
            self.fired = True
            raise sqlite3.OperationalError("simulated failure")
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _spec(i: int = 1) -> RepoSpec:
    return RepoSpec(owner=f"o{i}", repo=f"r{i}", url_https=f"https://github.com/o{i}/r{i}.git")


class TestWriteTxnRollback(unittest.TestCase):
    def _db(self) -> Database:
        d = Path(tempfile.mkdtemp())
        return Database(d / "repos.db")

    def test_failed_write_rolls_back_and_next_write_works(self):
        """核心断言：写失败后**同一连接的下一次写必须立刻成功**（说明事务没悬挂）。"""
        db = self._db()
        try:
            real_conn = db._conn
            flaky = _FlakyConn(real_conn, "INSERT INTO repos")
            db._conn = flaky            # type: ignore[assignment]
            with self.assertRaises(sqlite3.OperationalError):
                db.upsert_repo(_spec(1), "/tmp/x", host="github.com")
            self.assertTrue(flaky.fired, "前提：异常确实被注入")

            db._conn = real_conn        # type: ignore[assignment]
            # 若事务悬挂，这一句会等 busy_timeout（30s）或报 "cannot start a transaction"
            rid = db.upsert_repo(_spec(2), "/tmp/y", host="github.com")
            self.assertGreater(rid, 0)
            self.assertEqual(len(db.list_repos()), 1, "失败的写入不该留下半条记录")
        finally:
            db.close()

    def test_failed_history_write_does_not_break_later_writes(self):
        db = self._db()
        try:
            rid = db.upsert_repo(_spec(3), "/tmp/z", host="github.com")
            real_conn = db._conn
            flaky = _FlakyConn(real_conn, "INSERT INTO sync_history")
            db._conn = flaky            # type: ignore[assignment]
            with self.assertRaises(sqlite3.OperationalError):
                db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.CLONED)
            db._conn = real_conn        # type: ignore[assignment]

            db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.CLONED)
            self.assertEqual(len(db.history(rid)), 1, "回滚后应能正常写历史")
        finally:
            db.close()

    def test_write_txn_commits_on_success(self):
        db = self._db()
        try:
            from gcm.db.txn import write_txn
            with db._lock, write_txn(db):
                db._conn.execute(
                    "INSERT INTO repos (owner, repo, folder_name, url, local_path, host)"
                    " VALUES ('a','b','a__b','u','/p','github.com')")
            self.assertEqual(len(db.list_repos()), 1, "成功路径必须 commit")
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
