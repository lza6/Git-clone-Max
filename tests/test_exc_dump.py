# -*- coding: utf-8 -*-
"""G06-3 异常兜底测试：sys.excepthook 捕获写日志、Qt 消息拦截、safe-mode（对应指南 G06-3）。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.exc_dump import install_excepthook, write_error_log


class TestExcDump(unittest.TestCase):
    def test_write_error_log(self):
        d = Path(tempfile.mkdtemp())
        path = d / "error.log"
        try:
            1 / 0
        except Exception as e:
            write_error_log(path, e, context={"extra": "x"})
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("ZeroDivisionError", text)
        self.assertIn("Traceback", text)

    def test_install_excepthook_captures(self):
        d = Path(tempfile.mkdtemp())
        log_path = d / "error.log"
        install_excepthook(log_path=log_path)
        # 触发一次未捕获异常
        try:
            1 / 0
        except ZeroDivisionError as e:
            sys.excepthook(type(e), e, e.__traceback__)
        self.assertTrue(log_path.exists())
        self.assertIn("ZeroDivisionError", log_path.read_text(encoding="utf-8"))

    def test_install_idempotent(self):
        d = Path(tempfile.mkdtemp())
        install_excepthook(log_path=d / "error.log")
        install_excepthook(log_path=d / "error.log")  # 重复安装不应崩


if __name__ == "__main__":
    unittest.main(verbosity=2)
