# -*- coding: utf-8 -*-
"""G50-3 覆盖率补点：proxy/reports 低覆盖模块定向补测（CI 三平台门榣≥84）。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestProxyCoverage(unittest.TestCase):
    """G04-3 detect_system_proxy 全分支。"""

    def test_env_priority(self):
        from gcm.app.proxy import detect_system_proxy
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://h1:1"}, clear=True):
            self.assertEqual(detect_system_proxy(), "http://h1:1")
        with mock.patch.dict(os.environ, {"https_proxy": "http://h2:2", "HTTP_PROXY": "http://h3:3"}, clear=True):
            self.assertEqual(detect_system_proxy(), "http://h2:2")
        with mock.patch.dict(os.environ, {"http_proxy": "  http://h4:4  "}, clear=True):
            self.assertEqual(detect_system_proxy(), "http://h4:4")

    def test_empty_posix(self):
        from gcm.app.proxy import detect_system_proxy
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("os.name", "posix"):
            self.assertEqual(detect_system_proxy(), "")

    def test_registry_paths(self):
        from gcm.app.proxy import detect_system_proxy
        # https= 优先
        f1 = mock.MagicMock()
        f1.QueryValueEx.side_effect = [(1, 1), ("https=127.0.0.1:7890;http=127.0.0.1:7891", 1)]
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("os.name", "nt"), \
                mock.patch.dict(sys.modules, {"winreg": f1}):
            self.assertEqual(detect_system_proxy(), "http://127.0.0.1:7890")
        # 仅 http=
        f2 = mock.MagicMock()
        f2.QueryValueEx.side_effect = [(1, 1), ("http=127.0.0.1:8888", 1)]
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("os.name", "nt"), \
                mock.patch.dict(sys.modules, {"winreg": f2}):
            self.assertEqual(detect_system_proxy(), "http://127.0.0.1:8888")
        # ProxyEnable=0 → 空
        f3 = mock.MagicMock()
        f3.QueryValueEx.return_value = (0, 0)
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("os.name", "nt"), \
                mock.patch.dict(sys.modules, {"winreg": f3}):
            self.assertEqual(detect_system_proxy(), "")
        # 纯 host:port
        f4 = mock.MagicMock()
        f4.QueryValueEx.side_effect = [(1, 1), ("127.0.0.1:3128", 1)]
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("os.name", "nt"), \
                mock.patch.dict(sys.modules, {"winreg": f4}):
            self.assertEqual(detect_system_proxy(), "http://127.0.0.1:3128")
        # 异常 → 空
        f5 = mock.MagicMock()
        f5.OpenKey.side_effect = OSError("boom")
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch("os.name", "nt"), \
                mock.patch.dict(sys.modules, {"winreg": f5}):
            self.assertEqual(detect_system_proxy(), "")


class TestReportsCoverage(unittest.TestCase):
    """G07-3/G47-3 报表导出低覆盖分支。"""

    def test_parse_date(self):
        from gcm.reports import _parse_date
        self.assertIsNotNone(_parse_date("2026-09-20"))
        self.assertIsNone(_parse_date("bad"))
        self.assertIsNone(_parse_date(""))

    def test_export_csv_empty(self):
        from gcm.db.repo_db import Database
        from gcm.reports import export_csv
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "t.db")
        out = tmp / "r.csv"
        n = export_csv(db, out, {})
        self.assertEqual(n, 0)
        text = out.read_text(encoding="utf-8-sig")
        self.assertIn("owner", text)
        self.assertIn("started_at", text)

    def test_export_markdown_empty(self):
        from gcm.db.repo_db import Database
        from gcm.reports import export_markdown
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "t.db")
        out = tmp / "r.md"
        n = export_markdown(db, out, {})
        self.assertEqual(n, 0)
        self.assertIn("Git-clone-Max 同步报表", out.read_text(encoding="utf-8"))

    def test_export_xlsx_without_openpyxl(self):
        from gcm.db.repo_db import Database
        from gcm.reports import export_xlsx
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "t.db")
        with mock.patch.dict(sys.modules, {"openpyxl": None}):
            with self.assertRaises(RuntimeError):
                export_xlsx(db, tmp / "x.xlsx")

    def test_fetch_rows_start_gt_end_empty(self):
        from gcm.db.repo_db import Database
        from gcm.reports import _fetch_rows
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "t.db")
        self.assertEqual(_fetch_rows(db, {"start": "2026-09-20", "end": "2026-09-01"}), [])
        self.assertEqual(_fetch_rows(db, {"start": "bad"}), [])



class TestWorkerCloneRun(unittest.TestCase):
    """G50-3 CloneWorker.run 全路径：引擎/db 独立模式 + 异常 + 更新模式。"""

    def _setup(self):
        from gcm.app.worker import CloneWorker, TaskPayload
        from gcm.git.service import GitService
        from gcm.models import RepoSpec
        spec = RepoSpec(owner="o", repo="r", url_https="https://github.com/o/r.git")
        svc = mock.Mock()
        svc.sync.return_value = None
        svc.fetch_depth = 0
        svc.unshallow = False
        payload = TaskPayload(spec=spec, flag=None, host="github.com")
        return spec, svc, payload, CloneWorker

    def test_run_engine_mode_success(self):
        from gcm.models import SyncAction, SyncResult, SyncStatus
        spec, svc, payload, CloneWorker = self._setup()
        res = SyncResult(
            spec=spec, status=SyncStatus.SUCCESS, action=SyncAction.CLONED,
            message="克隆完成", path="C:/tmp/r",
            commits=3, head_sha="abc", remote_sha="abc")
        res.duration_ms = 10
        svc.sync.return_value = res
        got = []
        w = CloneWorker(0, payload, svc, db=None, progress_path=None)
        w.signals.result.connect(lambda i, r: got.append((i, r)))
        w.run()
        self.assertEqual(got[0][1].status, SyncStatus.SUCCESS)
        self.assertEqual(got[0][1].message, "克隆完成")
        svc.sync.assert_called_once_with(spec)
        self.assertEqual(svc.send_progress_detail, w._emit_progress_detail)

    def test_run_independent_mode_with_db_and_progress(self):
        from gcm.db.repo_db import Database
        from gcm.models import SyncAction, SyncResult, SyncStatus
        spec, svc, payload, CloneWorker = self._setup()
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "t.db")
        res = SyncResult(
            spec=spec, status=SyncStatus.SUCCESS, action=SyncAction.CLONED,
            path=str(tmp), commits=0, head_sha="def", remote_sha="def")
        res.duration_ms = 5
        svc.sync.return_value = res
        w = CloneWorker(1, payload, svc, db=db, progress_path=str(tmp / "p.json"))
        w.run()
        db2 = Database(tmp / "t.db")
        rows = db2.list_repos("github.com")
        self.assertTrue(any(r["owner"] == "o" for r in rows))
        self.assertTrue((tmp / "p.json").exists())

    def test_run_exception_sets_failed(self):
        from gcm.models import SyncStatus
        spec, svc, payload, CloneWorker = self._setup()
        svc.sync.side_effect = RuntimeError("boom")
        got = []
        w = CloneWorker(2, payload, svc, db=None, progress_path=None)
        w.signals.result.connect(lambda i, r: got.append((i, r)))
        w.run()
        self.assertEqual(got[0][1].status, SyncStatus.FAILED)
        self.assertIn("boom", got[0][1].detail)

    def test_run_is_update_emits_running_first(self):
        from gcm.models import SyncAction, SyncResult, SyncStatus
        spec, svc, payload, CloneWorker = self._setup()
        svc.sync.return_value = SyncResult(
            spec=spec, status=SyncStatus.SUCCESS, action=SyncAction.UPDATED,
            path="C:/tmp/r", message="更新完成")
        got = []
        w = CloneWorker(3, payload, svc, db=None, progress_path=None, is_update=True)
        w.signals.result.connect(lambda i, r: got.append((i, r)))
        w.run()
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0][1].status, SyncStatus.RUNNING)
        self.assertEqual(got[0][1].action, SyncAction.FETCHED)
        self.assertEqual(got[1][1].status, SyncStatus.SUCCESS)

if __name__ == "__main__":
    unittest.main(verbosity=2)
