"""G56-2 MCP 内部函数级补充测试：_read_request/_handle_call/_search/_sanitize/run_stdio 边界。"""
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from gcm.db.repo_db import Database
from gcm.mcp_server import (
    McpRequest, _error, _handle_call, _list_repos, _read_request, _result,
    _sanitize_row, _search_repos, run_stdio,
)
from gcm.models import RepoSpec


def _make_db(tmp: Path) -> Database:
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
    return Database(tmp / "repos.db")


class TestMcpInternals(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_mcp_internal_"))
        self.db = _make_db(self.tmp)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass

    def test_read_request_valid(self):
        it = iter(['{"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}'])
        req = _read_request(it)
        self.assertIsInstance(req, McpRequest)
        self.assertEqual(req.method, "initialize")

    def test_read_request_empty(self):
        self.assertIsNone(_read_request(iter([""])))
        self.assertIsNone(_read_request(iter([])))

    def test_read_request_bad_json(self):
        self.assertIsNone(_read_request(iter(["not-json"])))

    def test_result_and_error(self):
        self.assertEqual(_result(1, {"a": 1})["id"], 1)
        err = _error(1, -1, "boom")
        self.assertEqual(err["error"]["code"], -1)

    def test_sanitize_row_removes_sensitive(self):
        row = {"owner": "a", "token": "SECRET", "url": "https://u:p@x/y.git", "host": "g"}
        out = _sanitize_row(row)
        self.assertNotIn("token", out)
        self.assertNotIn("SECRET", str(out))
        self.assertEqual(_sanitize_row("plain"), "plain")  # 非 dict 原样

    def test_list_repos_host_filter(self):
        rows = _list_repos(self.db, host="github.com")
        self.assertEqual(len(rows), 1)

    def test_search_repos(self):
        self.assertEqual(len(_search_repos(self.db, "demo")), 1)
        self.assertEqual(len(_search_repos(self.db, "")), 0)
        self.assertEqual(len(_search_repos(self.db, "NOPE")), 0)

    def test_handle_call_list(self):
        resp = _handle_call(1, {"name": "list_repos", "arguments": {}}, self.tmp, False)
        self.assertIn("repos", resp["result"])

    def test_handle_call_search(self):
        resp = _handle_call(1, {"name": "search_repos",
                                "arguments": {"query": "demo"}}, self.tmp, False)
        self.assertEqual(resp["result"]["count"], 1)

    def test_handle_call_status(self):
        resp = _handle_call(1, {"name": "status", "arguments": {}}, self.tmp, False)
        self.assertIn("status", resp["result"])

    def test_handle_call_export_denied(self):
        resp = _handle_call(1, {"name": "export_report",
                                "arguments": {"format": "csv"}}, self.tmp, False)
        self.assertIn("error", resp)

    def test_handle_call_unknown(self):
        resp = _handle_call(1, {"name": "nope", "arguments": {}}, self.tmp, False)
        self.assertEqual(resp["error"]["code"], -32601)

    def test_run_stdio_unknown_method_and_bad_json(self):
        lines = [
            '{"jsonrpc":"2.0","id":1,"method":"not-a-method","params":{}}',
            'bad-json-line',
        ]
        buf = io.StringIO()
        old_stdin, old_stderr = sys.stdin, sys.stderr
        sys.stdin = io.StringIO("\n".join(lines) + "\n")
        sys.stderr = io.StringIO()
        try:
            with redirect_stdout(buf):
                run_stdio(self.tmp, allow_export=False)
        finally:
            sys.stdin, sys.stderr = old_stdin, old_stderr
        # 未知方法/非法 JSON 不产出响应体（仅日志）
        self.assertEqual(buf.getvalue().strip(), "")


if __name__ == "__main__":
    unittest.main()
