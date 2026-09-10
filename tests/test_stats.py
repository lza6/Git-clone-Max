# -*- coding: utf-8 -*-
"""G03-8 详情统计 + G07-1 统计中心 数据层测试。

- Database.stats_for_repo(repo_id)：单仓库聚合（次数/成功/失败/冲突/取消/平均耗时）
- Database.stats_overview()：全局聚合（总仓库/总同步/各 host 分布/近 30 天成功失败）
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.repo_db import Database
from gcm.models import RepoSpec, SyncAction, SyncStatus


def _spec(owner, repo):
    return RepoSpec(owner, repo, f"https://github.com/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


class TestRepoStats(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "stats.db")

    def tearDown(self):
        self.db.close()

    def test_stats_for_repo(self):
        rid = self.db.upsert_repo(_spec("s", "r"), "/x/s__r")
        self.db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.CLONED,
                                 "ok", duration_ms=1000)
        self.db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.UPDATED,
                                 "ok", duration_ms=3000)
        self.db.add_sync_history(rid, SyncStatus.FAILED, SyncAction.FAILED,
                                 "fail", duration_ms=2000)
        self.db.add_sync_history(rid, SyncStatus.CONFLICT, SyncAction.CONFLICT,
                                 "conf", duration_ms=4000)
        st = self.db.stats_for_repo(rid)
        self.assertEqual(st["total"], 4)
        self.assertEqual(st["success"], 2)
        self.assertEqual(st["failed"], 1)
        self.assertEqual(st["conflict"], 1)
        self.assertEqual(st["avg_duration_ms"], 2500)  # (1000+3000+2000+4000)/4

    def test_stats_empty(self):
        rid = self.db.upsert_repo(_spec("e", "mpty"), "/x/e__mpty")
        st = self.db.stats_for_repo(rid)
        self.assertEqual(st["total"], 0)
        self.assertEqual(st["avg_duration_ms"], 0)

    def test_stats_overview(self):
        rid1 = self.db.upsert_repo(_spec("o1", "a"), "/x/o1__a")
        rid2 = self.db.upsert_repo(_spec("o2", "b"), "/x/o2__b")
        self.db.add_sync_history(rid1, SyncStatus.SUCCESS, SyncAction.CLONED, "ok", duration_ms=100)
        self.db.add_sync_history(rid2, SyncStatus.FAILED, SyncAction.FAILED, "bad", duration_ms=200)
        ov = self.db.stats_overview()
        self.assertEqual(ov["total_repos"], 2)
        self.assertEqual(ov["total_syncs"], 2)
        self.assertEqual(ov["success_syncs"], 1)
        self.assertEqual(ov["failed_syncs"], 1)
        # host 分布
        self.assertGreaterEqual(ov["by_host"].get("github.com", 0), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
