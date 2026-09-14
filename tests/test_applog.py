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

    def test_bad_path_silent(self):
        # 非法路径不抛异常（静默降级）
        applog.info("should-not-crash", self.tmp / "no_dir_zzz" / "x" / "a.log")

    def test_singleton(self):
        a = applog.get_logger()
        b = applog.get_logger()
        self.assertIs(a, b)


if __name__ == "__main__":
    unittest.main(verbosity=2)
