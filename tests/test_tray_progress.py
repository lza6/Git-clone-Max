# -*- coding: utf-8 -*-
"""G22-4 托盘进度计数：tooltip 随引擎结果实时更新（mock tray 图标对象）。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gcm.ui.tray import TrayController


class _FakeTrayIcon:
    """不依赖真实系统托盘：记录 setToolTip 调用。"""

    def __init__(self):
        self.tips = []

    def setToolTip(self, tip):
        self.tips.append(tip)

    def setContextMenu(self, m):
        pass

    def show(self):
        pass

    def activated(self, *a, **k):
        pass


class TestTrayProgressCounts(unittest.TestCase):
    def _ctrl(self):
        c = TrayController(parent=None)
        fake = _FakeTrayIcon()
        c.tray = fake
        return c, fake

    def test_counts_flow_to_tooltip(self):
        c, fake = self._ctrl()
        c.update_counts(reset=True)
        c.update_counts(running_delta=3)
        c._flush_tooltip()
        self.assertIn("运行中 3", fake.tips[-1])
        c.update_counts(ok_delta=2)
        c.update_counts(bad_delta=1)
        c._flush_tooltip()
        self.assertIn("运行中 3", fake.tips[-1])
        self.assertIn("完成 2", fake.tips[-1])
        self.assertIn("失败 1", fake.tips[-1])

    def test_idle_tooltip_after_reset(self):
        c, fake = self._ctrl()
        c.update_counts(ok_delta=5, bad_delta=2)
        c.update_counts(reset=True)
        c._flush_tooltip()
        self.assertEqual(fake.tips[-1], "Git-clone-Max")

    def test_throttle_single_shot(self):
        c, fake = self._ctrl()
        c.update_counts(running_delta=1)
        self.assertTrue(c._tip_timer.isActive())
        c.update_counts(running_delta=1)  # 节流窗口内不重复 start
        self.assertTrue(c._tip_timer.isActive())

    def test_no_tray_object_safe(self):
        c = TrayController(parent=None)
        c.tray = None
        c.update_counts(running_delta=2)  # 不得抛异常
        c._flush_tooltip()


if __name__ == "__main__":
    unittest.main(verbosity=2)
