# -*- coding: utf-8 -*-
"""设置页可滚动分组布局测试（离屏 Qt）。

覆盖「设置与日志」Tab 改版约束：
- QScrollArea + 新分组「关于与更新」存在；
- 全部 12 个既有控件名与类型保留（不得因重构丢失）；
- 新增「自检环境」按钮 _run_selftest：git 存在 / 缺失 两条路径；
- 既有 _save_* 绑定不因布局改版被破坏。
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

from PyQt6.QtWidgets import QApplication, QCheckBox, QLabel, QLineEdit, QPlainTextEdit, \
    QPushButton, QScrollArea, QSpinBox

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.ui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)

# 既有控件名 → 对应 Qt 类型（与 main_window._build_settings_tab 保持一致）
EXPECTED_CONTROLS = {
    "ck_autostart": QCheckBox,
    "btn_check_update": QPushButton,
    "spin_concurrency": QSpinBox,
    "spin_fetch_timeout": QSpinBox,
    "spin_retries": QSpinBox,
    "edit_proxy": QLineEdit,
    "ck_unshallow": QCheckBox,
    "edit_token": QLineEdit,
    "log_count": QLabel,
    "btn_save_log": QPushButton,
    "btn_clear_log": QPushButton,
    "log_view": QPlainTextEdit,
}


class TestSettingsScroll(unittest.TestCase):
    """设置与日志 Tab：滚动区 / 控件保留 / 自检 / 绑定不回归。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = SettingsStore(self.tmp / "settings.json")
        self.w = MainWindow(data_dir=self.tmp, db=Database(self.tmp / "t.db"),
                            settings=self.store)
        self.w.show()

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

    # ------------------------------------------------------------ 滚动区
    def test_scroll_area_present(self):
        """tab_settings 下能找到 QScrollArea，且 widgetResizable 已开启。"""
        areas = self.w.tab_settings.findChildren(QScrollArea)
        self.assertGreaterEqual(len(areas), 1, "设置 Tab 应至少含 1 个 QScrollArea")
        self.assertTrue(areas[0].widgetResizable(),
                        "滚动区应 setWidgetResizable(True)")
        self.assertIsNotNone(areas[0].widget(), "滚动区应挂载内部 QWidget")

    # ------------------------------------------------------------ 控件保留
    def test_all_controls_preserved(self):
        """12 个既有控件全部存在且类型正确（改版不得丢控件）。"""
        for name, ctype in EXPECTED_CONTROLS.items():
            self.assertTrue(hasattr(self.w, name), f"控件 {name} 应存在")
            self.assertIsInstance(getattr(self.w, name), ctype,
                                  f"{name} 类型应为 {ctype.__name__}")

    # ------------------------------------------------------------ 自检按钮
    def test_selftest_button_runs(self):
        """_run_selftest：git 存在 → 弹窗信息含 git 路径与并发。"""
        self.assertTrue(hasattr(self.w, "btn_selftest"), "应存在 btn_selftest")
        self.assertIsInstance(self.w.btn_selftest, QPushButton)

        proc = mock.Mock()
        proc.stdout = "git version 2.50.1.windows.1\n"
        proc.stderr = ""
        informed = []

        with mock.patch("shutil.which", return_value=r"C:\Git\bin\git.exe") as m_which, \
                mock.patch("subprocess.run", return_value=proc) as m_run, \
                mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
            m_msg.information.side_effect = lambda *a, **k: informed.append(a)
            self.w._run_selftest()

        m_which.assert_called_once_with("git")
        m_run.assert_called_once()
        self.assertEqual(m_msg.information.call_count, 1, "应弹 1 次自检信息窗")
        text = m_msg.information.call_args.args[2]
        self.assertIn("git", text)
        self.assertIn("并发", text)

    def test_selftest_git_missing(self):
        """_run_selftest：git 缺失 → 提示未检测到 git（不抛异常）。"""
        informed = []
        with mock.patch("shutil.which", return_value=None), \
                mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
            m_msg.information.side_effect = lambda *a, **k: informed.append(a)
            self.w._run_selftest()   # 不应抛异常

        self.assertEqual(m_msg.information.call_count, 1)
        text = m_msg.information.call_args.args[2]
        self.assertIn("未检测到", text)

    # ------------------------------------------------------------ 绑定不回归
    def test_settings_controls_still_bound(self):
        """改版后 spin_concurrency.valueChanged 仍触发 _save_concurrency。"""
        self.w.spin_concurrency.setValue(5)
        self.assertEqual(self.w.settings.concurrency, 5,
                         "valueChanged 应触发 _save_concurrency 同步 settings")


if __name__ == "__main__":
    unittest.main(verbosity=2)