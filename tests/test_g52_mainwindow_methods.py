"""G52 主窗口新增方法覆盖测试：续跑调度 / 失败汇总 / 看门狗续跑 / 失败类别重试。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication([])

import gcm.ui.main_window as mw
from gcm.db.runtime_sessions import RuntimeSessionStore
from gcm.models import RepoSpec


class _FakeSettings:
    def __init__(self):
        self.download_dir = ""
        self.watchdog_silent = False


class _FakeWindow:
    """真实 MainWindow 方法绑定的最小替身（可见、busy 可控）。"""

    def __init__(self, data_dir: Path, store=None):
        self.data_dir = data_dir
        self.session_store = store or RuntimeSessionStore(data_dir / "session_runtime.json")
        self.settings = _FakeSettings()
        self.busy = False
        self.watchdog = type("W", (), {"silent": False})()
        self.progress_path = data_dir / "progress.json"
        self._launched: list = []
        self._logs: list = []
        self._visible = True

    def isVisible(self) -> bool:
        return self._visible

    def _launch(self, specs, **kwargs):
        self._launched.append(specs)

    def _emit_log(self, *a, **k):
        self._logs.append(a)


def _bind(win):
    win._schedule_resume_check = mw.MainWindow._schedule_resume_check.__get__(win)
    win._maybe_show_resume_dialog = mw.MainWindow._maybe_show_resume_dialog.__get__(win)
    win._on_watchdog_resume = mw.MainWindow._on_watchdog_resume.__get__(win)
    win._open_failure_dialog = mw.MainWindow._open_failure_dialog.__get__(win)
    win._on_failure_category_retry = mw.MainWindow._on_failure_category_retry.__get__(win)
    win._progress_gc_mod = mw.MainWindow._progress_gc_mod
    return win


class TestResumeCheckSchedule(unittest.TestCase):
    def test_schedule_when_visible(self):
        with tempfile.TemporaryDirectory() as td:
            win = _bind(_FakeWindow(Path(td)))
            with mock.patch("gcm.ui.main_window.QTimer.singleShot") as m:
                win._schedule_resume_check()
            m.assert_called_once()

    def test_schedule_when_hidden_skips(self):
        with tempfile.TemporaryDirectory() as td:
            win = _bind(_FakeWindow(Path(td)))
            win._visible = False
            with mock.patch("gcm.ui.main_window.QTimer.singleShot") as m:
                win._schedule_resume_check()
            m.assert_not_called()


class TestFailureDialogAndRetry(unittest.TestCase):
    def test_open_failure_dialog_success(self):
        with tempfile.TemporaryDirectory() as td:
            win = _bind(_FakeWindow(Path(td)))
            with mock.patch("gcm.ui.failure_panel.FailureDialog") as m:
                win._open_failure_dialog()
            m.assert_called_once()

    def test_on_failure_category_retry_launches(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prog = td / "progress.json"
            prog.write_text(json.dumps({
                "finished": [],
                "in_progress": {},
                "failed": [{"key": "owner/repo1", "message": "Connection timed out",
                            "time": "2026-09-25 00:00:00"}],
            }), encoding="utf-8")
            win = _bind(_FakeWindow(td))
            win._on_failure_category_retry("network")
            self.assertEqual(len(win._launched), 1)

    def test_on_failure_category_retry_empty(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prog = td / "progress.json"
            prog.write_text(json.dumps({"finished": [], "in_progress": {},
                                        "failed": []}), encoding="utf-8")
            win = _bind(_FakeWindow(td))
            win._on_failure_category_retry("network")
            self.assertEqual(len(win._launched), 0)


class TestWatchdogResume(unittest.TestCase):
    def test_resume_launches(self):
        with tempfile.TemporaryDirectory() as td:
            win = _bind(_FakeWindow(Path(td)))
            win._on_watchdog_resume([RepoSpec("o", "r", "https://github.com/o/r.git",
                                              folder_name="o__r")])
            self.assertEqual(len(win._launched), 1)

    def test_resume_busy_skips(self):
        with tempfile.TemporaryDirectory() as td:
            win = _bind(_FakeWindow(Path(td)))
            win.busy = True
            win._on_watchdog_resume([RepoSpec("o", "r", "https://github.com/o/r.git",
                                              folder_name="o__r")])
            self.assertEqual(len(win._launched), 0)


class TestResumeDialogGuard(unittest.TestCase):
    def test_hidden_no_dialog(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            store = RuntimeSessionStore(td / "session_runtime.json")
            store.replace([RepoSpec("o", "r", "https://github.com/o/r.git",
                                    folder_name="o__r")])
            win = _bind(_FakeWindow(td, store))
            win._visible = False
            with mock.patch.object(QMessageBox, "question") as m:
                win._maybe_show_resume_dialog()
            m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
