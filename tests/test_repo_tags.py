# -*- coding: utf-8 -*-
"""G03 标签/收藏/黑名单 数据层测试（对应指南 G04-3/G04-4/G04-5 的 repo_db 基础）。

- tags：打标签/清标签/按标签过滤
- favorite：收藏标记持久化、列表排序优先
- excluded：黑名单跳过一键更新
以上均基于 v5.2 DB 迁移新增列，返回 dict 含新字段。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.repo_db import Database
from gcm.models import RepoSpec


def _spec(owner="o", repo="r", host="github.com"):
    return RepoSpec(owner, repo, f"https://{host}/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


class TestRepoTags(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "g03.db")

    def tearDown(self):
        self.db.close()

    def test_set_and_clear_tags(self):
        rid = self.db.upsert_repo(_spec("a", "b"), "/x/a__b")
        self.db.set_tags(rid, ["前端", "工具"])
        rec = self.db.get_repo("a", "b")
        self.assertEqual(sorted(rec["tags"].split("|")), ["前端", "工具"])
        # 清空
        self.db.set_tags(rid, [])
        rec2 = self.db.get_repo("a", "b")
        self.assertEqual(rec2["tags"], "")

    def test_list_by_tag(self):
        rid1 = self.db.upsert_repo(_spec("a", "b"), "/x/a__b")
        rid2 = self.db.upsert_repo(_spec("c", "d"), "/x/c__d")
        self.db.set_tags(rid1, ["AI"])
        self.db.set_tags(rid2, ["工具"])
        rows = self.db.list_by_tag("AI")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["owner"], "a")


class TestRepoFavorite(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "g03.db")

    def tearDown(self):
        self.db.close()

    def test_set_favorite_persist(self):
        rid = self.db.upsert_repo(_spec("f", "r"), "/x/f__r")
        self.db.set_favorite(rid, True)
        rec = self.db.get_repo("f", "r")
        self.assertEqual(rec["favorite"], 1)
        self.db.set_favorite(rid, False)
        rec2 = self.db.get_repo("f", "r")
        self.assertEqual(rec2["favorite"], 0)

    def test_list_favorites_first(self):
        r1 = self.db.upsert_repo(_spec("a", "b"), "/x/a__b")
        r2 = self.db.upsert_repo(_spec("c", "d"), "/x/c__d")
        self.db.set_favorite(r2, True)
        rows = self.db.list_repos()
        # 收藏优先：c__d 应在 a__b 之前
        idx_c = next(i for i, r in enumerate(rows) if r["owner"] == "c")
        idx_a = next(i for i, r in enumerate(rows) if r["owner"] == "a")
        self.assertLess(idx_c, idx_a, "收藏仓库应排在前面")


class TestRepoExcluded(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "g03.db")

    def tearDown(self):
        self.db.close()

    def test_set_excluded(self):
        rid = self.db.upsert_repo(_spec("e", "r"), "/x/e__r")
        self.db.set_excluded(rid, True)
        rec = self.db.get_repo("e", "r")
        self.assertEqual(rec["excluded"], 1)
        self.db.set_excluded(rid, False)
        rec2 = self.db.get_repo("e", "r")
        self.assertEqual(rec2["excluded"], 0)

    def test_update_all_excludes_blacklist(self):
        r1 = self.db.upsert_repo(_spec("keep", "a"), "/x/keep__a")
        r2 = self.db.upsert_repo(_spec("skip", "b"), "/x/skip__b")
        self.db.set_excluded(r2, True)
        pending = self.db.list_pending_updates()
        # 黑名单仓库不应出现在待更新列表
        self.assertTrue(all(r["owner"] != "skip" for r in pending))
        self.assertTrue(any(r["owner"] == "keep" for r in pending))


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestManageModelAdvanced(unittest.TestCase):
    """G03-1 管理页过滤 / G03-6 过期仓库高亮（ManageModel 层）。"""

    def test_model_filter(self):
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication
        _app = QApplication.instance() or QApplication([])
        from gcm.ui.manage_model import ManageModel
        rows = [
            {"folder_name": "a__b", "owner": "a", "repo": "b",
             "local_path": "/x/a__b", "last_sync_at": "2026-09-01 10:00:00",
             "head_sha": "abc", "host": "github.com", "url": "u", "id": 1,
             "default_branch": "main", "tags": "AI|工具", "favorite": 1, "excluded": 0},
            {"folder_name": "c__d", "owner": "c", "repo": "d",
             "local_path": "/x/c__d", "last_sync_at": "2020-01-01 00:00:00",
             "head_sha": "def", "host": "github.com", "url": "u2", "id": 2,
             "default_branch": "main", "tags": "", "favorite": 0, "excluded": 0},
        ]
        m = ManageModel(rows)
        self.assertEqual(m.rowCount(), 2)
        # 数据含 tags/favorite/excluded（G03 基础）
        r0 = m.row_at(0)
        self.assertEqual(r0.tags, "AI|工具")
        self.assertEqual(r0.favorite, 1)
        # 过期仓库（>30 天未同步）应能被识别
        stale = m.is_stale(0)   # 2026-09-01 距今 8 天 → 未过期
        stale2 = m.is_stale(1)  # 2020-01-01 距今 > 30 天 → 过期
        self.assertFalse(stale)
        self.assertTrue(stale2)
