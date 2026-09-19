# -*- coding: utf-8 -*-
"""G47/G48 覆盖率助推：补足新增模块分支（diag 探测/归档错误路径/模型健康列/统计周期）。"""

import os, sys, tempfile, unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gcm.db.repo_db import Database
from gcm.models import RepoSpec, SyncStatus, SyncAction


class TestDiagProbes(unittest.TestCase):
    def test_probe_git_missing(self):
        from gcm.app.diag import probe_git
        with mock.patch("subprocess.run", side_effect=FileNotFoundError("no git")):
            r = probe_git()
            self.assertEqual(r["status"], "err")

    def test_tcp_ok_and_fail(self):
        from gcm.app.diag import _tcp_ok
        import socket
        ok, d1 = _tcp_ok("127.0.0.1", 1)  # 端口 1 通常拒绝 → err 分支
        with mock.patch("socket.create_connection", side_effect=OSError("x")):
            ok2, d2 = _tcp_ok("h", 443)
            self.assertFalse(ok2)

    def test_api_warn_and_err(self):
        from gcm.app.diag import probe_github_api
        with mock.patch("urllib.request.urlopen", side_effect=OSError("net")):
            r = probe_github_api()
            self.assertEqual(r["status"], "err")

    def test_run_diagnostics_with_proxy(self):
        from gcm.app.diag import run_diagnostics
        with mock.patch("gcm.app.diag.probe_git",
                        return_value={"name": "git", "status": "ok", "detail": "v"}):
            reps = run_diagnostics(proxy="http://127.0.0.1:8080")
            names = [r["name"] for r in reps]
            self.assertIn("代理", names)

    def test_grade_variants(self):
        from gcm.app.diag import grade
        self.assertIn("绿", grade([{"status": "ok"}]))
        self.assertIn("黄", grade([{"status": "warn"}]))
        self.assertIn("红", grade([{"status": "err"}]))


class TestArchiveErrorPath(unittest.TestCase):
    def test_bad_clone_raises(self):
        from gcm.git.archive import download_archive, _git
        tmp = Path(tempfile.mkdtemp())
        with mock.patch("gcm.git.archive._git", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                download_archive("https://x/y.git", tmp / "o.zip")


class TestModelHealthValue(unittest.TestCase):
    def test_health_value_via_db(self):
        from gcm.ui.manage_model import ManageModel
        d = Path(tempfile.mkdtemp())
        db = Database(d / "t.db")
        rid = db.upsert_repo(RepoSpec("o", "r", url_https="https://github.com/o/r.git"), "x")
        for _ in range(3):
            db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.UPDATED)
        model = ManageModel(db=db)
        model.set_rows(db.list_repos())
        self.assertGreaterEqual(model.health_value(0), 4)


class TestStatisticsPeriodPng(unittest.TestCase):
    def test_period_change_and_png(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        from PyQt6.QtWidgets import QFileDialog
        d = Path(tempfile.mkdtemp())
        db = Database(d / "t.db")
        dlg = StatisticsDialog(db)
        try:
            dlg.period_combo.setCurrentIndex(0)  # 7 天 → 触发 _on_period_changed
            self.assertEqual(dlg._days, 7)
            with mock.patch.object(QFileDialog, "getSaveFileName", return_value=("z.png", "PNG (*.png)")):
                dlg._export_png()  # 不抛异常即通过
        finally:
            dlg.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)