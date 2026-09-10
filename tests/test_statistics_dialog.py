# -*- coding: utf-8 -*-
"""G07-1 统计中心对话框 E2E：全局聚合展示。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gcm.db.repo_db import Database
from gcm.models import RepoSpec, SyncAction, SyncStatus


def _spec(owner, repo):
    return RepoSpec(owner, repo, f"https://github.com/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


class TestStatisticsDialog(unittest.TestCase):
    def test_dialog_shows_overview(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        d = Path(tempfile.mkdtemp())
        db = Database(d / "t.db")
        rid1 = db.upsert_repo(_spec("o1", "a"), "/x/o1__a")
        rid2 = db.upsert_repo(_spec("o2", "b"), "/x/o2__b")
        db.add_sync_history(rid1, SyncStatus.SUCCESS, SyncAction.CLONED, "ok", duration_ms=100)
        db.add_sync_history(rid1, SyncStatus.SUCCESS, SyncAction.UPDATED, "ok", duration_ms=200)
        db.add_sync_history(rid2, SyncStatus.FAILED, SyncAction.FAILED, "bad", duration_ms=300)
        dlg = StatisticsDialog(db=db)
        text = dlg.lbl_summary.text()
        self.assertIn("2", text)  # 总仓库 2
        self.assertIn("3", text)  # 总同步 3
        self.assertIn("2", text)  # 成功 2
        self.assertIn("1", text)  # 失败 1
        self.assertIn("github.com", dlg.lbl_hosts.text())
        db.close()

    def test_dialog_empty_db(self):
        from gcm.ui.statistics_dialog import StatisticsDialog
        d = Path(tempfile.mkdtemp())
        db = Database(d / "t.db")
        dlg = StatisticsDialog(db=db)
        self.assertIn("0", dlg.lbl_summary.text())
        db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
