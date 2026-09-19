# -*- coding: utf-8 -*-
"""G47 数据洞察测试：健康度/大小/多标签/元数据导入导出/xlsx/趋势/详情空态。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gcm.db.repo_db import Database
from gcm.models import RepoSpec, SyncStatus, SyncAction


def _mkdb():
    d = Path(tempfile.mkdtemp(prefix="g47_"))
    return Database(d / "t.db")


def _add(db, i=0, tags="a|b"):
    return db.upsert_repo(RepoSpec(f"o{i}", f"r{i}", url_https=f"https://github.com/o{i}/r{i}.git"),
                          f"D:/tmp/{i}", host="github.com"), db.set_tags(0, tags.split("|"))


class TestHealthScore(unittest.TestCase):
    def test_empty_history_default_3(self):
        db = _mkdb()
        rid = db.upsert_repo(RepoSpec("o", "r", url_https="https://github.com/o/r.git"), "x")
        self.assertEqual(db.repo_health(rid), 3)

    def test_all_success_scores_high(self):
        db = _mkdb()
        rid = db.upsert_repo(RepoSpec("o", "r", url_https="https://github.com/o/r.git"), "x")
        for i in range(5):
            db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.UPDATED)
        self.assertGreaterEqual(db.repo_health(rid), 4)

    def test_conflicts_lower_score(self):
        db = _mkdb()
        rid = db.upsert_repo(RepoSpec("o", "r", url_https="https://github.com/o/r.git"), "x")
        for i in range(4):
            db.add_sync_history(rid, SyncStatus.CONFLICT, SyncAction.UPDATED)
        self.assertLess(db.repo_health(rid), 3)


class TestDirSize(unittest.TestCase):
    def test_dir_size_sum(self):
        d = Path(tempfile.mkdtemp())
        (d / "a.txt").write_bytes(b"x" * 100)
        sub = d / "sub"; sub.mkdir()
        (sub / "b.bin").write_bytes(b"y" * 50)
        self.assertEqual(Database.dir_size(d), 150)


class TestTagFilter(unittest.TestCase):
    def test_and_or(self):
        db = _mkdb()
        r0 = db.upsert_repo(RepoSpec("o0", "r0", url_https="https://github.com/o0/r0.git"), "x")
        db.set_tags(r0, ["a", "b"])
        r1 = db.upsert_repo(RepoSpec("o1", "r1", url_https="https://github.com/o1/r1.git"), "y")
        db.set_tags(r1, ["a"])
        self.assertEqual(len(db.list_repos_tags(["a"], "or")), 2)
        self.assertEqual(len(db.list_repos_tags(["a", "b"], "and")), 1)
        self.assertEqual(len(db.list_repos_tags(["a", "b"], "or")), 2)


class TestMetadataImportExport(unittest.TestCase):
    def test_roundtrip(self):
        db = _mkdb()
        rid = db.upsert_repo(RepoSpec("o", "r", url_https="https://github.com/o/r.git"), "x")
        db.set_tags(rid, ["a", "b"]); db.set_favorite(rid, True); db.set_note(rid, "hi")
        exp = Path(tempfile.mkdtemp()) / "meta.json"
        n = db.export_metadata_json(exp)
        self.assertEqual(n, 1)
        db2 = _mkdb()
        db2.import_metadata_json(exp)
        rows = db2.list_repos()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["tags"], "a|b")
        self.assertEqual(rows[0]["favorite"], 1)
        self.assertEqual(rows[0]["note"], "hi")


class TestXlsxExport(unittest.TestCase):
    def test_xlsx_roundtrip(self):
        try:
            import openpyxl  # noqa
        except ImportError:
            self.skipTest("openpyxl 未安装")
        db = _mkdb()
        db.upsert_repo(RepoSpec("o", "r", url_https="https://github.com/o/r.git"), "x")
        out = Path(tempfile.mkdtemp()) / "r.xlsx"
        from gcm.reports import export_xlsx
        n = export_xlsx(db, out)
        import openpyxl as _xl
        wb = _xl.load_workbook(str(out))
        ws = wb.active
        rows = list(ws.iter_rows(values_only=True))
        self.assertEqual(n, 1)
        self.assertEqual(len(rows), 2)  # 表头 + 1 数据行
        self.assertEqual(rows[0][0], "owner")

    def test_missing_openpyxl_raises_hint(self):
        db = _mkdb()
        with mock.patch.dict(sys.modules, {"openpyxl": None}):
            from gcm.reports import export_xlsx
            try:
                export_xlsx(db, "x.xlsx")
                self.fail("应抛提示异常")
            except RuntimeError as e:
                self.assertIn("openpyxl", str(e))


class TestTrendAndDetail(unittest.TestCase):
    def test_trend_data_at(self):
        from gcm.ui.statistics_dialog import _TrendCanvas
        c = _TrendCanvas()
        c.resize(300, 120)
        c.set_data([{"date": "d1", "success": 1, "failed": 0, "conflict": 0,
                     "cloned": 1}] * 3)
        self.assertEqual(c.data_at(10), 0)

    def test_detail_empty_state(self):
        from gcm.ui.repo_detail_dialog import RepoDetailDialog
        dlg = RepoDetailDialog({"id": 1, "owner": "o", "repo": "r"}, [])
        try:
            self.assertEqual(dlg.table.rowCount(), 1)
            self.assertIn("暂无同步记录", dlg.table.item(0, 0).text())
        finally:
            dlg.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)