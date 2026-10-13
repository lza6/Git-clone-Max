# -*- coding: utf-8 -*-
"""G22-5/G21-3 结构化应用日志（applog）单测。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app import applog


class TestApplog(unittest.TestCase):
    def setUp(self):
        applog.reset_for_tests()
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        applog.reset_for_tests()

    def test_write_and_rotate_setup(self):
        p = self.tmp / "app.log"
        applog.info("hello|world", p)
        applog.warn("warn-msg", p)
        applog.error("err-msg", p)
        text = p.read_text(encoding="utf-8")
        self.assertIn("hello|world", text)
        self.assertIn("warn-msg", text)
        self.assertIn("err-msg", text)
        # 结构化格式：时间|级别|模块|消息
        first = text.splitlines()[0]
        parts = first.split("|")
        self.assertGreaterEqual(len(parts), 4)
        self.assertIn(parts[1], ("INFO", "WARNING", "ERROR"))

    def test_lazy_init_without_path_then_with(self):
        # 先无 path 调用（无 handler 不落盘），再带 path：仍能落盘
        applog.info("no-file-yet")
        p = self.tmp / "app.log"
        applog.info("with-file", p)
        self.assertIn("with-file", p.read_text(encoding="utf-8"))

    def test_missing_dir_is_autocreated(self):
        # 目录不存在 → get_logger 会 mkdir(parents=True) 自动建目录并正常落盘
        target = self.tmp / "no_dir_zzz" / "x" / "a.log"
        applog.info("auto-created", target)
        self.assertTrue(target.exists())
        self.assertIn("auto-created", target.read_text(encoding="utf-8"))

    def test_bad_path_silent(self):
        # 真正不可创建的路径（父级是个文件）→ 静默降级，不抛异常
        blocker = self.tmp / "afile"
        blocker.write_text("x", encoding="utf-8")
        target = blocker / "sub" / "a.log"
        applog.info("should-not-crash", target)  # 不应抛异常
        self.assertFalse(target.exists())
        # 降级后 logger 仍可用（不因坏路径把单例写坏）
        self.assertIsNotNone(applog.get_logger())
        # 好路径仍能正常落盘（证明降级未污染后续调用）
        good = self.tmp / "good.log"
        applog.info("after-bad-path", good)
        self.assertIn("after-bad-path", good.read_text(encoding="utf-8"))

    def test_singleton(self):
        a = applog.get_logger()
        b = applog.get_logger()
        self.assertIs(a, b)
    def test_multi_path_switches_handler(self):
        """G50-3：显式不同 log_path 也挂新 handler（单例已有 handler 不吞新路径）。"""
        p1 = self.tmp / "a.log"
        applog.info("msg-1", p1)
        self.assertTrue(p1.exists())
        applog.info("msg-1b", p1)  # 同路径：复用已挂 handler
        self.assertTrue(p1.exists())
        p2 = self.tmp / "b.log"
        applog.info("msg-2", p2)   # 不同路径：另挂 handler 并落盘
        self.assertTrue(p2.exists(), "不同路径应挂新 handler 并落盘")
        self.assertIn("msg-2", p2.read_text(encoding="utf-8"))



if __name__ == "__main__":
    unittest.main(verbosity=2)
