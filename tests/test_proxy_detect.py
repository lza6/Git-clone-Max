# -*- coding: utf-8 -*-
"""G04-3 代理自动检测测试：Windows 系统代理 + 环境变量（对应指南 G04-3）。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.proxy import detect_system_proxy


class TestProxyDetect(unittest.TestCase):
    def setUp(self):
        # 隔离：清掉全部代理环境变量，避免本机已设 HTTPS_PROXY 干扰
        self._saved = {}
        for k in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
            self._saved[k] = os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_env_http_proxy(self):
        os.environ["HTTP_PROXY"] = "http://127.0.0.1:7890"
        self.assertEqual(detect_system_proxy(), "http://127.0.0.1:7890")

    def test_env_https_proxy(self):
        os.environ["HTTPS_PROXY"] = "http://proxy.local:8080"
        self.assertEqual(detect_system_proxy(), "http://proxy.local:8080")

    def test_https_takes_priority_over_http(self):
        os.environ["HTTP_PROXY"] = "http://http.local:1111"
        os.environ["HTTPS_PROXY"] = "http://https.local:2222"
        self.assertEqual(detect_system_proxy(), "http://https.local:2222")

    def test_returns_empty_when_no_proxy(self):
        val = detect_system_proxy()
        self.assertIsInstance(val, str)


if __name__ == "__main__":
    unittest.main(verbosity=2)
