# -*- coding: utf-8 -*-
"""M2 安全纵深 G44-3（更新 sha256 提取）+ G44-5（URL 内嵌凭据剥离）专项测试。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import json

from gcm.app.updater import extract_release_sha256, check_latest_with_sha
from gcm.app.url_lib import strip_embedded_credentials

_RAW = '{"tag_name":"v9.9.9","html_url":"https://x","body":"## v9.9.9\n- onefile exe sha256: `abc123def456abc123def456abc123def456abc123def456abc123def456abc123def456`"}'
# 上面是 48 位非 hex 占位 —— 改用真正的 64 位 hex
_SHA = "a" * 64
_RELEASE_BODY = "exe sha256: `" + _SHA + "`  \n- portable zip sha256: `" + ("b" * 64) + "`"
_RELEASE_JSON = json.dumps({"tag_name": "v9.9.9", "html_url": "https://x", "body": _RELEASE_BODY})


class TestExtractSha256(unittest.TestCase):
    def test_extracts_first_sha(self):
        self.assertEqual(extract_release_sha256(_RELEASE_BODY), _SHA)

    def test_no_sha_returns_empty(self):
        self.assertEqual(extract_release_sha256("no checksum here"), "")
        self.assertEqual(extract_release_sha256(""), "")

    def test_noise_text(self):
        self.assertEqual(extract_release_sha256("random abc123"), "")


class TestCheckLatestWithSha(unittest.TestCase):
    def test_injected_fetcher_five_tuple(self):
        has_new, ver, url, err, sha = check_latest_with_sha(fetcher=lambda: _RELEASE_JSON)
        self.assertTrue(has_new, "v9.9.9 > 当前")
        self.assertEqual(ver, "v9.9.9")
        self.assertEqual(sha, _SHA)
        self.assertEqual(err, "")

    def test_injected_fetcher_no_sha(self):
        js = json.dumps({"tag_name": "v9.8.0", "html_url": "https://x", "body": "no sha"})
        has_new, ver, url, err, sha = check_latest_with_sha(fetcher=lambda: js)
        self.assertEqual(sha, "")
        self.assertEqual(err, "")

    def test_injected_fetcher_error(self):
        def bad():
            raise RuntimeError("net down")
        has_new, ver, url, err, sha = check_latest_with_sha(fetcher=bad)
        self.assertFalse(has_new)
        self.assertIn("检查更新失败", err)
        self.assertEqual(sha, "")


class TestStripEmbeddedCredentials(unittest.TestCase):
    def test_userinfo_password_removed(self):
        clean, had = strip_embedded_credentials("https://user:pass@github.com/o/r.git")
        self.assertTrue(had)
        self.assertEqual(clean, "https://github.com/o/r.git")

    def test_token_userinfo_removed(self):
        clean, had = strip_embedded_credentials("https://ghp_xxx@github.com/o/r.git")
        self.assertTrue(had)
        self.assertEqual(clean, "https://github.com/o/r.git")

    def test_access_token_query_removed(self):
        clean, had = strip_embedded_credentials(
            "https://github.com/o/r.git?access_token=abc&ref=main")
        self.assertTrue(had)
        self.assertNotIn("access_token", clean)
        self.assertIn("ref=main", clean)

    def test_case_insensitive_sensitive_param(self):
        clean, had = strip_embedded_credentials("https://h/o/r?PRIVATE_TOKEN=zz")
        self.assertTrue(had)
        self.assertEqual(clean, "https://h/o/r")

    def test_no_cred_untouched(self):
        clean, had = strip_embedded_credentials("https://github.com/o/r.git")
        self.assertFalse(had)
        self.assertEqual(clean, "https://github.com/o/r.git")

    def test_ssh_scp_not_misjudged(self):
        clean, had = strip_embedded_credentials("git@github.com:o/r.git")
        self.assertFalse(had)
        self.assertEqual(clean, "git@github.com:o/r.git")

    def test_empty(self):
        clean, had = strip_embedded_credentials("")
        self.assertFalse(had)
        self.assertEqual(clean, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
