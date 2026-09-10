# -*- coding: utf-8 -*-
"""G07-3 报表导出测试：CSV + Markdown（对应指南 G07-3）。"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.repo_db import Database
from gcm.models import RepoSpec, SyncAction, SyncStatus
from gcm.reports import export_csv, export_markdown


def _spec(owner, repo):
    return RepoSpec(owner, repo, f"https://github.com/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


class TestReports(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "report.db")
        rid = self.db.upsert_repo(_spec("o", "a"), "/x/o__a")
        self.db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.CLONED,
                                 "ok", commits=0, duration_ms=1000)
        self.db.add_sync_history(rid, SyncStatus.FAILED, SyncAction.FAILED,
                                 "bad", commits=0, duration_ms=2000)

    def tearDown(self):
        self.db.close()

    def test_export_csv(self):
        out = self.tmp / "report.csv"
        n = export_csv(self.db, out)
        self.assertEqual(n, 2)
        with open(out, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.reader(f))
        self.assertEqual(rows[0], ["owner", "repo", "host", "status", "action",
                                   "message", "commits", "duration_ms", "started_at"])
        self.assertEqual(len(rows), 3)  # header + 2 行

    def test_export_markdown(self):
        out = self.tmp / "report.md"
        n = export_markdown(self.db, out)
        self.assertEqual(n, 2)
        text = out.read_text(encoding="utf-8")
        self.assertIn("# Git-clone-Max 同步报表", text)
        self.assertIn("成功", text)
        self.assertIn("失败", text)

    def test_export_empty(self):
        db2 = Database(self.tmp / "empty.db")
        out = self.tmp / "empty.csv"
        n = export_csv(db2, out)
        self.assertEqual(n, 0)
        db2.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
