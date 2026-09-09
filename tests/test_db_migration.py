# -*- coding: utf-8 -*-
"""DB 迁移机制测试（对应指南 G06-6）：
- PRAGMA user_version 管理迁移版本
- 旧 v4/v5 库升级：自动加 tags / favorite / excluded 列，不丢数据
- 幂等：重复初始化不再重复迁移
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.repo_db import Database, _migrate, _SCHEMA_VERSION


def _make_legacy_db(path: Path, version: int = 0):
    """构造一个 v5.1 之前的旧库（无 tags/favorite/excluded 列）。"""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE repos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, owner TEXT NOT NULL, repo TEXT NOT NULL,
            folder_name TEXT NOT NULL, url TEXT NOT NULL, local_path TEXT NOT NULL,
            host TEXT NOT NULL DEFAULT 'github.com', default_branch TEXT, head_sha TEXT,
            last_sync_at TEXT, created_at TEXT, updated_at TEXT, UNIQUE(owner, repo, host));
        CREATE TABLE sync_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
            status TEXT NOT NULL, action TEXT NOT NULL, message TEXT, detail TEXT,
            commits INTEGER DEFAULT 0, head_before TEXT, head_after TEXT, remote_head TEXT,
            duration_ms INTEGER, started_at TEXT, ended_at TEXT);
        INSERT INTO repos (owner, repo, folder_name, url, local_path)
            VALUES ('o', 'r', 'o__r', 'https://github.com/o/r.git', '/tmp/o__r');
    """)
    conn.execute(f"PRAGMA user_version={version}")
    conn.commit()
    conn.close()


class TestDbMigration(unittest.TestCase):
    def test_migrate_adds_new_columns(self):
        tmp = Path(tempfile.mkdtemp())
        db_path = tmp / "legacy.db"
        _make_legacy_db(db_path)
        # 迁移
        conn = sqlite3.connect(str(db_path))
        _migrate(conn)
        conn.commit()
        cols = [r[1] for r in conn.execute("PRAGMA table_info(repos)")]
        for col in ("tags", "favorite", "excluded"):
            self.assertIn(col, cols, f"迁移后 repos 应有列 {col}")
        # 数据保留
        row = conn.execute("SELECT owner, repo FROM repos WHERE folder_name='o__r'").fetchone()
        self.assertEqual(row, ("o", "r"))
        conn.close()

    def test_migrate_sets_user_version(self):
        tmp = Path(tempfile.mkdtemp())
        db_path = tmp / "legacy.db"
        _make_legacy_db(db_path)
        conn = sqlite3.connect(str(db_path))
        _migrate(conn)
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(ver, _SCHEMA_VERSION)
        conn.close()

    def test_migrate_idempotent(self):
        tmp = Path(tempfile.mkdtemp())
        db_path = tmp / "legacy.db"
        _make_legacy_db(db_path)
        conn = sqlite3.connect(str(db_path))
        _migrate(conn)
        _migrate(conn)  # 二次迁移不崩、不重复加列
        cols = [r[1] for r in conn.execute("PRAGMA table_info(repos)")]
        self.assertEqual(cols.count("tags"), 1)
        conn.close()

    def test_database_init_runs_migration(self):
        """正常初始化 Database 即触发迁移：旧库升级到新结构。"""
        tmp = Path(tempfile.mkdtemp())
        db_path = tmp / "upgrade.db"
        _make_legacy_db(db_path)
        db = Database(db_path)
        cols = [r[1] for r in db._conn.execute("PRAGMA table_info(repos)")]
        self.assertIn("tags", cols)
        self.assertEqual(db.count(), 1, "迁移不丢数据")
        db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
