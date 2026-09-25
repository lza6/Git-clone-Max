"""G52-2 启动续跑对话框测试（QMessageBox 交互 mock；清空/续跑两条路径）。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication([])

from gcm.db.runtime_sessions import RuntimeSessionStore
from gcm.db.settings import Settings
from gcm.models import RepoSpec


class _FakeSettings:
    def __init__(self):
        self.s = Settings()
        self.download_dir = ""


class _FakeWindow:
    """最小 MainWindow 替身：只测续跑对话框逻辑（不建真实窗口）。"""

    def __init__(self, data_dir: Path, store):
        self.data_dir = data_dir
        self.session_store = store
        self.settings = _FakeSettings()
        self.busy = False
        self.engine = None
        self.watchdog = None
        self._launched: list = []
        self._cleared = False

    def isVisible(self) -> bool:
        # G52-2：真实 MainWindow 仅在可见时弹续跑对话框；替身模拟可见
        return True

    def _launch(self, specs, **kwargs):
        self._launched.append(specs)

    def _emit_log(self, *a, **k):
        pass


def _bind_methods(win):
    """把 main_window 的续跑对话框方法绑定到替身。"""
    import gcm.ui.main_window as mw
    win._maybe_show_resume_dialog = mw.MainWindow._maybe_show_resume_dialog.__get__(win)
    win._on_failure_category_retry = mw.MainWindow._on_failure_category_retry.__get__(win)
    win._progress_gc_mod = mw.MainWindow._progress_gc_mod


class TestResumeDialog(unittest.TestCase):
    def test_resume_yes_launches(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            store = RuntimeSessionStore(td / "session_runtime.json")
            store.replace([RepoSpec(owner="o", repo="r",
                                    url_https="https://github.com/o/r.git",
                                    folder_name="o__r")])
            win = _FakeWindow(td, store)
            _bind_methods(win)
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as m:
                win._maybe_show_resume_dialog()
            m.assert_called_once()
            self.assertEqual(len(win._launched), 1)
            self.assertEqual(win._launched[0][0].owner, "o")

    def test_resume_no_clears(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            store = RuntimeSessionStore(td / "session_runtime.json")
            store.replace([RepoSpec(owner="o", repo="r",
                                    url_https="https://github.com/o/r.git",
                                    folder_name="o__r")])
            win = _FakeWindow(td, store)
            _bind_methods(win)
            with mock.patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.No):
                win._maybe_show_resume_dialog()
            self.assertEqual(len(win._launched), 0)
            self.assertTrue(store.is_empty())

    def test_empty_store_no_dialog(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            store = RuntimeSessionStore(td / "session_runtime.json")
            win = _FakeWindow(td, store)
            _bind_methods(win)
            with mock.patch.object(QMessageBox, "question") as m:
                win._maybe_show_resume_dialog()
            m.assert_not_called()


if __name__ == "__main__":
    unittest.main()
