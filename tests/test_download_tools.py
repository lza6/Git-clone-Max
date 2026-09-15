# -*- coding: utf-8 -*-
"""download_tools.py 独立模块单测（G33-8 拆分新增）。

覆盖 save_log / export_report / clear_url_history / repo_input_menu /
choose_target / open_target 的取消与成功路径。均以 main_window.QFileDialog /
main_window.QMessageBox 模块属性为 mock 目标（与该模块实现约定一致）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from unittest import mock

from PyQt6.QtWidgets import QApplication

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.ui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)


class TestDownloadTools(unittest.TestCase):
    """download_tools 对话框/动作路径（经由 MainWindow 方法入口）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.w = MainWindow(data_dir=self.tmp,
                            db=Database(self.tmp / "t.db"),
                            settings=SettingsStore(self.tmp / "settings.json"))

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

    # ------------------------------------------------------------ save_log
    def test_save_log_cancel_no_write(self):
        """取消 → 不写文件、不追加日志。"""
        self.w.log.clear()
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd:
            m_fd.getSaveFileName.return_value = ("", "文本文件 (*.txt)")
            self.w.save_log()
        self.assertFalse((self.tmp / "clone_log.txt").exists())
        self.assertNotIn("日志已导出", self.w.log.to_plain_text())

    def test_save_log_write_failure_critical(self):
        """写入抛异常 → 弹 critical，不崩。"""
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd, \
                mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
            m_fd.getSaveFileName.return_value = (str(self.tmp / "x.txt"), "*.txt")
            with mock.patch("pathlib.Path.write_text",
                            side_effect=OSError("disk full")):
                self.w.save_log()  # 不应抛
        m_msg.critical.assert_called_once()

    # ------------------------------------------------------------ export_report
    def test_export_report_csv_path(self):
        """导出 .csv → export_csv 被调用且 n 行日志/状态栏更新。"""
        out = self.tmp / "rep.csv"
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd, \
                mock.patch("gcm.reports.export_csv", return_value=7) as m_csv:
            m_fd.getSaveFileName.return_value = (str(out), "CSV (*.csv);;Markdown (*.md)")
            self.w.export_report()
        m_csv.assert_called_once()
        self.assertIn("报表已导出", self.w.log.to_plain_text())

    def test_export_report_cancel(self):
        """取消导出 → 直接返回，不调 export_*。"""
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd, \
                mock.patch("gcm.reports.export_csv") as m_csv, \
                mock.patch("gcm.reports.export_markdown") as m_md:
            m_fd.getSaveFileName.return_value = ("", "")
            self.w.export_report()
        m_csv.assert_not_called()
        m_md.assert_not_called()

    def test_export_report_markdown_path(self):
        """导出 .md → export_markdown 被调用。"""
        out = self.tmp / "rep.md"
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd, \
                mock.patch("gcm.reports.export_markdown", return_value=3) as m_md:
            m_fd.getSaveFileName.return_value = (str(out), "CSV (*.csv);;Markdown (*.md)")
            self.w.export_report()
        m_md.assert_called_once()

    def test_export_report_failure_critical(self):
        """导出抛异常 → critical 弹窗，不崩。"""
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd, \
                mock.patch("gcm.reports.export_csv", side_effect=OSError("boom")), \
                mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
            m_fd.getSaveFileName.return_value = (str(self.tmp / "r.csv"), "")
            self.w.export_report()
        m_msg.critical.assert_called_once()

    # ------------------------------------------------------------ history menu
    def test_repo_input_menu_with_history(self):
        """有历史 → 菜单含「从历史粘贴」子菜单与「清空历史」。"""
        self.w.url_history.add("https://github.com/a/b")
        with mock.patch("PyQt6.QtWidgets.QMenu.exec") as m_exec, \
                mock.patch("gcm.ui.main_window.QMessageBox"):
            self.w._repo_input_menu(self.w.repo_input.rect().topLeft())
        m_exec.assert_called_once()

    def test_clear_url_history_emits_log(self):
        """清空历史 → 日志追加「URL 历史已清空」。"""
        self.w.url_history.add("https://github.com/a/b")
        self.w._clear_url_history()
        self.assertIn("URL 历史已清空", self.w.log.to_plain_text())
        self.assertEqual(self.w.url_history.items(), [])

    # ------------------------------------------------------------ choose/open target
    def test_choose_target_cancel_keeps_text(self):
        """取消选择 → target_edit 不变。"""
        original = self.w.target_edit.text()
        with mock.patch("gcm.ui.main_window.QFileDialog") as m_fd:
            m_fd.getExistingDirectory.return_value = ""
            self.w.choose_target()
        self.assertEqual(self.w.target_edit.text(), original)

    def test_open_target_missing_warns(self):
        """目录不存在 → 警告，不调 startfile。"""
        self.w.target_edit.setText(str(self.tmp / "nope"))
        with mock.patch("os.startfile") as m_start, \
                mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
            self.w.open_target()
        m_start.assert_not_called()
        m_msg.warning.assert_called_once()
        self.assertIn("目录不存在", m_msg.warning.call_args.args[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)