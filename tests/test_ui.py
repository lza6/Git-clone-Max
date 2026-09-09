# -*- coding: utf-8 -*-
"""UI 集成测试（离屏 QtTest）：主窗口关键交互路径。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtWidgets import QApplication, QMessageBox

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus

_app = QApplication.instance() or QApplication(sys.argv)


class MainWindowHarness:
    """轻量桩：等价于 MainWindow 的可测交互，但隔离 Qt 弹窗。"""

    def __init__(self, window):
        self.w = window
        self.asked = []          # [(title, shown)]
        self.informed = []       # [(title, text)]

    def patch_dialogs(self):
        self._orig_question = QMessageBox.question
        self._orig_information = QMessageBox.information
        QMessageBox.question = self._fake_question
        QMessageBox.information = self._fake_information

    def _fake_question(self, parent, title, text, buttons=QMessageBox.StandardButton.Yes,
                       defaultButton=QMessageBox.StandardButton.No):
        self.asked.append((title, text))
        # 默认返回 No：模拟用户拒绝（不真实启动任务，避免污染后续测试状态）
        return QMessageBox.StandardButton.No

    def _fake_information(self, parent, title, text, *args, **kwargs):
        self.informed.append((title, text))

    def restore_dialogs(self):
        QMessageBox.question = self._orig_question
        QMessageBox.information = self._orig_information


def _make_window():
    d = Path(tempfile.mkdtemp())
    db = Database(d / "t.db")
    ss = SettingsStore(d / "settings.json")
    from gcm.ui.main_window import MainWindow
    win = MainWindow(data_dir=d, db=db, settings=ss)
    return win, d


class TestMainWindow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._w, cls._d = _make_window()
        cls.h = MainWindowHarness(cls._w)
        cls.h.patch_dialogs()

    @classmethod
    def tearDownClass(cls):
        cls.h.restore_dialogs()
        cls._w.close()

    def _drain_log_timer(self):
        """结束测试前清空日志节流定时器积压，避免窗口销毁时 QTimer 引用跨线程告警/挂起。"""
        w = self._w
        if hasattr(w, "_log_batch_timer") and w._log_batch_timer.isActive():
            w._log_batch_timer.stop()
        if hasattr(w, "_log_batch"):
            w._log_batch.clear()
            w._flush_log_batch()

    def test_invalid_lines_prompts(self):
        try:
            w = self._w
            w.repo_input.setPlainText("https://github.com/a/b\nnot a url")
            w.start_all()
            # 应弹窗问忽略无效行
            self.assertTrue(any("无效地址" in t for t, _ in self.h.asked),
                            "无效地址应触发确认弹窗")
            # 用户点「否」→ 不应启动任务
            self.assertFalse(w.busy, "用户拒绝忽略无效行时不应启动任务")
        finally:
            self._drain_log_timer()

    def test_no_specs_informs(self):
        try:
            w = self._w
            w.repo_input.setPlainText("")
            w.start_all()
            # 空输入应弹出"没有可用的 GitHub 地址"（含"可用"字样）
            self.assertTrue(
                any(("没有可用的 GitHub 地址" in text) for _, text in self.h.informed),
                "空输入应提示无有效地址")
        finally:
            self._drain_log_timer()

    def test_update_all_empty_db_informs(self):
        """一键更新在数据库为空时提示，不崩溃。"""
        try:
            w = self._w
            # 清空 DB 再点一键更新 → 应弹「数据库中没有已记录的仓库」
            w.db.delete_all_repos()
            w.update_all()
            self.assertTrue(
                any("数据库中没有已记录的仓库" in text for _, text in self.h.informed),
                "空 DB 一键更新应提示")
            self.assertFalse(w.busy)
        finally:
            self._drain_log_timer()

    def test_valid_only_starts_and_clears(self):
        try:
            w = self._w
            w.repo_input.setPlainText("https://github.com/a/b\nhttps://github.com/c/d")
            # 直接调用 _launch：引擎去重/调度在后台线程跑真实 git，
            # 这里不等待网络（测试只关心 UI 状态机与复位逻辑）。
            specs = [RepoSpec("a", "b", "https://github.com/a/b.git"),
                     RepoSpec("c", "d", "https://github.com/c/d.git")]
            target = self._d / "clones"
            w._launch(specs, target_root=target, shallow=False, depth=1, clear_input=True)
            self.assertTrue(w.busy)
            self.assertFalse(w.btn_start.isEnabled())
            # 引擎已接管完成判定：直接触发 finished 槽（等价于全部 worker 完成后引擎 emit）
            for row in range(w.table.rowCount()):
                w.table.item(row, 2).setText("成功")
                w.table.item(row, 3).setText("已最新")
            w._pending_count = w.table.rowCount()
            w._on_engine_finished()
            self.assertFalse(w.busy)
            self.assertTrue(w.btn_start.isEnabled())
            self.assertEqual(w.repo_input.toPlainText(), "")
        finally:
            self._drain_log_timer()

    def test_settings_persist_concurrency(self):
        try:
            w = self._w
            w.spin_concurrency.setValue(3)
            w._save_concurrency(3)
            self.assertEqual(w.settings.concurrency, 3)
            self.assertEqual(w.pool.maxThreadCount(), 3)
            # 新窗口读同一 settings.json 应恢复
            from gcm.ui.main_window import MainWindow
            w2 = MainWindow(data_dir=self._d, db=Database(self._d / "t2.db"),
                            settings=SettingsStore(self._d / "settings.json"))
            self.assertEqual(w2.pool.maxThreadCount(), 3)
            w2.close()
        finally:
            self._drain_log_timer()

    def test_empty_action_label(self):
        try:
            w = self._w
            res = SyncResult(spec=RepoSpec("x", "y", "u"), status=SyncStatus.SUCCESS)
            res.action = SyncAction.EMPTY
            w._prepare_table(1)
            w._add_table_row(0, res.spec)
            w._on_worker_result(0, res)
            self.assertEqual(w.table.item(0, 3).text(), "空仓库")
        finally:
            self._drain_log_timer()


if __name__ == "__main__":
    unittest.main(verbosity=2)