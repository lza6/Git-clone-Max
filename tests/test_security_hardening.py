# -*- coding: utf-8 -*-
"""安全加固测试：host 白名单强制 + token 仅注入 github.com（对应审查 F1/F2/F3）。

- F1 HIGH: parse_any_repo_url 拒绝非白名单 host，token 不发给未知域名
- F2 MEDIUM: 所有 path 段含 @ 拒绝
- F3 MEDIUM: ref 合法性校验（拒绝控制字符/空白/非法字符）
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.url_lib import parse_any_repo_url, _is_valid_ref


class TestHostWhitelist(unittest.TestCase):
    def test_known_hosts_accepted(self):
        for u in ("https://github.com/a/b", "https://gitlab.com/a/b",
                  "https://gitee.com/a/b", "https://codeberg.org/a/b",
                  "https://bitbucket.org/a/b"):
            self.assertIsNotNone(parse_any_repo_url(u), u)

    def test_unknown_host_rejected(self):
        # 自建任意域名不再被当作仓库（token 不泄露给未知主机）
        self.assertIsNone(parse_any_repo_url("https://evil.example.com/o/r"))
        self.assertIsNone(parse_any_repo_url("https://github.com.evil.com/o/r"))
        self.assertIsNone(parse_any_repo_url("git@evil.example.com:o/r.git"))

    def test_known_srht_accepted(self):
        self.assertIsNotNone(parse_any_repo_url("https://git.sr.ht/~user/repo"))


class TestAtAllSegments(unittest.TestCase):
    def test_at_in_prefix_segment_rejected(self):
        # 子组前缀里的 @ 不再绕过（F2）
        self.assertIsNone(parse_any_repo_url("https://gitlab.com/a@evil.com/owner/repo"))

    def test_at_in_owner_repo_rejected(self):
        # owner 段含 @（凭据前置）→ 拒绝
        self.assertIsNone(parse_any_repo_url("https://github.com/attacker@evil/r"))

    def test_at_cannot_override_host(self):
        # repo 后 @ 视为 tag（合法语法）；关键验证：host 仍固定为白名单值
        s = parse_any_repo_url("https://github.com/o/repo@v1")
        self.assertIsNotNone(s)
        self.assertTrue(s.url_https.startswith("https://github.com/"), s.url_https)
        self.assertEqual(s.ref, "v1")


class TestRefValidation(unittest.TestCase):
    def test_valid_refs(self):
        for ref in ("v1.2.0", "main", "dev/feature-1", "v1.0.0-rc.1"):
            self.assertTrue(_is_valid_ref(ref), ref)

    def test_invalid_refs(self):
        for ref in ("", "evil tag", "v1\x01", "..", "a/../b",
                    "refs/heads/main", "-upload-pack=x", "a b"):
            self.assertFalse(_is_valid_ref(ref), ref)


class TestTokenHostScoped(unittest.TestCase):
    def test_token_injected_only_for_github(self):
        from gcm.git.service import GitService
        # github.com 仓库 → 注入 token
        svc_g = GitService(Path(tempfile.mkdtemp()), token="ghp_xyz")
        svc_g._current_host = "github.com"
        env_g = svc_g._env()
        self.assertIn("GIT_CONFIG_KEY_0", env_g)
        self.assertIn("http.extraHeader", env_g["GIT_CONFIG_KEY_0"])
        # 非 github host → 不注入 token
        svc_x = GitService(Path(tempfile.mkdtemp()), token="ghp_xyz")
        svc_x._current_host = "gitlab.com"
        env_x = svc_x._env()
        if env_x:
            self.assertNotIn("GIT_CONFIG_KEY_0", env_x)

    def test_no_token_no_inject(self):
        from gcm.git.service import GitService
        svc = GitService(Path(tempfile.mkdtemp()), token="")
        svc._current_host = "github.com"
        env = svc._env()
        self.assertTrue(env is None or "GIT_CONFIG_KEY_0" not in env)


if __name__ == "__main__":
    unittest.main(verbosity=2)
