"""S-03：schema 迁移失败不得被「强推 user_version」固化为已完成（Rev10 W2 P0）。

旧行为（Rev10 审计注入失败复现）：磁盘满/锁超时让 `_migrate` 中途抛
OperationalError → except 兜住 → rollback（撤不掉已执行的 DDL，legacy 模式
DDL 走 autocommit）→ `PRAGMA user_version=8` 强推到头 → 之后每次打开都
跳过迁移 = 半迁移库永久不可自愈。

新行为：只有 v8（FTS5 虚表，可接受降级）允许降级推进；其余失败上抛，
`user_version` 只在对应迁移真正成功后写入（v8 成功写 8，v9.. 写到头）。
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from gcm.db.repo_db import _SCHEMA_VERSION, Database, _migrate


def _old_db(tmp: Path, version: int = 3) -> sqlite3.Connection:
    """造一个旧版本库（带 repos 表基线，模拟 v3 用户升级到 v8）。"""
    conn = sqlite3.connect(tmp / "old.db")
    conn.executescript("""
        CREATE TABLE repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner TEXT NOT NULL, repo TEXT NOT NULL,
            folder_name TEXT NOT NULL, url TEXT NOT NULL,
            local_path TEXT NOT NULL, host TEXT NOT NULL DEFAULT 'github.com',
            default_branch TEXT, head_sha TEXT, last_sync_at TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
            UNIQUE(owner, repo, host)
        );
        CREATE TABLE sync_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL, status TEXT NOT NULL, action TEXT NOT NULL,
            message TEXT, detail TEXT, commits INTEGER DEFAULT 0,
            head_before TEXT, head_after TEXT, remote_head TEXT,
            duration_ms INTEGER, started_at TEXT, ended_at TEXT
        );
    """)
    conn.execute(f"PRAGMA user_version={version}")
    conn.commit()
    return conn


class TestMigrationAtomicity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s03_"))
        self.addCleanup(self._cleanup_tmp)

    def _cleanup_tmp(self):
        import gc
        import shutil
        gc.collect()
        try:
            shutil.rmtree(self.tmp, ignore_errors=True)
        except Exception:
            pass

    def test_v8_failure_degrades_but_advances(self):
        """v8（FTS）失败 → 降级为 LIKE，但版本推进（这是**唯一**允许的降级）。"""
        conn = _old_db(self.tmp, version=7)
        conn.executescript("ALTER TABLE repos ADD COLUMN tags TEXT DEFAULT '';"
                           "ALTER TABLE repos ADD COLUMN favorite INTEGER DEFAULT 0;"
                           "ALTER TABLE repos ADD COLUMN excluded INTEGER DEFAULT 0;"
                           "ALTER TABLE repos ADD COLUMN note TEXT DEFAULT '';"
                           "ALTER TABLE repos ADD COLUMN ssh_key TEXT DEFAULT '';")
        conn.execute("PRAGMA user_version=7")
        conn.commit()
        with mock.patch("gcm.db.repo_db.migrate_to_v8",
                        side_effect=sqlite3.OperationalError("no such module: fts5")):
            try:
                _migrate(conn)
            except sqlite3.OperationalError:
                pass  # Database.__init__ 会做降级判定；这里只验 user_version 语义
        # 走 Database 入口验证降级路径
        conn.close()
        with mock.patch("gcm.db.repo_db.migrate_to_v8",
                        side_effect=sqlite3.OperationalError("no such module: fts5")):
            db = Database(self.tmp / "old.db")
            self.addCleanup(db.close)
            self.assertFalse(db._fts_ready, "FTS 失败必须降级 _fts_ready=False")
            v = db._conn.execute("PRAGMA user_version").fetchone()[0]
            self.assertEqual(v, _SCHEMA_VERSION,
                             "FTS 降级后版本应推进（避免每次打开重试建表）")
            # 搜索仍可用（LIKE 路）
            db.upsert_repo(
                __import__("gcm.models", fromlist=["RepoSpec"]).RepoSpec(
                    "o", "r", "https://github.com/o/r"),
                local_path=str(self.tmp))
            self.assertEqual(len(db.list_repos()), 1)

    def test_mid_migration_failure_does_not_pin_version(self):
        """v4（队列表）失败 → user_version 必须停在 3，重开库会**重跑**迁移。"""
        conn = _old_db(self.tmp, version=3)
        conn.close()
        with mock.patch("gcm.db.repo_db.migrate_to_v4",
                        side_effect=sqlite3.OperationalError(
                            "database or disk is full")), \
                self.assertRaises(sqlite3.OperationalError):
            Database(self.tmp / "old.db")

        # 关键：版本没被推到头
        vconn = sqlite3.connect(self.tmp / "old.db")
        v = vconn.execute("PRAGMA user_version").fetchone()[0]
        vconn.close()
        self.assertLess(v, _SCHEMA_VERSION,
                        "中途失败的迁移不得强推 user_version（旧实现会推到头=永久坏库）")
        # 重开库（不注入失败）→ 迁移重跑并成功
        db = Database(self.tmp / "old.db")
        self.addCleanup(db.close)
        v2 = db._conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(v2, _SCHEMA_VERSION, "重开库必须重跑迁移并推进到头")
        self.assertTrue(db._fts_ready)

    def test_user_version_written_only_after_success(self):
        """成功路径：版本 = _SCHEMA_VERSION 且表都在。"""
        conn = _old_db(self.tmp, version=0)
        conn.close()
        db = Database(self.tmp / "old.db")
        self.addCleanup(db.close)
        v = db._conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(v, _SCHEMA_VERSION)
        cols = [r[1] for r in db._conn.execute("PRAGMA table_info(repos)")]
        for c in ("tags", "favorite", "excluded", "note", "ssh_key"):
            self.assertIn(c, cols, f"v1–v3 列缺失：{c}")


if __name__ == "__main__":
    unittest.main()
