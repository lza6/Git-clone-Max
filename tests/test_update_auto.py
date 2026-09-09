# -*- coding: utf-8 -*-
"""启动后静默检查更新 + Release body 动态化 的测试。

- publish_release._build_body：body 模板随 gcm.__version__ 动态生成。
- main_window 手动检查更新槽回归（check_update_now 弹窗路径）。
- main_window 启动后静默检查 _check_update_silent：有新版 → 托盘 notify +
  日志提示；err / 无新版 → 静默不通知。
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
sys.path.insert(0, str(ROOT / "scripts"))

import publish_release as pr  # noqa: E402

from gcm import __version__  # noqa: E402
from gcm.db.repo_db import Database  # noqa: E402
from gcm.db.settings import SettingsStore  # noqa: E402
from gcm.ui.main_window import MainWindow  # noqa: E402

from PyQt6.QtWidgets import QApplication, QMessageBox  # noqa: E402

# 共享 QApplication 实例（离屏）
_app = QApplication.instance() or QApplication(sys.argv)


class TestBuildBody(unittest.TestCase):
    def test_build_body_contains_version(self):
        body = pr._build_body()
        self.assertIsInstance(body, str)
        self.assertIn(__version__, body)
        self.assertIn("并行下载", body)
        self.assertIn("增量更新", body)
        self.assertIn("冲突保护", body)
        # 模板应包含"## 版本 {__version__}" 小节头
        self.assertIn(f"## 版本 {__version__}", body)

    def test_main_uses_build_body(self):
        """Release 新建路径：create_git_release 收到的 message 来自 _build_body。"""
        # 直接断言 main 调用的是 _build_body() 的返回值（pr.BODY 与它同源同值）
        self.assertEqual(pr._build_body(), pr.BODY)
        # 主路径硬编码绑定（换 message 来源时此测试会先失败）
        src = Path(pr.__file__).read_text(encoding="utf-8")
        self.assertIn("message=_build_body()", src)


class TestCheckUpdateSilent(unittest.TestCase):
    """_check_update_silent：有新版 / err / 无新版 三路。"""

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

    class FakeTray:
        """托盘替身：记录 notify 调用。"""

        def __init__(self):
            self.notified = []

        def notify(self, title, message):
            self.notified.append((title, message))

    def test_check_update_silent_new(self):
        w = self.w
        tray = self.FakeTray()
        w.tray = tray
        with mock.patch("gcm.app.updater.check_latest",
                        return_value=(True, "v9.9.9", "https://example.com/x", "")):
            w._check_update_silent()
        self.assertEqual(len(tray.notified), 1)
        self.assertIn("v9.9.9", tray.notified[0][1])
        log_text = w.log.to_plain_text()
        self.assertIn("发现新版本 v9.9.9", log_text)

    def test_check_update_silent_error(self):
        w = self.w
        tray = self.FakeTray()
        w.tray = tray
        with mock.patch("gcm.app.updater.check_latest",
                        return_value=(False, "", "", "检查更新失败：boom")):
            w._check_update_silent()
        self.assertEqual(tray.notified, [])
        self.assertNotIn("发现新版本", w.log.to_plain_text())

    def test_check_update_silent_no_new(self):
        w = self.w
        tray = self.FakeTray()
        w.tray = tray
        with mock.patch("gcm.app.updater.check_latest",
                        return_value=(False, "", "", "")):
            w._check_update_silent()
        self.assertEqual(tray.notified, [])
        self.assertNotIn("发现新版本", w.log.to_plain_text())


class TestCheckUpdateNow(unittest.TestCase):
    """手动按钮槽回归：err → information 弹窗被调。"""

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

    def test_check_update_now_button(self):
        with mock.patch("gcm.app.updater.check_latest",
                        return_value=(False, "", "", "网络错误")) as m_check, \
                mock.patch("gcm.ui.main_window.QMessageBox") as m_msg:
            m_msg.StandardButton.Yes = QMessageBox.StandardButton.Yes
            m_msg.StandardButton.No = QMessageBox.StandardButton.No
            self.w.check_update_now()
        m_check.assert_called_once()
        m_msg.information.assert_called_once()
        self.assertIn("网络错误", m_msg.information.call_args.args[2])


if __name__ == "__main__":
    unittest.main(verbosity=2)
