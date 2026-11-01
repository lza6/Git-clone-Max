"""C4 守卫：双路检索（FTS5 trigram + LIKE 兜底）+ 查询语法 + 索引可重建。

**为什么双路是硬要求**（Rev7 §2.1 的实测修正）：FTS5 的 `trigram` 分词要求查询串
**≥3 个 unicode 字符**，不足则 match nothing。中文最常搜的两字词（克隆/爬虫/学习）
在纯 FTS 下**零命中** —— 上线当天就会被报「搜索坏了」。所以：
`len(q)>=3` 走 FTS（bm25 排序），`len(q)<3` 走 LIKE 下推（3000 行实测 3.9ms）。
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.repo_db import Database  # noqa: E402
from gcm.db.repo_query import parse_query  # noqa: E402
from gcm.models import RepoSpec  # noqa: E402

_SEED = [
    ("lza6", "Git-clone-Max", "tool,批量", "日常用的批量克隆工具"),
    ("acme", "machine-learning", "ml,笔记", "机器学习实战教程"),
    ("x", "crawler", "tool", "爬虫与量化笔记"),
]


def _db() -> Database:
    d = Path(tempfile.mkdtemp(prefix="gcm_c4_"))
    db = Database(d / "repos.db")
    for owner, repo, tags, note in _SEED:
        rid = db.upsert_repo(
            RepoSpec(owner=owner, repo=repo,
                     url_https=f"https://github.com/{owner}/{repo}"),
            f"/tmp/{repo}")
        db._conn.execute(
            "UPDATE repos SET tags=?, note=? WHERE id=?", (tags, note, rid))
    db._conn.commit()
    db.rebuild_fts()
    return db


class TestQueryParser(unittest.TestCase):
    """C4-3：纯函数解析器 —— 非法输入必须**报错**，不能静默忽略。"""

    def test_plain_text(self):
        q = parse_query("  machine  learning ")
        self.assertEqual(q.text, "machine learning")
        self.assertTrue(q.ok)

    def test_fields(self):
        q = parse_query("tag:python host:gitlab.com favorite:1 updated:<30d 爬虫")
        self.assertEqual(q.tags, ["python"])
        self.assertEqual(q.hosts, ["gitlab.com"])
        self.assertTrue(q.favorite)
        self.assertEqual(q.updated_within_days, 30)
        self.assertEqual(q.text, "爬虫")
        self.assertTrue(q.ok)

    def test_negation(self):
        q = parse_query("tag:tool -tag:archive -host:gitlab.com")
        self.assertEqual(q.tags, ["tool"])
        self.assertEqual(q.neg_tags, ["archive"])
        self.assertEqual(q.neg_hosts, ["gitlab.com"])

    def test_updated_older(self):
        self.assertEqual(parse_query("updated:>7d").updated_older_days, 7)

    def test_unsupported_fields_are_reported(self):
        """`lang:`/`size:`/`archived:` 必须报错 —— 静默忽略会让用户以为筛选生效了。"""
        for raw in ("lang:go", "size:>10MB", "archived:1"):
            with self.subTest(raw=raw):
                q = parse_query(raw)
                self.assertFalse(q.ok)
                self.assertTrue(any("不支持" in e for e in q.errors))

    def test_unknown_field_is_reported(self):
        self.assertFalse(parse_query("foo:bar").ok)

    def test_bad_updated_syntax(self):
        self.assertFalse(parse_query("updated:30").ok)


class TestDualPathSearch(unittest.TestCase):
    """C4-1：两字走 LIKE、三字走 FTS，两条路返回同形状的行。"""

    def setUp(self):
        self.db = _db()

    def tearDown(self):
        self.db.close()

    def test_two_char_chinese_uses_like_and_hits(self):
        """核心回归：两字中文在 FTS 下是零命中，必须靠 LIKE 兜底。"""
        got = [r["repo"] for r in self.db.search_repos("学习")]
        self.assertEqual(got, ["machine-learning"],
                         "两字中文必须能搜到（纯 trigram 会零命中）")

    def test_three_char_chinese_hits(self):
        got = [r["repo"] for r in self.db.search_repos("机器学")]
        self.assertEqual(got, ["machine-learning"])

    def test_english_hits(self):
        self.assertEqual([r["repo"] for r in self.db.search_repos("clone")],
                         ["Git-clone-Max"])

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.db.search_repos(""), [])

    def test_like_wildcards_are_escaped(self):
        """`%` / `_` 必须当**字面量**，不能当通配符（注入面）。

        `%` → 没有记录含它 → 空；`_` → folder_name 里真的有 `__`，所以**应该**能命中
        （命中恰恰证明它被当字面量处理了 —— 当通配符的话会命中全部记录）。
        """
        # 直接断言转义结果（比"命中几条"更精确：本仓库的 folder_name 天然含 `__`，
        # 用行数判断反而分不清"字面量命中"与"通配符全命中"）
        from gcm.db.repo_search import SearchMixin
        bs = chr(92)
        self.assertEqual(SearchMixin._like_pattern("%"), f"%{bs}%%")
        self.assertEqual(SearchMixin._like_pattern("_"), f"%{bs}_%")
        self.assertEqual(SearchMixin._like_pattern(f"a{bs}b"), f"%a{bs}{bs}b%")
        # 端到端：库里没有任何记录含 `%` → 必须为空
        self.assertEqual(self.db.search_repos("%"), [])

    def test_both_paths_same_shape(self):
        a = self.db.search_repos("学习")      # LIKE 路
        b = self.db.search_repos("机器学")    # FTS 路
        self.assertTrue(a and b)
        self.assertEqual(set(a[0].keys()), set(b[0].keys()),
                         "两条路必须返回同形状的行，调用方不该感知差别")


class TestStructuredFilters(unittest.TestCase):
    """C4-3：tag / host / favorite / updated 过滤，且与关键字可组合。"""

    def setUp(self):
        self.db = _db()

    def tearDown(self):
        self.db.close()

    def test_tag_filter(self):
        got = sorted(r["repo"] for r in self.db.search_repos("tag:tool"))
        self.assertEqual(got, ["Git-clone-Max", "crawler"])

    def test_tag_is_boundary_matched(self):
        """`tag:py` 不得命中 `python`（子串匹配的经典坑）。"""
        self.assertEqual(self.db.search_repos("tag:py"), [])

    def test_negated_tag(self):
        got = sorted(r["repo"] for r in self.db.search_repos("tag:tool -tag:批量"))
        self.assertEqual(got, ["crawler"])

    def test_host_filter(self):
        self.assertEqual(len(self.db.search_repos("host:github.com")), 3)
        self.assertEqual(self.db.search_repos("host:gitlab.com"), [])

    def test_keyword_plus_tag(self):
        got = [r["repo"] for r in self.db.search_repos("tag:tool 爬虫")]
        self.assertEqual(got, ["crawler"])

    def test_favorite_filter(self):
        self.assertEqual(len(self.db.search_repos("favorite:0")), 3)


class TestFtsIndexIsDerived(unittest.TestCase):
    """C4-2：索引是派生的，可随时 drop/rebuild，且行数与 repos 对齐。"""

    def setUp(self):
        self.db = _db()

    def tearDown(self):
        self.db.close()

    def test_schema_v8_and_fts_table(self):
        v = self.db._conn.execute("PRAGMA user_version").fetchone()[0]
        from gcm.db.repo_db import _SCHEMA_VERSION
        self.assertEqual(v, _SCHEMA_VERSION)
        self.assertGreaterEqual(v, 8, "C4 需要 v8（repo_fts 虚表）")
        row = self.db._conn.execute(
            "SELECT name FROM sqlite_master WHERE name='repo_fts'").fetchone()
        self.assertIsNotNone(row)

    def test_rebuild_is_idempotent(self):
        n1 = self.db.rebuild_fts()
        n2 = self.db.rebuild_fts()
        self.assertEqual(n1, n2)
        self.assertEqual(n1, 3)

    def test_ensure_fts_aligns_index(self):
        """`ensure_fts()` 把索引对齐到 repos 行数（外部内容表的 count 不可用于判过期）。"""
        self.assertEqual(self.db.ensure_fts(), 3, "应把索引对齐成 3 条")
        db2 = Database(Path(tempfile.mkdtemp()) / "r.db")
        try:
            db2.upsert_repo(RepoSpec(owner="a", repo="b", url_https="u"), "/tmp/b")
            self.assertEqual(db2.ensure_fts(), 1)
            self.assertEqual(db2.ensure_fts(), 1, "重复调用结果一致（幂等）")
        finally:
            db2.close()

    def test_ensure_fts_on_empty_db_returns_zero(self):
        db3 = Database(Path(tempfile.mkdtemp()) / "e.db")
        try:
            self.assertEqual(db3.ensure_fts(), 0, "没有仓库时不该建索引")
        finally:
            db3.close()

    def test_search_survives_missing_index(self):
        """索引被 drop 后搜索必须仍**有结果**（自动降级 LIKE），而不是报错或变空。"""
        self.db._conn.execute("DROP TABLE repo_fts")
        self.db._conn.commit()
        self.assertEqual([r["repo"] for r in self.db.search_repos("机器学")],
                         ["machine-learning"], "FTS 不可用时必须降级 LIKE 且照样命中")
        self.assertEqual([r["repo"] for r in self.db.search_repos("学习")],
                         ["machine-learning"])


if __name__ == "__main__":
    unittest.main()
