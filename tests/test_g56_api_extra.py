"""G56 补充覆盖：HTTP API 报告/搜索/404/POST 空体 + MCP search/status/export 边界。"""
import json
import socket
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from gcm.db.repo_db import Database
from gcm.models import RepoSpec


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_db(tmp: Path) -> None:
    db = Database(tmp / "repos.db")
    db.upsert_repo(RepoSpec(owner="alice", repo="demo",
                            url_https="https://github.com/alice/demo.git",
                            folder_name="alice__demo"),
                   local_path=str(tmp / "alice__demo"), host="github.com")
    db.upsert_repo(RepoSpec(owner="bob", repo="lib",
                            url_https="https://github.com/bob/lib.git",
                            folder_name="bob__lib"),
                   local_path=str(tmp / "bob__lib"), host="gitlab.com")
    db.close()


def _serve(tmp: Path, allow_mutate: bool = False):
    from gcm.http_api import create_server
    port = _free_port()
    httpd = create_server(f"127.0.0.1:{port}", root=tmp, data_dir=tmp,
                          allow_mutate=allow_mutate)
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    return httpd, port


def _get(port: int, path: str) -> tuple[int, str, dict]:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            return resp.status, resp.headers.get("Content-Type", ""), raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        return e.code, e.headers.get("Content-Type", ""), raw


class TestHttpApiEndpoints(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_api_extra_"))
        _make_db(self.tmp)
        self.httpd, self.port = _serve(self.tmp)
        self.addCleanup(self.httpd.shutdown)
        self.addCleanup(self.httpd.server_close)

    def test_repos_list_and_host_filter(self):
        code, _, body = _get(self.port, "/api/repos?host=github.com")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["repos"][0]["owner"], "alice")

    def test_search(self):
        code, _, body = _get(self.port, "/api/repos/search?q=demo")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["count"], 1)

    def test_search_missing_q(self):
        code, _, body = _get(self.port, "/api/repos/search")
        self.assertEqual(code, 400)

    def test_report_csv(self):
        code, ctype, raw = _get(self.port, "/api/report?format=csv")
        self.assertEqual(code, 200)
        self.assertIn("text/csv", ctype)
        self.assertIn("owner,repo,host", raw)

    def test_report_markdown(self):
        code, ctype, raw = _get(self.port, "/api/report?format=markdown")
        self.assertEqual(code, 200)
        self.assertIn("text/markdown", ctype)
        self.assertIn("| owner |", raw)

    def test_report_json_default(self):
        code, _, raw = _get(self.port, "/api/report")
        self.assertEqual(code, 200)
        data = json.loads(raw)
        self.assertEqual(data["count"], 2)

    def test_unknown_path_404(self):
        code, _, body = _get(self.port, "/api/nope")
        self.assertEqual(code, 404)

    def test_post_clone_empty_body(self):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/api/clone",
                                     data=b"", method="POST")
        try:
            urllib.request.urlopen(req, timeout=10)
            self.fail("应拒绝")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 403)

    def test_version(self):
        code, _, raw = _get(self.port, "/version")
        self.assertEqual(code, 200)
        data = json.loads(raw)
        self.assertEqual(data["readonly"], True)


class TestMcpExtra(unittest.TestCase):
    def _run(self, lines: list[str]) -> list[str]:
        import subprocess
        import sys
        env = dict(__import__("os").environ)
        env["GCM_MCP_ENABLE"] = "1"
        env["GCM_DATA_DIR"] = str(self.tmp)
        proc = subprocess.run(
            [sys.executable, "-m", "gcm.mcp_server"],
            input="\n".join(lines) + "\n", capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, timeout=30)
        out = proc.stdout or ""
        return [ln for ln in out.splitlines() if ln.strip()]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_mcp_extra_"))
        _make_db(self.tmp)

    def test_search_and_status_tools(self):
        init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {}})
        search = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "search_repos",
                                        "arguments": {"query": "demo"}}})
        status = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "status", "arguments": {}}})
        lines = self._run([init, search, status])
        joined = "\n".join(lines)
        self.assertIn('"demo"', joined)
        self.assertIn('"total_repos"', joined)

    def test_export_denied_and_unknown_tool(self):
        init = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {}})
        export = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                             "params": {"name": "export_report",
                                        "arguments": {"format": "csv"}}})
        nosuch = json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                             "params": {"name": "nope", "arguments": {}}})
        lines = self._run([init, export, nosuch])
        joined = "\n".join(lines)
        self.assertIn('"error"', joined)


if __name__ == "__main__":
    unittest.main()
