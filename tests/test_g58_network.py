"""G58 网络深化测试：host 探测 / 认证头类型建议。

（G67-21：原含 RateLimitState 用例，该模块因零生产引用已删除。）
"""
import unittest
from unittest import mock

from gcm.app.host_probe import probe_git_service, probe_host, probe_https, suggest_auth_header


class TestHostProbe(unittest.TestCase):
    def test_probe_https_ok(self):
        with mock.patch("socket.create_connection"):
            r = probe_https("example.com", timeout=1)
            self.assertEqual(r["status"], "ok")

    def test_probe_https_err(self):
        with mock.patch("socket.create_connection", side_effect=OSError("no")):
            r = probe_https("example.com", timeout=1)
            self.assertEqual(r["status"], "err")

    def test_probe_git_ok(self):
        with mock.patch("subprocess.run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = "a\tHEAD\nb\trefs/heads/main\n"
            run.return_value.stderr = ""
            r = probe_git_service("https://example.com/x.git", timeout=1)
            self.assertEqual(r["status"], "ok")
            self.assertIn("2", r["detail"])

    def test_probe_git_err(self):
        with mock.patch("subprocess.run") as run:
            run.return_value.returncode = 1
            run.return_value.stdout = ""
            run.return_value.stderr = "Authentication failed"
            r = probe_git_service("https://example.com/x.git", timeout=1)
            self.assertEqual(r["status"], "err")

    def test_suggest_auth(self):
        self.assertEqual(suggest_auth_header({"detail": "could not read Username"}), "Basic")
        self.assertEqual(suggest_auth_header({"detail": "glpat private token"}), "PRIVATE-TOKEN")
        self.assertEqual(suggest_auth_header({"detail": "401 Unauthorized"}), "Bearer")
        self.assertEqual(suggest_auth_header({"detail": "random"}), "Bearer")

    def test_probe_host_skips_git_when_https_fails(self):
        with mock.patch("socket.create_connection", side_effect=OSError("no")):
            reports = probe_host("example.com")
            self.assertEqual(len(reports), 1)
            self.assertEqual(reports[0]["status"], "err")


if __name__ == "__main__":
    unittest.main()
