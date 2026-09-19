# -*- coding: utf-8 -*-
"""G46 Phase3：批次总览条 / 键盘 Enter 开详情 / 右键菜单统一 / reduced-motion 守卫。"""
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

from gcm.app.engine import SyncEngine  # noqa: E402
from gcm.db.repo_db import Database  # noqa: E402
from gcm.models import RepoSpec, SyncResult, SyncStatus, SyncAction  # noqa: E402
from gcm.ui.main_window import MainWindow  # noqa: E402


def _make_window():
    tmp = Path(tempfile.mkdtemp(prefix="g46_nav_"))
    db = Database(tmp / "t.db")
    w = MainWindow(data_dir=tmp, db=db, settings=None)
    return tmp, db, w


def _res(status, action="updated", message="x"):
    return SyncResult(spec=RepoSpec("o", "r", url_https="https://github.com/o/r.git"),
                      status=status, action=SyncAction(action), message=message)


class TestBatchOverview(unittest.TestCase):
    """G46-4：批次总览条数字随结果累加。"""

    def _panel(self):
        tmp, db, w = _make_window()
        try:
            return w.progress_panel
        finally:
            db.close()

    def test_overview_after_results(self):
        panel = self._panel()
        panel._prepare_table(5)
        for i in range(5):
            panel._add_table_row(i, RepoSpec("o", f"r{i}", url_https="https://github.com/o/r.git"))
        for i in range(3):
            panel._on_worker_result(i, _res(SyncStatus.SUCCESS))
        panel._on_worker_result(3, _res(SyncStatus.FAILED, "failed"))
        panel._on_worker_result(4, _res(SyncStatus.CONFLICT))
        self.assertEqual(panel.batch_total_lbl.text(), "已完成 5/5 · 成功率 60%")
        self.assertEqual(panel.batch_bar.value(), 100)


class TestKeyboardNav(unittest.TestCase):
    """G46-6：管理表 Enter 打开详情（经 manage_double_clicked 信号）。"""

    def test_enter_emits_double_clicked(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtGui import QKeyEvent
        tmp, db, w = _make_window()
        try:
            panel = w.manage_panel
            rec = db.upsert_repo(RepoSpec("o", "r1", url_https="https://github.com/o/r1.git"),
                                 "D:/tmp/clones", host="github.com")
            w._load_db_into_grid()
            events = []
            panel.manage_double_clicked.connect(lambda idx: events.append(idx))
            self.assertGreater(panel.manage_model.rowCount(), 0, "模型应有行")
            idx = panel.manage_model.index(0, 0)
            panel.manage_table.setCurrentIndex(idx)
            ev = QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return,
                           Qt.KeyboardModifier.NoModifier)
            with mock.patch.object(MainWindow, "show_history") as m_hist:
                handled = panel.eventFilter(panel.manage_table, ev)
            self.assertEqual(m_hist.call_count, 1, "应打开同步历史")
            self.assertTrue(handled, "Enter 应被事件过滤器消费")
            self.assertEqual(len(events), 1, "应发出 manage_double_clicked")
        finally:
            db.close()


class TestContextMenus(unittest.TestCase):
    """G46-12：进度表重试项置灰+tooltip；管理表动作无选中置灰。"""

    def test_progress_menu_retry_disabled_for_success_row(self):
        tmp, db, w = _make_window()
        try:
            panel = w.progress_panel
            panel._prepare_table(2)
            panel._add_table_row(0, RepoSpec("o", "r1", url_https="https://github.com/o/r1.git"))
            panel._on_worker_result(0, _res(SyncStatus.SUCCESS))
            menu = panel._build_progress_menu(0)
            retry = [a for a in menu.actions() if "重试" in a.text()]
            self.assertEqual(len(retry), 1)
            self.assertFalse(retry[0].isEnabled())
            self.assertIn("仅失败", retry[0].toolTip() or "")
        finally:
            db.close()

    def test_progress_menu_retry_enabled_for_failed_row(self):
        tmp, db, w = _make_window()
        try:
            panel = w.progress_panel
            panel._prepare_table(2)
            panel._add_table_row(0, RepoSpec("o", "r1", url_https="https://github.com/o/r1.git"))
            panel._on_worker_result(0, _res(SyncStatus.FAILED, "failed"))
            menu = panel._build_progress_menu(0)
            retry = [a for a in menu.actions() if "重试" in a.text()][0]
            self.assertTrue(retry.isEnabled())
        finally:
            db.close()

    def test_manage_menu_disables_without_selection(self):
        tmp, db, w = _make_window()
        try:
            panel = w.manage_panel
            menu = panel._manage_menu()
            for a in menu.actions():
                if "打标签" in a.text() or "导出" in a.text() or "删除" in a.text():
                    self.assertFalse(a.isEnabled(), f"{a.text()} 应置灰")
        finally:
            db.close()


class TestReducedMotionGuard(unittest.TestCase):
    """G46-8：prefers_reduced_motion 开启时禁用完成动效。"""

    def test_guard_returns_true_when_reduced(self):
        tmp, db, w = _make_window()
        try:
            panel = w.progress_panel
            w.settings.prefers_reduced_motion = True
            self.assertTrue(panel._on_worker_result_motion_guard())
            w.settings.prefers_reduced_motion = False
            self.assertFalse(panel._on_worker_result_motion_guard())
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)