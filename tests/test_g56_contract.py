# -*- coding: utf-8 -*-
"""G56-5 契约文档一致性测试：--json 顶层 schema / 错误码 / 退出码与
docs/agent-contract.md 抽样断言；HTTP API 只读边界冒烟（可选项）。

硬性约束：不触网；HTTP 只绑 127.0.0.1 随机端口。
"""
from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import unittest
import urllib.request
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestContract(unittest.TestCase):
    def test_contract_doc_lists_json_schema_and_exit_codes(self):
        """契约文档存在且包含 --json 顶层字段与退出码说明。"""
        doc = ROOT / "docs" / "agent-contract.md"
        self.assertTrue(doc.is_file(), "缺少 docs/agent-contract.md")
        text = doc.read_text(encoding="utf-8")
        for key in ("counts", "results", "invalid", "status", "code"):
            self.assertIn(key, text, f"契约文档缺字段 {key}")
        for code in ("0 | 全部成功", "1 | 有失败", "2 | 无有效输入"):
            self.assertIn(code, text, f"契约文档缺退出码说明 {code}")

    def test_cli_json_schema_matches_contract(self):
        """空输入路径下实际输出与契约顶层 schema 一致。"""
        tmp = Path(tempfile.mkdtemp(prefix="gcm_g56_contract_"))
        urls = tmp / "empty.txt"
        urls.write_text("# only comment\n", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        from gcm.cli import main as cli_main
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli_main(["--cli", "--json", "--dir", str(tmp / "out"), str(urls)])
        self.assertEqual(rc, 2)
        doc = json.loads(stdout.getvalue())
        self.assertEqual(sorted(doc.keys()),
                         ["counts", "invalid", "ok", "results", "version"])

    def test_error_schema_has_code_message_redacted(self):
        """错误统一 schema：{code, message, redacted}。"""
        from gcm.cli import _error
        e = _error(2, "https://tok@host/x failed")
        self.assertEqual(sorted(e.keys()), ["code", "message", "redacted"])
        self.assertEqual(e["code"], 2)
        self.assertNotIn("tok@", e["redacted"])


class TestHttpApiReadonly(unittest.TestCase):
    """G56-4 HTTP API 冒烟：只绑 127.0.0.1；默认拒绝写。"""

    def _free_port(self) -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    def test_readonly_boundaries(self):
        """GET /version 只读 OK；POST /api/clone 默认 403；随机端口释放。"""
        tmp = Path(tempfile.mkdtemp(prefix="gcm_g56_http_"))
        from gcm.db.repo_db import Database
        from gcm.models import RepoSpec
        db = Database(tmp / "repos.db")
        db.upsert_repo(RepoSpec(owner="alice", repo="demo",
                                url_https="https://github.com/alice/demo.git",
                                folder_name="alice__demo"),
                       local_path=str(tmp / "alice__demo"), host="github.com")
        db.close()
        port = self._free_port()
        from gcm.http_api import create_server
        httpd = create_server(f"127.0.0.1:{port}", root=tmp, data_dir=tmp,
                              allow_mutate=False)
        import threading
        th = threading.Thread(target=httpd.serve_forever, daemon=True)
        th.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/version", timeout=10) as resp:
                doc = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(doc["readonly"], True)
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=10) as resp:
                st = json.loads(resp.read().decode("utf-8"))
                self.assertTrue(st["readonly"])
                self.assertGreaterEqual(st["status"]["total_repos"], 1)
            # 只读模式下 POST 写 → 403
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/clone",
                data=b"https://github.com/alice/demo.git\n",
                method="POST")
            try:
                urllib.request.urlopen(req, timeout=10)
                self.fail("只读模式应拒绝写请求")
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 403)
                body = json.loads(e.read().decode("utf-8"))
                self.assertEqual(body["error"]["code"], 403)
        finally:
            httpd.shutdown()
            httpd.server_close()
        # 端口释放验证
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
