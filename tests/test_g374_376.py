# -*- coding: utf-8 -*-
"""G37-4/G37-6 主控侧 单测（离屏 Qt）。

覆盖：
- G37-4 自动更新定时器：设置间隔启动/0 关闭/busy 跳过
- G37-6 每仓库备注：set_note 持久化 + 详情页备注编辑/保存
"""
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

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.models import RepoSpec
from gcm.ui.main_window import MainWindow
from gcm.ui.repo_detail_dialog import RepoDetailDialog

_app = QApplication.instance() or QApplication(sys.argv)


class TestG374AutoUpdate(unittest.TestCase):
    """G37-4 自动更新定时器。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.w = MainWindow(data_dir=self.tmp,
                            db=Database(self.tmp / "t.db"),
                            settings=SettingsStore(self.tmp / "settings.json"))

    def tearDown(self):
        try:
            self.w._auto_update_timer.stop()
            if hasattr(self.w, "_log_batch_timer") and self.w._log_batch_timer.isActive():
                self.w._log_batch_timer.stop()
            if hasattr(self.w, "_log_batch"):
                self.w._log_batch.clear()
                self.w._flush_log_batch()
        except Exception:
            pass
        # 复位 busy：避免 busy=True 时 closeEvent 弹「确认退出」模态框卡死
        try:
            self.w.busy = False
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass

    def test_timer_off_by_default(self):
        """默认 auto_update_minutes=0 → 定时器未激活。"""
        self.assertFalse(self.w._auto_update_timer.isActive())

    def test_restart_starts_on_positive(self):
        """设置 60 分钟 → 定时器激活且间隔 60*60*1000。"""
        self.w.settings.auto_update_minutes = 60
        self.w._restart_auto_update_timer()
        self.assertTrue(self.w._auto_update_timer.isActive())
        self.assertEqual(self.w._auto_update_timer.interval(), 60 * 60 * 1000)

    def test_restart_zero_stops(self):
        """设置 0 → 定时器停止。"""
        self.w.settings.auto_update_minutes = 60
        self.w._restart_auto_update_timer()
        self.w.settings.auto_update_minutes = 0
        self.w._restart_auto_update_timer()
        self.assertFalse(self.w._auto_update_timer.isActive())

    def test_tick_calls_update_all_when_idle(self):
        """非 busy → tick 触发 update_all。"""
        with mock.patch.object(MainWindow, "update_all") as m_up:
            self.w.busy = False
            self.w._on_auto_update_tick()
        m_up.assert_called_once()

    def test_tick_skips_when_busy(self):
        """busy → tick 跳过 update_all。"""
        with mock.patch.object(MainWindow, "update_all") as m_up:
            self.w.busy = True
            self.w._on_auto_update_tick()
        m_up.assert_not_called()

    def test_save_auto_update_restarts(self):
        """_save_auto_update 持久化并重启定时器。"""
        self.w._save_auto_update(30)
        self.assertEqual(self.w.settings.auto_update_minutes, 30)
        self.assertTrue(self.w._auto_update_timer.isActive())


class TestG376Note(unittest.TestCase):
    """G37-6 每仓库备注。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "t.db")
        self.spec = RepoSpec(owner="o", repo="r",
                             url_https="https://github.com/o/r",
                             folder_name="o__r")
        self.rid = self.db.upsert_repo(self.spec, str(self.tmp), host="github.com")

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass

    def test_set_note_persists(self):
        """set_note → get_repo 返回 note。"""
        self.db.set_note(self.rid, "我的收藏仓库")
        row = self.db.get_repo("o", "r", "github.com")
        self.assertEqual(row["note"], "我的收藏仓库")

    def test_note_migration_column(self):
        """note 列存在（迁移 0002 生效）。"""
        cols = [r[1] for r in self.db._conn.execute("PRAGMA table_info(repos)")]
        self.assertIn("note", cols)

    def test_detail_dialog_save_note(self):
        """详情页备注编辑 → _save_note 写入 DB（经 MainWindow parent 提供 db）。"""
        # MainWindow 复用本案 db；保存后、close 前读取（close 会关闭连接）
        repo = self.db.get_repo("o", "r", "github.com")
        hist = self.db.history(self.rid, 5)
        w = MainWindow(data_dir=self.tmp,
                       db=Database(self.tmp / "w_g37.db"),
                       settings=SettingsStore(self.tmp / "settings.json"))
        # 把本案 repo 的行预置到 w 的 db（_save_note 按 id 定位写 w.parent().db）
        wdb = w.db
        wdb.upsert_repo(self.spec, str(self.tmp), host="github.com")
        try:
            dlg = RepoDetailDialog(repo=repo, history=hist, parent=w)
            dlg.note_edit.setText("新备注")
            with mock.patch("gcm.ui.repo_detail_dialog.QMessageBox"):
                dlg._save_note()
            dlg.close()
            row = wdb.get_repo("o", "r", "github.com")
            self.assertEqual(row["note"], "新备注")
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)