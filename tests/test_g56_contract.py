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

    def test_readonly_boundaries(self):
        """GET /version 只读 OK；POST /api/clone 默认 403；端口已释放。"""
        tmp = Path(tempfile.mkdtemp(prefix="gcm_g56_http_"))
        from gcm.db.repo_db import Database
        from gcm.models import RepoSpec
        db = Database(tmp / "repos.db")
        db.upsert_repo(RepoSpec(owner="alice", repo="demo",
                                url_https="https://github.com/alice/demo.git",
                                folder_name="alice__demo"),
                       local_path=str(tmp / "alice__demo"), host="github.com")
        db.close()
        # G66-12：原实现用 _free_port()「先 bind 再 close」探测空闲端口，
        # 在真正使用前存在 TOCTOU 窗口 —— 同批其它测试文件也各自起 HTTP server，
        # 实测会让本用例间歇性报错（同一条命令一次 OK 一次 FAILED）。
        # 改为 bind 到端口 0 让内核分配，再从 socket 反查实际端口，彻底消除竞态。
        from gcm.http_api import create_server
        httpd = create_server("127.0.0.1:0", root=tmp, data_dir=tmp,
                              allow_mutate=False)
        port = httpd.server_address[1]
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
        # 端口释放验证：server_close() 后该端口应能被重新绑定。
        # G69-18：原实现直接 bind，在 Linux/macOS 上会因刚关闭的连接留下
        # TIME_WAIT 而抛 `OSError: [Errno 48/98] Address already in use`
        # —— 那是内核的正常语义，不是「端口没释放」，CI 上必红。
        # 用 SO_REUSEADDR 消除 TIME_WAIT 干扰，仍保留「端口回到可用状态」的语义。
        with socket.socket() as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", port))


if __name__ == "__main__":
    unittest.main()
