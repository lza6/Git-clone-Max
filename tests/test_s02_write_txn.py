"""S-02：DB 写方法异常路径必须 rollback（Rev10 W2 P0）。

此前 `set_tags` / `set_favorite` / `set_note` / `set_excluded` / `delete_repo` /
`delete_all_repos` 六个方法是裸 `_begin_immediate(); execute(); commit()`：
中途抛错时事务悬开，下一次正常写会把这半截写入一起提交（Rev10 审计
用独立连接复现 `multi.tags = 'PARTIAL-WRITE'` 被脏提交）。
修法：统一走 `write_txn`（异常 rollback 再上抛），与 upsert_repo 等 4 处同范式。

测试失败注入用**真实 SQLite 触发器 RAISE**（连接属性只读，且真实约束失败
比 mock 更能代表磁盘满/锁超时/约束冲突这类生产故障）。
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from gcm.db.repo_db import Database
from gcm.models import RepoSpec, SyncAction, SyncStatus


class TestWriteTxnRollback(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "repos.db")

    def tearDown(self):
        self.db.close()

    def _add_repo(self, owner="o", repo="r") -> int:
        spec = RepoSpec(owner, repo, f"https://github.com/{owner}/{repo}")
        return self.db.upsert_repo(spec, local_path=str(self.tmp))

    def _arm_trigger(self, name="trg_fail"):
        """装一个让 repos 表一切 UPDATE/DELETE 都 ABORT 的触发器。"""
        self.db._conn.execute(
            f"CREATE TRIGGER {name} BEFORE UPDATE ON repos "
            "BEGIN SELECT RAISE(ABORT, 'injected-failure'); END;")
        self.db._conn.commit()

    def _disarm_trigger(self, name="trg_fail"):
        self.db._conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        self.db._conn.commit()

    def test_set_tags_failure_rolls_back_and_recovers(self):
        rid = self._add_repo()
        self._arm_trigger()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.set_tags(rid, ["a"])
        self._disarm_trigger()
        # 关键断言：事务已回滚，下一次正常写不被脏数据一起提交
        self.assertFalse(self.db._conn.in_transaction,
                         "set_tags 失败后事务必须已结束（此前会悬开）")
        self.db.set_tags(rid, ["clean"])
        row = self.db._conn.execute(
            "SELECT tags FROM repos WHERE id=?", (rid,)).fetchone()
        self.assertEqual(row["tags"], "clean")

    def test_set_favorite_note_excluded_rollback(self):
        rid = self._add_repo()
        self._arm_trigger()
        for method, args in ((self.db.set_favorite, (rid, True)),
                             (self.db.set_note, (rid, "n")),
                             (self.db.set_excluded, (rid, True))):
            with self.assertRaises(sqlite3.IntegrityError, msg=method.__name__):
                method(*args)
            self.assertFalse(self.db._conn.in_transaction,
                             f"{method.__name__} 失败后事务必须回滚")
        self._disarm_trigger()
        # 解除触发器后全部可正常写入
        self.db.set_favorite(rid, True)
        self.db.set_note(rid, "n")
        self.db.set_excluded(rid, True)

    def test_delete_repo_two_statements_atomic(self):
        rid = self._add_repo()
        self.db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.CLONED)
        self._disarm_trigger()
        # delete_repo 内部两条 DELETE：让第二条失败 → 第一条必须回滚
        # 第二条 DELETE FROM repos 会被 repos 自引用吗？sync_history FK ON DELETE CASCADE
        # 不拦。改用触发器 BEFORE DELETE ON repos。
        self.db._conn.execute(
            "CREATE TRIGGER trg_fail_del BEFORE DELETE ON repos "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END;")
        self.db._conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.delete_repo(rid)
        self.db._conn.execute("DROP TRIGGER trg_fail_del")
        self.db._conn.commit()
        self.assertFalse(self.db._conn.in_transaction)
        row = self.db._conn.execute(
            "SELECT COUNT(*) c FROM sync_history WHERE repo_id=?", (rid,)).fetchone()
        self.assertEqual(row["c"], 1, "第二条失败时第一条（sync_history 删除）必须回滚")
        self.assertIsNotNone(self.db.get_repo("o", "r"), "repos 行必须还在")

    def test_delete_all_repos_rollback(self):
        self._add_repo("a", "b")
        self.db._conn.execute(
            "CREATE TRIGGER trg_fail_del2 BEFORE DELETE ON repos "
            "BEGIN SELECT RAISE(ABORT, 'injected'); END;")
        self.db._conn.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.delete_all_repos()
        self.db._conn.execute("DROP TRIGGER trg_fail_del2")
        self.db._conn.commit()
        self.assertFalse(self.db._conn.in_transaction)
        self.assertEqual(self.db.count(), 1, "失败后数据必须原样保留")

    def test_happy_paths_still_work(self):
        """迁移不得破坏正常路径。"""
        rid = self._add_repo()
        self.db.set_tags(rid, ["t1", "t2"])
        self.db.set_favorite(rid, True)
        self.db.set_note(rid, "note")
        self.db.set_excluded(rid, True)
        self.db.delete_repo(rid)
        self.assertIsNone(self.db.get_repo("o", "r"))


if __name__ == "__main__":
    unittest.main()
