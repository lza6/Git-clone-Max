# -*- coding: utf-8 -*-
"""G56-2 MCP server 测试：stdio 握手（initialize/tools/list）→ list_repos 只读。

- 用临时 GCM_DATA_DIR + 真 SQLite 库（插入一仓）跑本进程内 stdio 循环；
- 断言：默认不启用（GCM_MCP_ENABLE 为空 → 退出码 3）；
- 断言：无 GCM_MCP_ALLOW_EXPORT 时 export_report 返回错误（不写盘）；
- 断言：list_repos 返回行剔除敏感列、url 打码。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _write_db(data_dir: Path) -> Path:
    """建一个带一仓（含敏感 url）的 repos.db。"""
    from gcm.db.repo_db import Database
    from gcm.models import RepoSpec
    db = Database(data_dir / "repos.db")
    spec = RepoSpec(owner="alice", repo="demo",
                    url_https="https://ghp_secret@github.com/alice/demo.git",
                    folder_name="alice__demo")
    db.upsert_repo(spec, local_path=str(data_dir / "alice__demo"), host="github.com")
    db.close()
    return data_dir


class TestMcpStdio(unittest.TestCase):
    def _run_stdio(self, data_dir: Path, inputs: list[dict]) -> list[dict]:
        """喂入 JSON-RPC 请求行，收集 stdout 响应行。"""
        from gcm.mcp_server import run_stdio
        import io
        from contextlib import redirect_stdout, redirect_stderr
        buf = io.StringIO()
        payload = "\n".join(json.dumps(i) for i in inputs) + "\n"
        old_stdin, old_stdout = sys.stdin, sys.stdout
        from unittest import mock
        with mock.patch("sys.stdin", io.StringIO(payload)), \
             mock.patch("sys.stdout", buf), \
             redirect_stderr(io.StringIO()):
            rc = run_stdio(data_dir, allow_export=False)
        sys.stdin, sys.stdout = old_stdin, old_stdout
        self.assertEqual(rc, 0)
        return [json.loads(line) for line in buf.getvalue().splitlines() if line.strip()]

    def test_stdio_handshake_and_list_repos(self):
        """initialize → tools/list → tools/call list_repos：三跳握手 + 只读结果。"""
        tmp = Path(tempfile.mkdtemp(prefix="gcm_g56_mcp_"))
        _write_db(tmp)
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {}}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "list_repos", "arguments": {}}},
        ]
        out = self._run_stdio(tmp, reqs)
        self.assertGreaterEqual(len(out), 3)
        self.assertEqual(out[0]["result"]["serverInfo"]["name"], "gcm")
        names = [t["name"] for t in out[1]["result"]["tools"]]
        self.assertIn("list_repos", names)
        self.assertIn("search_repos", names)
        self.assertIn("export_report", names)
        self.assertIn("status", names)
        repos = out[2]["result"]["repos"]
        self.assertEqual(len(repos), 1)
        self.assertEqual(repos[0]["owner"], "alice")
        self.assertNotIn("ghp_secret", json.dumps(repos))
        self.assertNotIn("token", json.dumps(repos))

    def test_mcp_disabled_without_env(self):
        """GCM_MCP_ENABLE 未设置 → 退出码 3（显式启用开关）。"""
        env = dict(os.environ)
        env.pop("GCM_MCP_ENABLE", None)
        proc = subprocess.run(
            [sys.executable, "-m", "gcm.mcp_server"],
            input="", capture_output=True, text=True, env=env, timeout=60, cwd=str(ROOT))
        self.assertEqual(proc.returncode, 3)

    def test_export_report_denied_without_flag(self):
        """未传 --allow-export → export_report 返回 JSON-RPC 错误，不写盘。"""
        tmp = Path(tempfile.mkdtemp(prefix="gcm_g56_mcp_exp_"))
        _write_db(tmp)
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "export_report", "arguments": {"format": "csv"}}},
        ]
        out = self._run_stdio(tmp, reqs)
        called = out[1]
        self.assertIn("error", called)
        self.assertIn("显式启用", called["error"]["message"])
        self.assertFalse((tmp / "repos_report.csv").exists(), "未放行不应写盘")


if __name__ == "__main__":
    unittest.main()
