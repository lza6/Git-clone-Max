"""G58 网络深化测试：限流状态 / host 探测 / 认证头类型建议。"""
import unittest
from unittest import mock

from gcm.app.host_probe import probe_git_service, probe_host, probe_https, suggest_auth_header
from gcm.app.rate_limit import RateLimitState


class TestRateLimit(unittest.TestCase):
    def test_observe_remaining(self):
        rl = RateLimitState()
        rl.observe({"X-RateLimit-Remaining": "3", "X-RateLimit-Reset": "9999999999"}, 200)
        self.assertEqual(rl.remaining, 3)

    def test_limited_when_zero(self):
        rl = RateLimitState()
        rl.observe({"X-RateLimit-Remaining": "0"}, 200)
        self.assertTrue(rl.is_limited)

    def test_backoff_after_429(self):
        rl = RateLimitState()
        rl.observe({}, 429)
        self.assertGreater(rl.backoff_seconds(), 0)
        rl.observe({}, 429)
        self.assertGreater(rl.backoff_seconds(), 5)

    def test_not_limited_by_default(self):
        rl = RateLimitState()
        self.assertFalse(rl.is_limited)
        self.assertEqual(rl.backoff_seconds(), 0.0)

    def test_reset(self):
        rl = RateLimitState()
        rl.observe({}, 429)
        rl.reset()
        self.assertFalse(rl.is_limited)
        self.assertEqual(rl.backoff_seconds(), 0.0)


class TestHostProbe(unittest.TestCase):
    def test_probe_https_ok(self):
        with mock.patch("socket.create_connection") as conn:
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
