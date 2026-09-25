"""G56-4 HTTP API 写路径补充测试：--allow-mutate 放行 POST /api/clone（真实本地裸仓）+ 审计落盘。"""
import json
import socket
import subprocess
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from gcm.http_api import create_server


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_bare(td: Path, name: str) -> Path:
    src = td / "src"
    src.mkdir()
    (src / "f.txt").write_text("hello\n", encoding="utf-8")
    for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@e.com"],
                ["git", "config", "user.name", "t"], ["git", "add", "."],
                ["git", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=src, check=True, capture_output=True)
    bare = td / name
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)],
                   check=True, capture_output=True)
    return bare


class TestHttpApiWrite(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_api_write_"))
        self.bare = _make_bare(self.tmp, "r0.git")
        port = _free_port()
        self.httpd = create_server(f"127.0.0.1:{port}", root=self.tmp / "out",
                                   data_dir=self.tmp, allow_mutate=True)
        self.port = port
        th = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        th.start()
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.httpd.server_close)

    def test_post_clone_allowed_and_clones(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/clone",
            data=(str(self.bare) + "\n").encode("utf-8"), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                self.assertEqual(resp.status, 201)
                payload = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(payload.get("ok"))
                self.assertGreaterEqual(payload["counts"]["total"], 1)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            self.fail(f"POST 克隆失败: {e.code} {body}")

    def test_post_unknown_path_404(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/nope",
            data=b"x", method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("应 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_post_empty_body(self):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/clone",
            data=b"", method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("应拒绝空体")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 400)

    def test_audit_log_written(self):
        """allow_mutate 模式：写操作审计落盘 <root>/.gcm-api-audit.jsonl。"""
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/clone",
            data=(str(self.bare) + "\n").encode("utf-8"), method="POST")
        try:
            urllib.request.urlopen(req, timeout=60)
        except urllib.error.HTTPError:
            pass
        audit = self.tmp / "out" / ".gcm-api-audit.jsonl"
        self.assertTrue(audit.exists())


if __name__ == "__main__":
    unittest.main()
