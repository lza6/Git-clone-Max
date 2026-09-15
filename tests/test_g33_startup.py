# -*- coding: utf-8 -*-
"""G33-6 启动耗时自检 单测（离屏 Qt）。

覆盖：
- MainWindow 构造后记录启动耗时（_startup_ms >= 0）
- _run_selftest 输出含「启动耗时」行（>3000ms 时标注较慢）
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


class TestStartupTiming(unittest.TestCase):
    """启动耗时记录 + 自检输出。"""

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

    def test_startup_ms_recorded(self):
        """构造后 _startup_ms 为非负整数。"""
        self.assertIsInstance(self.w._startup_ms, int)
        self.assertGreaterEqual(self.w._startup_ms, 0)

    def test_selftest_output_contains_startup_ms(self):
        """自检信息弹窗含「启动耗时」行。"""
        proc = mock.Mock()
        proc.stdout = "git version 2.50.1.windows.1\n"
        proc.stderr = ""
        informed = []
        with mock.patch("shutil.which", return_value=r"C:\Git\bin\git.exe"), \
                mock.patch("subprocess.run", return_value=proc), \
                mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
            m_msg.information.side_effect = lambda *a, **k: informed.append(a)
            self.w._run_selftest()
        self.assertEqual(m_msg.information.call_count, 1)
        text = m_msg.information.call_args.args[2]
        self.assertIn("启动耗时", text)
        self.assertIn("ms", text)

    def test_selftest_applog_written(self):
        """自检完成后写 app.log（selftest|startup_ms=…）。"""
        proc = mock.Mock()
        proc.stdout = "git version 2.50.1\n"
        proc.stderr = ""
        log_path = self.tmp / "app.log"
        with mock.patch("shutil.which", return_value=r"C:\Git\bin\git.exe"), \
                mock.patch("subprocess.run", return_value=proc), \
                mock.patch("gcm.ui.main_window.QMessageBox"):
            self.w._run_selftest(log_path=log_path)
        self.assertTrue(log_path.exists(), "应写 app.log")
        self.assertIn("startup_ms=", log_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
