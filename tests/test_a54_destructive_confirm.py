"""A5-4（R6 破坏性操作缺确认）守卫：6 个入口全部套「默认 No 确认范式」。

- U-1 choose_target 敏感目录确认框必须有 Yes|No 两键（旧 bug：只有 Ok 键，
  与 Yes 比较恒 False → 用户选择被静默丢弃）
- U-4 立即清理历史：删前 question 默认 No
- U-5 恢复默认设置：重置前 question 默认 No
- U-7 清空重试队列：清空前 question 默认 No
- U-18 清空输入区：>20 行先确认；改名「清空输入区」
- U-26 取消全部：运行中先确认；空闲直接放行
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

from PyQt6.QtWidgets import QApplication, QMessageBox

_app = QApplication.instance() or QApplication(sys.argv)

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.ui.main_window import MainWindow
from gcm.ui.retry_panel import RetryQueuePanel as RetryPanel

NO = QMessageBox.StandardButton.No
YES = QMessageBox.StandardButton.Yes


class _WinCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.w = MainWindow(data_dir=self.tmp,
                            db=Database(self.tmp / "t.db"),
                            settings=SettingsStore(self.tmp / "settings.json"))

    def tearDown(self):
        self.w.close()
        self.w.deleteLater()


class TestU1SensitiveDirConfirm(_WinCase):
    def test_confirm_dialog_has_yes_no_buttons(self):
        """U-1：敏感目录确认必须传 Yes|No 按钮（回归守卫：旧版只有 Ok 键）。"""
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd, \
                mock.patch("gcm.ui.download_tools._msgbox") as m_mb:
            m_fd.getExistingDirectory.return_value = os.environ.get("SystemRoot", r"C:\Windows")
            m_mb.return_value.StandardButton = QMessageBox.StandardButton
            m_mb.return_value.warning.return_value = YES
            self.w.choose_target()
            args, kwargs = m_mb.return_value.warning.call_args
            buttons = kwargs.get("buttons") or (args[3] if len(args) > 3 else None)
            default = kwargs.get("defaultButton") or (args[4] if len(args) > 4 else None)
            self.assertIsNotNone(buttons, "warning 必须显式传 buttons（旧 bug 正是没传）")
            self.assertEqual(default, NO, "默认按钮必须是 No")


class TestU4PruneHistoryConfirm(_WinCase):
    def test_default_no_blocks_prune(self):
        """U-4：question 返回 No → prune_history 不被调用。"""
        self.w.db.prune_history = mock.Mock(return_value=0)
        with mock.patch("gcm.ui.settings_panel.QMessageBox") as m_msg:
            m_msg.question.return_value = NO
            m_msg.StandardButton = QMessageBox.StandardButton
            self.w.settings_panel._clean_g53_history()
        self.w.db.prune_history.assert_not_called()

    def test_yes_prunes(self):
        self.w.db.prune_history = mock.Mock(return_value=7)
        with mock.patch("gcm.ui.settings_panel.QMessageBox") as m_msg:
            m_msg.question.return_value = YES
            m_msg.StandardButton = QMessageBox.StandardButton
            self.w.settings_panel._clean_g53_history()
        self.w.db.prune_history.assert_called_once()


class TestU5ResetDefaultsConfirm(_WinCase):
    def test_default_no_blocks_reset(self):
        """U-5：question 返回 No → 配置不被重置。"""
        self.w.settings.concurrency = 32
        self.w.settings_store.save(self.w.settings)
        with mock.patch("gcm.ui.settings_panel.QMessageBox") as m_msg:
            m_msg.question.return_value = NO
            m_msg.StandardButton = QMessageBox.StandardButton
            self.w.settings_panel._reset_defaults()
        self.assertEqual(self.w.settings_store.load().concurrency, 32,
                         "确认选 No 时配置必须原样保留")

    def test_yes_resets(self):
        self.w.settings.concurrency = 32
        self.w.settings_store.save(self.w.settings)
        with mock.patch("gcm.ui.settings_panel.QMessageBox") as m_msg:
            m_msg.question.return_value = YES
            m_msg.StandardButton = QMessageBox.StandardButton
            self.w.settings_panel._reset_defaults()
        self.assertEqual(self.w.settings_store.load().concurrency, 8)


class TestU26CancelAllConfirm(_WinCase):
    def test_busy_default_no_keeps_engine(self):
        """U-26：运行中 + 默认 No → engine.cancel_all 不被调用。"""
        eng = mock.Mock()
        eng.busy = True
        self.w.engine = eng
        with mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
            m_msg.question.return_value = NO
            m_msg.StandardButton = QMessageBox.StandardButton
            self.w.cancel_all()
        eng.cancel_all.assert_not_called()

    def test_idle_cancels_without_confirm(self):
        """空闲（无 engine）时不弹确认，直接走日志路径。"""
        self.w.engine = None
        with mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
            self.w.cancel_all()
        m_msg.question.assert_not_called()


class TestU18ClearInputConfirm(_WinCase):
    def test_many_lines_default_no_keeps_text(self):
        """U-18：>20 行 + No → 输入区保留。"""
        self.w.repo_input.setPlainText("\n".join(
            f"https://github.com/o{i}/r{i}" for i in range(25)))
        with mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
            m_msg.question.return_value = NO
            m_msg.StandardButton = QMessageBox.StandardButton
            self.w.confirm_clear_input()
        self.assertGreater(self.w.repo_input.blockCount(), 20)

    def test_few_lines_clears_without_confirm(self):
        self.w.repo_input.setPlainText("https://github.com/a/b")
        with mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
            self.w.confirm_clear_input()
        m_msg.question.assert_not_called()
        self.assertEqual(self.w.repo_input.toPlainText(), "")

    def test_button_renamed(self):
        """U-18：按钮文案必须是「清空输入区」（与行为一致）。"""
        self.assertIn("清空输入区", self.w.btn_clear.text())


class TestU7RetryQueueConfirm(unittest.TestCase):
    def test_clear_queue_asks_confirm_default_no(self):
        """U-7：清空重试队列前 question 默认 No；No → 队列不清。"""
        panel = RetryPanel.__new__(RetryPanel)  # 只测方法体，不走 __init__ 的 Qt 装配
        q = mock.Mock()
        q.count.return_value = 5
        logs = []
        with mock.patch.object(RetryPanel, "queue", lambda self: q), \
                mock.patch.object(RetryPanel, "_log", lambda self, m: logs.append(m)), \
                mock.patch.object(RetryPanel, "refresh", lambda self: None), \
                mock.patch("gcm.ui.retry_panel.QMessageBox") as m_msg:
            m_msg.question.return_value = NO
            m_msg.StandardButton = QMessageBox.StandardButton
            n = panel.clear_queue()
        q.clear.assert_not_called()
        self.assertEqual(n, 0, "确认 No 时未清空，返回 0（返回值契约=本次清空数）")

    def test_clear_queue_yes_clears(self):
        panel = RetryPanel.__new__(RetryPanel)
        q = mock.Mock()
        q.count.return_value = 5
        with mock.patch.object(RetryPanel, "queue", lambda self: q), \
                mock.patch.object(RetryPanel, "_log", lambda self, m: None), \
                mock.patch.object(RetryPanel, "refresh", lambda self: None), \
                mock.patch.object(RetryPanel, "changed", mock.Mock()), \
                mock.patch("gcm.ui.retry_panel.QMessageBox") as m_msg:
            m_msg.question.return_value = YES
            m_msg.StandardButton = QMessageBox.StandardButton
            panel.clear_queue()
        q.clear.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
