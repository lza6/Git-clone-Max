"""G52 主窗口补充覆盖：update_all / 行转 spec / 启动恢复 / 归档行 / 热键 / 剪贴板 / 引擎行日志。"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PyQt6.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication([])

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.models import RepoSpec
from gcm.ui.main_window import MainWindow
from gcm.ui.theme import LogLevel


def _make_win():
    d = Path(tempfile.mkdtemp(prefix="gcm_mw_extra_"))
    db = Database(d / "t.db")
    ss = SettingsStore(d / "settings.json")
    win = MainWindow(data_dir=d, db=db, settings=ss)
    return win, d


class TestMainWindowExtra(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 屏蔽首启向导（QDialog.exec 会阻塞测试）
        cls._orig_onb = MainWindow._maybe_show_onboarding
        MainWindow._maybe_show_onboarding = lambda self: None
        cls.w, cls.d = _make_win()
        # 不真正 show（避免任何定时器/向导），仅置 visible 守卫所需状态
        cls.w.show()
        cls.w.hide()
        cls._orig_q = QMessageBox.question
        cls._orig_i = QMessageBox.information
        cls._orig_w = QMessageBox.warning
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.No)
        QMessageBox.information = staticmethod(lambda *a, **k: None)
        QMessageBox.warning = staticmethod(lambda *a, **k: None)

    @classmethod
    def tearDownClass(cls):
        MainWindow._maybe_show_onboarding = cls._orig_onb
        QMessageBox.question = cls._orig_q
        QMessageBox.information = cls._orig_i
        QMessageBox.warning = cls._orig_w
        try:
            cls.w.close()
        except Exception:
            pass

    def test_rows_to_specs(self):
        rows = [{"owner": "a", "repo": "b", "host": "github.com",
                 "local_path": str(self.d / "a__b"), "folder_name": "a__b",
                 "url": "https://github.com/a/b.git", "branch": "main"}]
        specs = self.w._rows_to_specs(rows)
        self.assertEqual(len(specs), 1)
        self.assertEqual(specs[0].folder_name, "a__b")

    def test_update_all_empty_db(self):
        # 空库 → information 提示，不崩
        self.w.update_all()

    def test_update_all_busy(self):
        self.w.busy = True
        try:
            self.w.update_all()
        finally:
            self.w.busy = False

    def test_startup_recovery_prefill_empty(self):
        self.w._startup_recovery_prefill()  # 无残留 → 不崩

    def test_archive_line_empty(self):
        self.w.repo_input.setPlainText("")
        self.w._archive_current_line()  # 空 → warn 不崩

    def test_archive_line_with_url(self):
        self.w.repo_input.setPlainText("https://github.com/a/b.git")
        with mock.patch("gcm.git.archive.download_archive") as da:
            self.w._archive_current_line()  # 线程 daemon 启动，不等待

    def test_hotkey_start_cancel(self):
        self.w._hotkey_start_cancel()  # busy=False → start_all 或提示

    def test_on_clipboard_url(self):
        self.w._on_clipboard_url("https://github.com/x/y")

    def test_on_engine_line(self):
        self.w._on_engine_line(0, "test line", LogLevel.INFO.value)


if __name__ == "__main__":
    unittest.main()
