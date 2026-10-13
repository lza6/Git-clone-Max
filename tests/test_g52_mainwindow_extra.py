"""G52 主窗口补充覆盖：update_all / 行转 spec / 启动恢复 / 归档行 / 热键 / 剪贴板 / 引擎行日志。"""
import json
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
        # 空库 → 必须走 information 提示分支，且**不得**启动任何任务
        with mock.patch.object(QMessageBox, "information") as info, \
             mock.patch.object(MainWindow, "_launch") as launch:
            self.w.busy = False
            self.w.update_all()
            info.assert_called_once()
            self.assertIn("没有已记录的仓库", info.call_args[0][2])
            launch.assert_not_called()

    def test_update_all_busy(self):
        # busy=True → 走「任务运行中」分支，不得读库、不得启动
        with mock.patch.object(QMessageBox, "information") as info, \
             mock.patch.object(MainWindow, "_launch") as launch:
            self.w.busy = True
            try:
                self.w.update_all()
            finally:
                self.w.busy = False
            info.assert_called_once()
            self.assertIn("有任务正在运行", info.call_args[0][2])
            launch.assert_not_called()

    def test_startup_recovery_prefill_empty(self):
        # 无残留 → 不得回填输入区、不得发 WARN 日志
        prog = self.w.progress_path
        prog.parent.mkdir(parents=True, exist_ok=True)
        prog.write_text(json.dumps({"in_progress": {}, "failed": []}), encoding="utf-8")
        self.w.repo_input.setPlainText("")
        with mock.patch.object(self.w, "_emit_log") as emit:
            self.w._startup_recovery_prefill()
            emit.assert_not_called()
        self.assertEqual(self.w.repo_input.toPlainText(), "")

    def test_startup_recovery_prefill_restores_pending(self):
        # 有未完成项 → 回填其 URL 且发 WARN（真实副作用，不只是「不崩」）
        prog = self.w.progress_path
        prog.parent.mkdir(parents=True, exist_ok=True)
        prog.write_text(json.dumps({
            "in_progress": {"github.com/o/r": {"status": "running"}},
            "failed": [],
        }), encoding="utf-8")
        self.w.repo_input.setPlainText("")
        self.w._startup_recovery_prefill()
        text = self.w.repo_input.toPlainText()
        self.assertIn("github.com/o/r", text)
        self.assertIn("https://github.com/o/r", text)

    def test_archive_line_empty(self):
        # 空输入 → 必须发 WARN 且不建 archives 目录
        self.w.repo_input.setPlainText("")
        arch = self.w.data_dir / "archives"
        with mock.patch.object(self.w, "_emit_log") as emit:
            self.w._archive_current_line()
            emit.assert_called_once()
            self.assertEqual(emit.call_args[0][1], LogLevel.WARN)
            self.assertIn("输入区为空", emit.call_args[0][2])
        self.assertFalse(arch.exists())

    def test_archive_line_with_url(self):
        self.w.repo_input.setPlainText("https://github.com/a/b.git")
        arch = self.w.data_dir / "archives"
        started = []
        with mock.patch("gcm.git.archive.download_archive") as da, \
             mock.patch("threading.Thread") as th:
            th.side_effect = lambda **k: started.append(k) or mock.Mock()
            self.w._archive_current_line()
            # 真实行为：目标路径按 URL 末段清洗后落在 archives/ 下
            self.assertTrue(arch.is_dir())
            self.assertEqual(da.call_count, 0, "后台线程未启动前不得同步下载")
            self.assertEqual(len(started), 1)
            self.assertTrue(started[0]["daemon"])
        # 目标路径可由 url 末段推导；下载被 mock → 不应真的落文件
        self.assertFalse((arch / "b.zip").exists())

    def test_archive_line_sanitizes_name(self):
        # G67-2 回归守卫：URL 末段含 Windows 非法字符不得直接落成文件名
        from gcm.app.url_lib import sanitize_name
        url = "https://github.com/a/we:ird*name?.git"
        self.w.repo_input.setPlainText(url)
        arch = self.w.data_dir / "archives"
        captured = []
        with mock.patch("gcm.git.archive.download_archive") as da, \
             mock.patch("threading.Thread") as th:
            th.side_effect = lambda **k: captured.append(k) or mock.Mock()
            self.w._archive_current_line()
            # 线程启动前不得同步下载
            self.assertEqual(da.call_count, 0)
            self.assertEqual(len(captured), 1)
            self.assertTrue(captured[0]["daemon"])
        # 真实落点 = archives/<sanitize_name(末段去 .git)>.zip，且已建目录
        expected = arch / f"{sanitize_name('we:ird*name?')}.zip"
        self.assertTrue(arch.is_dir(), "archives 目录应已创建")
        self.assertEqual(expected.name, "we_ird_name_.zip")
        for bad in ':*?"<>|':
            self.assertNotIn(bad, expected.name)
        # 未真正下载 → 不应留下 zip 文件（下载被 mock）
        self.assertFalse(expected.exists())

    def test_hotkey_start_cancel(self):
        # busy=False → 应真正触发 start_all（转发到 progress_panel）
        with mock.patch.object(self.w.progress_panel, "_hotkey_start_cancel") as hk:
            self.w._hotkey_start_cancel()
            hk.assert_called_once()

    def test_on_clipboard_url(self):
        self.w.repo_input.setPlainText("")
        with mock.patch.object(self.w, "_emit_log") as emit:
            self.w._on_clipboard_url("https://github.com/x/y")
            emit.assert_called_once()
            self.assertIn("https://github.com/x/y", emit.call_args[0][2])
            self.assertEqual(emit.call_args[0][1], LogLevel.INFO)
        self.assertIn("https://github.com/x/y", self.w.repo_input.toPlainText())
        self.assertIn("https://github.com/x/y", self.w.statusBar().currentMessage())

    def test_on_engine_line(self):
        with mock.patch.object(self.w.progress_panel, "_on_engine_line") as fwd:
            self.w._on_engine_line(0, "test line", LogLevel.INFO.value)
            fwd.assert_called_once_with(0, "test line", LogLevel.INFO.value)


if __name__ == "__main__":
    unittest.main()
