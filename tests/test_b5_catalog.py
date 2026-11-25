# -*- coding: utf-8 -*-
"""B5 仓库编目测试：清单内容 / 未探测语义 / 脱敏 / Markdown / CLI / MCP 资源。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.catalog import (CATALOG_SCHEMA, build_catalog, render_markdown,
                             write_catalog)
from gcm.app.knowledge import RepoProfile
from gcm.db.repo_db import Database


def _add_repo(db: Database, owner: str, repo: str, *, url: str, profile=None) -> int:
    with db._lock:
        cur = db._conn.execute(
            "INSERT INTO repos (owner, repo, folder_name, url, local_path, host,"
            " head_sha, last_sync_at, favorite, tags) VALUES"
            " (?, ?, ?, ?, ?, 'github.com', 'abc123', '2026-11-26 00:00:00', 1, 'tag1')",
            (owner, repo, f"{owner}__{repo}", url, f"D:/clones/{owner}__{repo}"))
        db._conn.commit()
        rid = int(cur.lastrowid)
    if profile is not None:
        db.set_repo_profile(rid, profile)
    return rid


class TestCatalogBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="b5_cat_")
        self.addCleanup(self.tmp.cleanup)
        self.db = Database(Path(self.tmp.name) / "repos.db")
        self.addCleanup(self.db.close)
        self.dir = Path(self.tmp.name)

    def test_build_catalog_probed_and_not_probed(self):
        _add_repo(self.db, "o", "r", url="https://github.com/o/r.git",
                  profile=RepoProfile(lang="python", kind="cli",
                                      summary="A | pipe demo", has_ci=True,
                                      has_tests=False, has_docs=True,
                                      langs={"python": 3}))
        _add_repo(self.db, "o2", "r2", url="https://github.com/o2/r2.git")
        cat = build_catalog(self.db)
        self.assertEqual(cat["schema"], CATALOG_SCHEMA)
        self.assertEqual(cat["count"], 2)
        self.assertEqual(cat["by_lang"], {"python": 1})
        self.assertEqual(cat["by_kind"], {"cli": 1})
        by_repo = {x["repo"]: x for x in cat["repos"]}
        self.assertEqual(by_repo["r"]["profile_status"], "probed")
        self.assertEqual(by_repo["r"]["profile"]["summary"], "A | pipe demo")
        self.assertFalse(by_repo["r"]["profile"]["has_tests"])
        self.assertEqual(by_repo["r2"]["profile_status"], "not_probed")
        self.assertIsNone(by_repo["r2"]["profile"])

    def test_url_credentials_redacted(self):
        _add_repo(self.db, "o", "r",
                  url="https://user:sekrit@github.com/o/r.git")
        cat = build_catalog(self.db)
        self.assertNotIn("sekrit", json.dumps(cat, ensure_ascii=False))

    def test_markdown_groups_and_escapes(self):
        _add_repo(self.db, "o", "r", url="https://github.com/o/r.git",
                  profile=RepoProfile(lang="python", kind="cli",
                                      summary="A | pipe", has_ci=True))
        md = render_markdown(build_catalog(self.db))
        self.assertIn("## github.com", md)
        self.assertIn("o__r", md)
        self.assertIn("python", md)
        self.assertIn("A \\| pipe", md, "表格单元里的竖线必须转义")
        self.assertNotIn("✓", md, "用户可见文本不得用字符符号（缺字形风险）")

    def test_markdown_marks_not_probed(self):
        _add_repo(self.db, "o", "r", url="https://github.com/o/r.git")
        md = render_markdown(build_catalog(self.db))
        self.assertIn("未探测", md, "未探测画像的仓应显式标注")

    def test_write_catalog_files(self):
        _add_repo(self.db, "o", "r", url="https://github.com/o/r.git")
        out = self.dir / "export"
        paths = write_catalog(self.db, out)
        self.assertEqual(paths["count"], 1)
        doc = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
        self.assertEqual(doc["count"], 1)
        md = Path(paths["markdown"]).read_text(encoding="utf-8")
        self.assertIn("# 本地仓库编目", md)


class TestCatalogExits(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="b5_exit_")
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name) / "data"
        self.data.mkdir()
        self.db = Database(self.data / "repos.db")
        _add_repo(self.db, "o", "r", url="https://github.com/o/r.git",
                  profile=RepoProfile(lang="rust", kind="library"))
        self.db.close()

    def test_cli_export_catalog(self):
        from gcm import cli
        out = Path(self.tmp.name) / "cat-out"
        buf = io.StringIO()
        with mock.patch("gcm.__main__.get_data_dir", return_value=self.data), \
             contextlib.redirect_stdout(buf):
            rc = cli.main(["--cli", "--export-catalog", str(out)])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertTrue((out / "catalog.json").exists())
        doc = json.loads((out / "catalog.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["by_lang"], {"rust": 1})

    def test_cli_export_catalog_opens_readonly_first(self):
        """审查修正：以只读连接打开既有库（不再有可写连接的无谓写锁）。"""
        import gcm.db.repo_db as repo_db

        from gcm import cli
        calls: list[bool] = []
        real = repo_db.Database

        class Spy:
            def __init__(self, path, readonly=False):
                calls.append(bool(readonly))
                self._db = real(path, readonly=readonly)

            def __getattr__(self, name):
                return getattr(self._db, name)

            def close(self):
                self._db.close()

        out = Path(self.tmp.name) / "cat-ro"
        buf = io.StringIO()
        with mock.patch("gcm.__main__.get_data_dir", return_value=self.data), \
             mock.patch.object(repo_db, "Database", Spy), \
             contextlib.redirect_stdout(buf):
            rc = cli.main(["--cli", "--export-catalog", str(out)])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertEqual(calls, [True], "既有可用库必须恰好一次、以只读连接打开")

    def test_mcp_resource_catalog(self):
        from gcm.mcp_server import _RESOURCES, _resource_read
        self.assertIn("gcm://catalog", [r["uri"] for r in _RESOURCES])
        res = _resource_read("gcm://catalog", self.data)
        doc = json.loads(res["text"])
        self.assertEqual(doc["count"], 1)
        self.assertEqual(doc["repos"][0]["profile"]["kind"], "library")


if __name__ == "__main__":
    unittest.main(verbosity=2)
