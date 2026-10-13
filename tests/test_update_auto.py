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
        # G48 模板 H1 为 "# Git-clone-Max v{version} 发布说明"，版本号在标题中
        self.assertIn(f"Git-clone-Max v{__version__}", body)

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

    def _pump(self, predicate, timeout: float = 5.0):
        """泵事件循环直到 predicate() 为真（G67-9 静默检查改为工作线程后必需）。"""
        import time as _t
        app = QApplication.instance()
        deadline = _t.monotonic() + timeout
        while _t.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return True
            _t.sleep(0.01)
        app.processEvents()
        return predicate()

    def test_check_update_silent_new(self):
        w = self.w
        tray = self.FakeTray()
        w.tray = tray
        with mock.patch("gcm.app.updater.check_latest",
                        return_value=(True, "v9.9.9", "https://example.com/x", "")):
            w._check_update_silent()
            # G67-9：整段（含 HTTP 与内部 2s 重试）已挪进 daemon 线程，
            # 托盘提示与日志经信号/既有线程约定回主线程 → 必须泵事件等待。
            self.assertTrue(self._pump(lambda: len(tray.notified) == 1),
                            "新版本托盘提示应在工作线程完成后到达")
            # 日志走 `_log_line` 信号桥 → GUI 线程写模型；再等一拍让它落到模型里
            self.assertTrue(self._pump(
                lambda: "发现新版本 v9.9.9" in w.log.to_plain_text()),
                f"新版本应写入日志模型；实际日志：{w.log.to_plain_text()[-200:]!r}")
        self.assertIn("v9.9.9", tray.notified[0][1])

    def test_check_update_silent_error(self):
        w = self.w
        tray = self.FakeTray()
        w.tray = tray
        with mock.patch("gcm.app.updater.check_latest",
                        return_value=(False, "", "", "检查更新失败：boom")):
            w._check_update_silent()
            self.assertFalse(self._pump(lambda: bool(tray.notified), timeout=1.0),
                             "出错时不得托盘提示")
        self.assertEqual(tray.notified, [])
        self.assertNotIn("发现新版本", w.log.to_plain_text())
        # G21-3：静默检查失败必须留痕（结构化 app.log）
        self.assertTrue((w.data_dir / "app.log").exists(), "静默失败应写 app.log 留痕")

    def test_check_update_silent_no_new(self):
        w = self.w
        tray = self.FakeTray()
        w.tray = tray
        with mock.patch("gcm.app.updater.check_latest",
                        return_value=(False, "", "", "")):
            w._check_update_silent()
            self.assertFalse(self._pump(lambda: bool(tray.notified), timeout=1.0),
                             "无新版本不得托盘提示")
            self.assertFalse(self._pump(
                lambda: "发现新版本" in w.log.to_plain_text(), timeout=1.0),
                "无新版本不得写「发现新版本」日志")


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
            # G67-9：网络请求已挪到工作线程，结果经 `update_checked` 信号回 GUI 线程。
            # 原测试假设「函数返回时弹窗已发生」，那是同步实现的产物，现在必须泵事件。
            self._pump(lambda: m_msg.information.called)
        m_check.assert_called()
        self.assertTrue(m_msg.information.called, "错误提示应经信号回到 GUI 线程后弹出")
        self.assertIn("网络错误", m_msg.information.call_args.args[2])

    def _pump(self, predicate, timeout: float = 5.0):
        """泵事件循环直到 predicate() 为真（G67-9 异步桥必需）。"""
        import time as _t
        app = QApplication.instance()
        deadline = _t.monotonic() + timeout
        while _t.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return True
            _t.sleep(0.01)
        app.processEvents()
        return predicate()


if __name__ == "__main__":
    unittest.main(verbosity=2)
