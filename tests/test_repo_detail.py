# -*- coding: utf-8 -*-
"""RepoDetailDialog 离屏单元测试。"""
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

from PyQt6.QtWidgets import QApplication, QLabel, QMessageBox

from gcm.ui.repo_detail_dialog import RepoDetailDialog

_app = QApplication.instance() or QApplication(sys.argv)


def _repo(tmp_dir: Path) -> dict:
    return {
        "owner": "octocat",
        "repo": "hello-world",
        "folder_name": "octocat__hello-world",
        "local_path": str(tmp_dir),
        "head_sha": "0123456789abcdef",
        "last_sync_at": "2026-09-09 12:00:00",
        "host": "github.com",
        "url": "https://github.com/octocat/hello-world.git",
        "default_branch": "main",
    }


def _history() -> list:
    return [
        {
            "started_at": "2026-09-09 12:00:00",
            "action": "cloned",
            "status": "success",
            "commits": 1,
            "message": "首次克隆完成",
            "detail": "",
            "duration_ms": 1200,
        },
        {
            "started_at": "2026-09-09 13:00:00",
            "action": "updated",
            "status": "success",
            "commits": 3,
            "message": "增量更新",
            "detail": "",
            "duration_ms": 800,
        },
        {
            "started_at": "2026-09-09 14:00:00",
            "action": "failed",
            "status": "failed",
            "commits": 0,
            "message": "网络超时",
            "detail": "",
            "duration_ms": 500,
        },
    ]


class TestRepoDetailDialog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.dlg = RepoDetailDialog(_repo(cls.tmp), _history())
        cls.dlg.show()

    @classmethod
    def tearDownClass(cls):
        cls.dlg.close()

    def test_dialog_builds_with_rows(self):
        self.assertEqual(self.dlg.table.rowCount(), 3)
        self.assertIn("octocat", self.dlg.lbl_title.text())
        self.assertIn("hello-world", self.dlg.lbl_title.text())
        # 元信息区含 local_path 文本（QLabel 渲染）
        texts = [w.text() for w in self.dlg.findChildren(QLabel)]
        self.assertTrue(any(str(self.tmp) in t for t in texts), "元信息区应含 local_path")

    def test_copy_path_clipboard(self):
        self.dlg.btn_copy_path.click()
        self.assertEqual(QApplication.clipboard().text(), str(self.tmp))

    def test_open_dir_missing_warns(self):
        dlg = RepoDetailDialog(_repo(Path(tempfile.mkdtemp()) / "no_such_dir"), [])
        dlg.show()
        with mock.patch("os.startfile") as m_start, \
                mock.patch("gcm.ui.repo_detail_dialog.QMessageBox.warning") as m_warn:
            dlg.btn_open_dir.click()  # 不抛异常
        m_start.assert_not_called()
        m_warn.assert_called_once()
        dlg.close()

    def test_open_dir_exists_calls_startfile(self):
        dlg = RepoDetailDialog(_repo(self.tmp), [])
        dlg.show()
        with mock.patch("os.startfile") as m_start:
            dlg.btn_open_dir.click()
        m_start.assert_called_once()
        dlg.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
