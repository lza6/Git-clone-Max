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

class TestSafeCleanup(unittest.TestCase):
    """LOW 修复：只清理半成品（含 .git 或空目录），不误删非空非 git 目录。"""

    def test_safe_rmtree_removes_partial_git(self):
        from gcm.git.service import GitService
        base = Path(tempfile.mkdtemp())
        d = base / "partial"
        (d / ".git").mkdir(parents=True)
        (d / "a.txt").write_text("x", encoding="utf-8")
        svc = GitService(base / "root")
        svc._safe_rmtree_partial(d)
        self.assertFalse(d.exists(), "含 .git 的半成品应被清理")

    def test_safe_rmtree_removes_empty_dir(self):
        from gcm.git.service import GitService
        base = Path(tempfile.mkdtemp())
        d = base / "empty"
        d.mkdir()
        svc = GitService(base / "root")
        svc._safe_rmtree_partial(d)
        self.assertFalse(d.exists(), "空目录应被清理")

    def test_safe_rmtree_keeps_user_data(self):
        from gcm.git.service import GitService
        base = Path(tempfile.mkdtemp())
        d = base / "user_data"
        d.mkdir()
        (d / "important.txt").write_text("keep", encoding="utf-8")
        svc = GitService(base / "root")
        svc._safe_rmtree_partial(d)
        self.assertTrue(d.exists(), "非空且非 git 目录必须保留")
        self.assertTrue((d / "important.txt").exists())

    def test_already_exists_classified_platform(self):
        from gcm.git.service import _is_networkish_error, _classify_failure
        msg = "fatal: destination path 'x' already exists and is not an empty directory"
        self.assertFalse(_is_networkish_error(msg), "目录占用不应重试")
        label, _ = _classify_failure(msg)
        self.assertIn("目录已存在", label)
