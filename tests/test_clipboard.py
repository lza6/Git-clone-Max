# -*- coding: utf-8 -*-
"""G09-1 剪贴板监听测试：检测 git URL → 信号提示；开关控制。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gcm.app.clipboard_watcher import ClipboardWatcher


class TestClipboardWatcher(unittest.TestCase):
    def setUp(self):
        _app.clipboard().clear()

    def test_detect_git_url(self):
        got = []
        w = ClipboardWatcher(parent=None, enabled=True, on_url=got.append)
        _app.clipboard().setText("https://gitlab.com/x/y")
        w.check_once()
        self.assertEqual(got, ["https://gitlab.com/x/y"])

    def test_ignore_non_url(self):
        got = []
        w = ClipboardWatcher(parent=None, enabled=True, on_url=got.append)
        _app.clipboard().setText("hello world not a url")
        w.check_once()
        self.assertEqual(got, [])

    def test_dedupe_same_url(self):
        got = []
        w = ClipboardWatcher(parent=None, enabled=True, on_url=got.append)
        _app.clipboard().setText("https://github.com/a/b")
        w.check_once()
        w.check_once()  # 同一 URL 不重复提示
        self.assertEqual(got, ["https://github.com/a/b"])

    def test_disabled_no_signal(self):
        got = []
        w = ClipboardWatcher(parent=None, enabled=False, on_url=got.append)
        _app.clipboard().setText("https://github.com/a/b")
        w.check_once()
        self.assertEqual(got, [])

    def test_github_url_detected(self):
        got = []
        w = ClipboardWatcher(parent=None, enabled=True, on_url=got.append)
        _app.clipboard().setText("看这个仓库 https://github.com/octo/hello 很不错")
        w.check_once()
        self.assertEqual(got, ["https://github.com/octo/hello"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestClipboardWatcherEdge(unittest.TestCase):
    """补齐 watcher 缺失行：start/stop、定时器、空/静态资源、回调异常、多 URL。"""

    def test_start_stop_and_timer(self):
        w = ClipboardWatcher(parent=None, enabled=True, on_url=lambda u: None)
        w.start()
        self.assertTrue(w._timer.isActive())
        w.stop()
        self.assertFalse(w._timer.isActive())
        w2 = ClipboardWatcher(parent=None, enabled=False)
        w2.start()  # disabled → 不启动
        self.assertFalse(w2._timer.isActive())

    def test_empty_clipboard_noop(self):
        got = []
        w = ClipboardWatcher(parent=None, enabled=True, on_url=got.append)
        _app.clipboard().clear()
        w.check_once()
        self.assertEqual(got, [])

    def test_static_resource_filtered(self):
        got = []
        w = ClipboardWatcher(parent=None, enabled=True, on_url=got.append)
        _app.clipboard().setText("https://cdn.example.com/assets/logo.png")
        w.check_once()
        self.assertEqual(got, [])

    def test_callback_exception_swallowed(self):
        def bad_cb(u):
            raise RuntimeError("cb fail")
        w = ClipboardWatcher(parent=None, enabled=True, on_url=bad_cb)
        _app.clipboard().setText("https://github.com/a/b")
        try:
            w.check_once()  # 回调异常不冒泡
        except Exception as e:
            self.fail(f"回调异常应被吞掉: {e}")

    def test_ssh_url_detected(self):
        got = []
        w = ClipboardWatcher(parent=None, enabled=True, on_url=got.append)
        _app.clipboard().setText("git@gitlab.com:grp/repo.git")
        w.check_once()
        self.assertEqual(got, ["git@gitlab.com:grp/repo.git"])
