"""S-10：错误分类两处误判（Rev10 W2 P1）。

误判 1：SSH 公钥失败 `Permission denied (publickey)` 归「本地写入失败」，
        建议「清理磁盘空间」—— 毫不相干。归「认证」。
误判 2：任意 4xx/5xx 三位数都被当 HTTP 状态码 —— `fatal: bad object 404`
        （sha 片段）、端口 4031 都会误判。状态码只在 HTTP 语境词在场时生效。
"""
import unittest

from gcm.app.errors import classify_error


class TestSshAuthClassification(unittest.TestCase):
    def test_publickey_is_auth_not_local_io(self):
        r = classify_error(
            "git@github.com: Permission denied (publickey).\r\n"
            "fatal: Could not read from remote repository.", "")
        self.assertEqual(r.category, "auth")
        self.assertNotIn("磁盘", r.action, "公钥失败不得建议清磁盘")

    def test_host_key_verification_is_auth(self):
        r = classify_error("Host key verification failed.", "")
        self.assertEqual(r.category, "auth")


class TestStatusCodeContext(unittest.TestCase):
    def test_bad_object_sha_not_status(self):
        """`fatal: bad object 404` 的 404 是 sha 片段，不是 HTTP 状态。"""
        r = classify_error("fatal: bad object 404", "")
        self.assertNotEqual(r.code, "repo_not_found")

    def test_real_http_404_still_detected(self):
        r = classify_error("The requested URL returned error: 404", "")
        self.assertEqual(r.code, "repo_not_found")

    def test_real_http_500_retryable(self):
        r = classify_error("The requested URL returned error: 500", "")
        self.assertTrue(r.retryable)

    def test_port_number_not_status(self):
        r = classify_error("connect to host.example:4031 failed", "")
        self.assertNotEqual(r.category, "not_found")

    def test_pathspec_number_not_status(self):
        r = classify_error("error: pathspec 123 did not match any file(s)", "")
        self.assertEqual(r.category, "other")


if __name__ == "__main__":
    unittest.main()
