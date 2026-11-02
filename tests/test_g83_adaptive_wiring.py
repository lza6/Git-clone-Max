"""G83 P1-1：自适应自动更新间隔 —— 主窗接线验证。

覆盖：批次有变更回基础间隔、无变更退避、功能关时不维护状态。
用真实 MainWindow（offscreen），结果走真实 SyncResult 对象。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from gcm.db.repo_db import Database  # noqa: E402
from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus  # noqa: E402
from gcm.ui.main_window import MainWindow  # noqa: E402


def _res(action: SyncAction) -> SyncResult:
    return SyncResult(spec=RepoSpec("o", "r", url_https="https://github.com/o/r.git"),
                      status=SyncStatus.SUCCESS, action=action)


class _WinCase(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp(prefix="g83_adapt_"))
        db = Database(tmp / "t.db")
        self.win = MainWindow(data_dir=tmp, db=db, settings=None)
        self.win.settings.auto_update_minutes = 30  # 开启自动更新（默认 0=关）
        self.addCleanup(self.win.close)
        self.addCleanup(self.win.deleteLater)

    def _set_auto(self, minutes: int):
        self.win.settings.auto_update_minutes = minutes
        self.win._restart_auto_update_timer()


class TestAdaptiveWiring(_WinCase):
    def test_init_state(self):
        self.assertIsNone(self.win._auto_update_no_change_streak)
        self.assertFalse(self.win._auto_update_batch_had_changes)

    def test_interval_base_when_no_history(self):
        self._set_auto(30)
        self.assertEqual(self.win._current_auto_update_minutes(), 30)

    def test_result_updated_marks_batch_change(self):
        self.win._on_worker_result(0, _res(SyncAction.UPDATED))
        self.assertTrue(self.win._auto_update_batch_had_changes)
        # 结算后 streak 归零、定时器回基础间隔
        self.win._on_engine_finished()
        self.assertEqual(self.win._auto_update_no_change_streak, 0)
        self.assertFalse(self.win._auto_update_batch_had_changes)

    def test_result_fetched_no_change_backs_off(self):
        self.win._on_worker_result(0, _res(SyncAction.FETCHED))
        self.assertFalse(self.win._auto_update_batch_had_changes)
        self.win._on_engine_finished()
        self.assertEqual(self.win._auto_update_no_change_streak, 1)
        self.assertEqual(self.win._current_auto_update_minutes(), 45)

    def test_disabled_does_not_track_streak(self):
        self._set_auto(0)
        self.win._on_engine_finished()
        self.assertIsNone(self.win._auto_update_no_change_streak)

    def test_recovery_after_change(self):
        self.win._auto_update_no_change_streak = 3
        self.win._on_worker_result(0, _res(SyncAction.CLONED))
        self.win._on_engine_finished()
        self.assertEqual(self.win._current_auto_update_minutes(), 30)


if __name__ == "__main__":
    unittest.main()
