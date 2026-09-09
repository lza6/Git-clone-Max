# -*- coding: utf-8 -*-
"""设置页 UI 测试（离屏 Qt）：自动清空开关 + Tooltip + 设置持久化。

覆盖：start_all 使用 settings.auto_clear 作为 _launch.clear_input；
开关控件与 settings 双向绑定；并发/代理/Token 控件 Tooltip 非空；
修改开关后新窗口可从同一 settings.json 恢复。
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

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.ui.main_window import MainWindow

# 共享 QApplication 实例（离屏）
_app = QApplication.instance() or QApplication(sys.argv)


class TestSettingUi(unittest.TestCase):
    """MainWindow 设置页：自动清空开关 / Tooltip / 持久化。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = SettingsStore(self.tmp / "settings.json")
        self.w = MainWindow(data_dir=self.tmp, db=Database(self.tmp / "t.db"),
                            settings=self.store)

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

    # ------------------------------------------------------------ 开关绑定
    def test_auto_clear_checkbox_binds(self):
        """开关初值与 settings 一致；_save_auto_clear 同步并持久化。"""
        # 默认 auto_clear=True → 控件选中
        self.assertEqual(self.w.ck_auto_clear.isChecked(), self.w.settings.auto_clear)
        self.assertTrue(self.w.settings.auto_clear)

        # 取消勾选 → settings 同步为 False
        self.w._save_auto_clear(False)
        self.assertIs(self.w.settings.auto_clear, False)

        # 重新加载同一 settings.json → 仍为 False（持久化）
        reloaded = SettingsStore(self.tmp / "settings.json").load()
        self.assertIs(reloaded.auto_clear, False)

        # 重新勾选 → settings 同步为 True
        self.w._save_auto_clear(True)
        reloaded2 = SettingsStore(self.tmp / "settings.json").load()
        self.assertIs(reloaded2.auto_clear, True)

    # ------------------------------------------------------------ start_all 联动
    def test_start_all_uses_auto_clear(self):
        """start_all 传给 _launch 的 clear_input 跟随 settings.auto_clear。"""
        with mock.patch("gcm.ui.main_window.QMessageBox"):
            # 默认 True
            with mock.patch.object(MainWindow, "_launch") as m_launch:
                self.w.settings.auto_clear = True
                self.w.repo_input.setPlainText("https://github.com/a/b")
                self.w.start_all()
            self.assertIs(m_launch.call_args.kwargs["clear_input"], True)

            # 关闭自动清空 → False
            self.w.busy = False
            self.w.settings.auto_clear = False
            with mock.patch.object(MainWindow, "_launch") as m_launch:
                self.w.repo_input.setPlainText("https://github.com/a/b")
                self.w.start_all()
            self.assertIs(m_launch.call_args.kwargs["clear_input"], False)
            self.assertFalse(self.w.busy)

    # ------------------------------------------------------------ Tooltip
    def test_tooltips_present(self):
        """关键设置控件 Tooltip 非空。"""
        for w in (self.w.spin_concurrency, self.w.edit_proxy, self.w.edit_token):
            self.assertTrue(w.toolTip().strip(), f"{w} 应设置非空 Tooltip")

    # ------------------------------------------------------------ 持久化往返
    def test_settings_persist_roundtrip(self):
        """修改开关 → 新窗口读同一 settings.json 恢复勾选状态。"""
        # 通过控件触发勾选/取消（模拟用户点击）
        self.w.ck_auto_clear.setChecked(False)
        self.assertIs(self.w.settings.auto_clear, False)

        # 新窗口读同一 settings.json
        w2 = MainWindow(data_dir=self.tmp,
                        db=Database(self.tmp / "t2.db"),
                        settings=SettingsStore(self.tmp / "settings.json"))
        try:
            self.assertFalse(w2.ck_auto_clear.isChecked())
            self.assertIs(w2.settings.auto_clear, False)
        finally:
            w2.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)