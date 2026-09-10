# -*- coding: utf-8 -*-
"""G04-4 下载限速测试：settings 注入 git http.lowSpeedLimit/lowSpeedTime。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestRateLimit(unittest.TestCase):
    def test_env_injects_low_speed(self):
        from gcm.git.service import GitService
        svc = GitService(Path(tempfile.mkdtemp()), rate_limit_kbps=500)
        env = svc._env()
        self.assertEqual(env["GIT_CONFIG_COUNT"], "2")
        self.assertIn("http.lowSpeedLimit", env["GIT_CONFIG_KEY_0"])
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], "500")
        self.assertIn("http.lowSpeedTime", env["GIT_CONFIG_KEY_1"])
        self.assertEqual(env["GIT_CONFIG_VALUE_1"], "30")

    def test_no_rate_no_extra(self):
        from gcm.git.service import GitService
        svc = GitService(Path(tempfile.mkdtemp()), rate_limit_kbps=0)
        env = svc._env()
        self.assertTrue(env is None or "GIT_CONFIG_COUNT" not in env)

    def test_rate_with_token_merges(self):
        from gcm.git.service import GitService
        svc = GitService(Path(tempfile.mkdtemp()), token="ghp_x", rate_limit_kbps=100)
        svc._current_host = "github.com"
        env = svc._env()
        self.assertEqual(env["GIT_CONFIG_COUNT"], "3")
        self.assertIn("http.extraHeader", env["GIT_CONFIG_KEY_0"])
        self.assertIn("http.lowSpeedLimit", env["GIT_CONFIG_KEY_1"])


class TestRateSetting(unittest.TestCase):
    def test_rate_limit_kbps_persists(self):
        from gcm.db.settings import SettingsStore, Settings
        d = Path(tempfile.mkdtemp())
        st = SettingsStore(d / "settings.json")
        s = st.load()
        s.rate_limit_kbps = 800
        st.save(s)
        self.assertEqual(st.load().rate_limit_kbps, 800)
        self.assertEqual(Settings().rate_limit_kbps, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)