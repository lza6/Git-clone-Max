# -*- coding: utf-8 -*-
"""G44-1 安全纵深：token 掩码 + 报表导出不含凭据（离屏 Qt + 真实 db）。"""
from __future__ import annotations

import csv
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication, QCheckBox, QLineEdit

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.models import RepoSpec, SyncAction, SyncStatus
from gcm.reports import EXPORT_COLUMNS, export_csv, export_markdown
from gcm.ui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


def _spec(owner, repo, host="github.com"):
    return RepoSpec(owner, repo, f"https://{host}/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


class TestG44TokenMasking(unittest.TestCase):
    """G44-1：edit_token / edit_host_tokens 掩码与显示明文切换。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(self.tmp / "settings.json")
        s = store.load()
        s.token = "ghp_secret"
        s.host_tokens = {"gitlab.com": "glpat_secret"}
        store.save(s)
        self.w = MainWindow(data_dir=self.tmp, db=Database(self.tmp / "t.db"),
                            settings=store)

    def tearDown(self):
        try:
            if hasattr(self.w, "_log_batch_timer") and self.w._log_batch_timer.isActive():
                self.w._log_batch_timer.stop()
            if hasattr(self.w, "_log_batch"):
                self.w._log_batch.clear()
                self.w._flush_log_batch()
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass

    # ------------------------------------------------------------ 掩码断言
    def test_edit_token_echo_password(self):
        """G44-1：全局 token 输入框掩码（Password）。"""
        self.assertIsInstance(self.w.edit_token, QLineEdit)
        self.assertEqual(self.w.edit_token.echoMode(), QLineEdit.EchoMode.Password)

    def test_host_tokens_editor_default_password(self):
        """G44-1：host_tokens 编辑器初始 Password，显示明文开关未勾选。"""
        self.assertIsInstance(self.w.edit_host_tokens, QLineEdit)
        self.assertEqual(self.w.edit_host_tokens.echoMode(),
                         QLineEdit.EchoMode.Password)
        self.assertFalse(self.w.ck_show_host_tokens.isChecked())

    # ------------------------------------------------------------ 切换开关
    def test_show_host_tokens_toggle_switches_echo(self):
        """G44-1：勾选「显示明文」→ Normal；取消 → 恢复 Password。"""
        self.w.ck_show_host_tokens.setChecked(True)
        self.assertEqual(self.w.edit_host_tokens.echoMode(),
                         QLineEdit.EchoMode.Normal)
        self.w.ck_show_host_tokens.setChecked(False)
        self.assertEqual(self.w.edit_host_tokens.echoMode(),
                         QLineEdit.EchoMode.Password)

    def test_show_host_tokens_is_checkbox(self):
        """G44-1：开关控件为 QCheckBox。"""
        self.assertIsInstance(self.w.ck_show_host_tokens, QCheckBox)


class TestG44ReportNoCredentials(unittest.TestCase):
    """G44-1：CSV/Markdown 导出列不含 token / host_tokens / editor 等敏感列。"""

    SENSITIVE = {"token", "host_tokens", "editor", "access_token",
                 "private_token", "password", "secret", "credential"}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "g44.db")
        rid = self.db.upsert_repo(_spec("o", "r"), "/x/o__r", host="gitlab.com")
        self.db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.CLONED,
                                 "ok", commits=1, duration_ms=500)

    def tearDown(self):
        self.db.close()

    # ------------------------------------------------------------ 列契约
    def test_export_columns_constant_excludes_sensitive(self):
        """EXPORT_COLUMNS 固定为元数据列，不含任何敏感列。"""
        self.assertEqual(
            EXPORT_COLUMNS,
            ("owner", "repo", "host", "status", "action",
             "message", "commits", "duration_ms", "started_at"))
        self.assertTrue(self.SENSITIVE.isdisjoint(EXPORT_COLUMNS))

    def test_csv_header_excludes_sensitive(self):
        """真实 db 导出 CSV：表头列不含敏感列。"""
        out = self.tmp / "g44.csv"
        n = export_csv(self.db, out)
        self.assertGreaterEqual(n, 1)
        with open(out, encoding="utf-8-sig", newline="") as f:
            header = next(csv.reader(f))
        self.assertEqual(set(header), set(EXPORT_COLUMNS))
        self.assertTrue(self.SENSITIVE.isdisjoint(set(header)))

    def test_markdown_export_has_no_credential_text(self):
        """真实 db 导出 Markdown：不含 token / host_tokens / editor 字样。"""
        out = self.tmp / "g44.md"
        self.assertGreaterEqual(export_markdown(self.db, out), 1)
        text = out.read_text(encoding="utf-8").lower()
        self.assertNotIn("token", text)
        self.assertNotIn("host_tokens", text)
        self.assertNotIn("editor", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
