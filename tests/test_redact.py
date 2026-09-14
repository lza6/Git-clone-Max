# -*- coding: utf-8 -*-
"""G28-1 敏感信息打码 单测（计划书/下一步改进指南.md G28-1）。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.util.redact import redact


class TestRedact(unittest.TestCase):
    def test_url_embedded_token(self):
        self.assertEqual(
            redact("https://ghp_abc123@github.com/o/r.git"),
            "https://***@github.com/o/r.git")

    def test_authorization_bearer(self):
        out = redact("remote: HTTP Error 401 - Authorization: Bearer ghp_secret9")
        self.assertNotIn("ghp_secret9", out)
        self.assertIn("Authorization: Bearer ***", out)

    def test_private_token_header(self):
        out = redact("fatal: PRIVATE-TOKEN: glpat-xyz failed")
        self.assertNotIn("glpat-xyz", out)
        self.assertIn("PRIVATE-TOKEN: ***", out)

    def test_plain_line_untouched(self):
        line = "Cloning into 'o__r'...\nReceiving objects: 45%"
        self.assertEqual(redact(line), line)

    def test_empty_and_nonstr(self):
        self.assertEqual(redact(""), "")
        self.assertEqual(redact(None), None)  # type: ignore[arg-type]

    def test_mixed_real_world_git_error(self):
        line = ("fatal: unable to access 'https://token123@gitlab.com/g/sub/r.git/' : "
                "Authorization: Bearer tok456")
        out = redact(line)
        self.assertNotIn("token123", out)
        self.assertNotIn("tok456", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
