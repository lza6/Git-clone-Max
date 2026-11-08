"""S-04：`tag:` 搜索与存储/UI 的分隔符必须同一口径（Rev10 W2 P1）。

三份口径的实测症状：`set_tags` 存 `a|b`，`tag:` 搜索按 `,` 边界匹配，
UI「批量打标签」输入框也按 `,` 拆但整串当单标签写入 → 多标签仓库
`tag:x` 永久零命中（Rev10 审计复现，本文件钉死回归）。
"""
import tempfile
import unittest
from pathlib import Path

from gcm.db.repo_db import Database
from gcm.models import RepoSpec


class TestTagSearchParity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="s04_"))
        self.db = Database(self.tmp / "repos.db")
        rid = self.db.upsert_repo(
            RepoSpec("o", "multi", "https://github.com/o/multi"),
            local_path=str(self.tmp))
        self.db.set_tags(rid, ["web", "data-base", "ml"])
        rid2 = self.db.upsert_repo(
            RepoSpec("x", "legacy", "https://github.com/x/legacy"),
            local_path=str(self.tmp))
        # 历史逗号格式（旧版本/元数据导入可能写入）
        self.db._conn.execute(
            "UPDATE repos SET tags='tool,批量' WHERE id=?", (rid2,))
        self.db._conn.commit()

    def tearDown(self):
        self.db.close()

    def test_pipe_tags_all_searchable(self):
        for t in ("web", "data-base", "ml"):
            self.assertEqual(len(self.db.search_repos(f"tag:{t}")), 1, f"tag:{t}")

    def test_legacy_comma_tags_searchable(self):
        self.assertEqual(len(self.db.search_repos("tag:tool")), 1)

    def test_no_substring_match(self):
        """`tag:da` 不得命中 `data-base`（边界匹配）。"""
        self.assertEqual(self.db.search_repos("tag:da"), [])

    def test_negative_tag(self):
        self.assertEqual(
            [r["repo"] for r in self.db.search_repos("-tag:web")], ["legacy"])

    def test_list_by_tag_pipe_path(self):
        self.assertEqual(len(self.db.list_by_tag("web")), 1)


if __name__ == "__main__":
    unittest.main()
