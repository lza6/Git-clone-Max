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

from gcm.util.redact import (
    redact,
    redact_home,
    set_redact_userprofile,
)


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


class TestRedactUserProfile(unittest.TestCase):
    """G44-4：redact / redact_home 对 home 目录路径打码（默认关，向后兼容）。"""

    def tearDown(self):
        # 复位全局开关，避免泄漏到其它用例
        set_redact_userprofile(False)

    # ------------------------------------------------------------ redact_home
    def test_home_windows_masked(self):
        """Windows C:\\Users\\<用户名> → ~（保留后续子路径）。"""
        out = redact_home(r"C:\Users\Alice\foo\repos.db")
        self.assertIn(r"~\foo\repos.db", out)
        self.assertNotIn("Alice", out)

    def test_home_windows_lowercase_drive_masked(self):
        """盘符/Users 大小写不敏感同样打码。"""
        out = redact_home(r"c:\users\bob\data\x")
        self.assertIn(r"~\data\x", out)
        self.assertNotIn("bob", out)

    def test_home_posix_masked(self):
        """POSIX /home/<用户名> → ~。"""
        out = redact_home("/home/alice/foo/repos.db")
        self.assertIn("~/foo/repos.db", out)
        self.assertNotIn("alice", out)

    def test_home_absent_untouched(self):
        """无 home 段文本原样保留。"""
        line = "Cloning into 'o__r'... path=/srv/data/repos.db"
        self.assertEqual(redact_home(line), line)

    def test_home_custom_path(self):
        """home 参数按精确路径打码（如自定义数据目录）。"""
        out = redact_home(r"D:\data\user1\foo.log", home=r"D:\data\user1")
        self.assertIn(r"~\foo.log", out)
        self.assertNotIn(r"D:\data\user1", out)

    def test_home_custom_posix_path_case_sensitive(self):
        """home 参数为 POSIX 路径时精确匹配（大小写敏感）。"""
        out = redact_home("/var/lib/user7/app.log", home="/var/lib/user7")
        self.assertIn("~/app.log", out)
        self.assertNotIn("/var/lib/user7", out)

    def test_home_slash_only_ignored(self):
        """home 仅斜杠/反斜杠（rstrip 后为空）不参与打码，文本原样。"""
        line = "path=/srv/app/x"
        self.assertEqual(redact_home(line, home="/"), line)
        self.assertEqual(redact_home(line, home="\\"), line)

    def test_home_nonstr_empty(self):
        """非字符串 / 空文本原样返回。"""
        self.assertEqual(redact_home(""), "")
        self.assertIsNone(redact_home(None))  # type: ignore[arg-type]

    # ------------------------------------------------------------ redact 参数
    def test_default_keeps_home_text(self):
        """默认行为不变：不打码 home 路径（既有用例回归）。"""
        line = r"failed: C:\Users\Alice\foo\repos.db timeout"
        self.assertIn("Alice", redact(line))

    def test_userprofile_true_masks_home(self):
        """redact(userprofile=True) 打码 home 路径。"""
        out = redact(r"err C:\Users\Alice\foo\repos.db", userprofile=True)
        self.assertNotIn("Alice", out)
        self.assertIn(r"~\foo\repos.db", out)

    def test_userprofile_false_keeps_home(self):
        """redact(userprofile=False) 显式关闭不打码。"""
        line = r"err C:\Users\Alice\foo\repos.db"
        self.assertIn("Alice", redact(line, userprofile=False))

    # ------------------------------------------------------------ 全局开关
    def test_global_switch_enable_disable(self):
        """set_redact_userprofile 全局开关：开启打码、关闭恢复。"""
        set_redact_userprofile(True)
        self.assertNotIn("Alice", redact(r"C:\Users\Alice\x"))
        set_redact_userprofile(False)
        self.assertIn("Alice", redact(r"C:\Users\Alice\x"))

    def test_global_switch_posix_via_redact(self):
        """全局开关开启后 redact 同样打码 POSIX home。"""
        set_redact_userprofile(True)
        try:
            out = redact("trace /home/carol/run.log")
            self.assertNotIn("carol", out)
            self.assertIn("~/run.log", out)
        finally:
            set_redact_userprofile(False)

