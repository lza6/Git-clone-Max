"""G84-23：MCP `resources` / `prompts` 的真实握手验证。

历史缺口：`initialize` 里 `resources: {}` / `prompts: {}` 是**空对象** ——
agent 只能靠人读 `docs/agent-contract.md` 才知道这服务能干什么、该怎么用。
现在两条通道都通了，而且**只读 + 打码**。

这里不用单元级 mock：真起 `python -m gcm.mcp_server` 子进程走 stdio 握手，
因为"协议能不能被客户端读懂"这件事只在真进程里成立。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _Session:
    """最小 MCP stdio 客户端（跳过 notifications，只取带 id 的响应）。"""

    def __init__(self, data_dir: pathlib.Path, allow_mutate: bool = False):
        env = dict(os.environ, GCM_MCP_ENABLE="1", GCM_DATA_DIR=str(data_dir),
                   PYTHONIOENCODING="utf-8")
        args = [sys.executable, "-m", "gcm.mcp_server"]
        if allow_mutate:
            args.append("--allow-mutate")
        self.proc = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1, env=env)
        self._id = 0

    def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        msg = {"jsonrpc": "2.0", "id": self._id, "method": method,
               "params": params or {}}
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise AssertionError("MCP 进程提前退出")
            data = json.loads(line)
            if data.get("id") == self._id:
                return data

    def close(self) -> None:
        try:
            if self.proc.stdin:
                self.proc.stdin.close()
            self.proc.wait(timeout=15)
        except Exception:      # noqa: BLE001 —— 收尾失败不影响断言结果
            self.proc.kill()
        finally:
            for pipe in (self.proc.stdout, self.proc.stderr):
                try:
                    if pipe:
                        pipe.close()
                except Exception:      # noqa: BLE001 —— 关管道失败无害
                    pass


class TestMcpResourcesAndPrompts(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.data = pathlib.Path(self._td.name) / "data"
        self.data.mkdir()
        self.s = _Session(self.data)
        self.addCleanup(self.s.close)
        self.s.call("initialize")

    def test_capabilities_declare_resources_and_prompts(self):
        caps = self.s.call("initialize")["result"]["capabilities"]
        self.assertIn("subscribe", caps.get("resources", {}),
                      "resources 能力仍是空对象（agent 不知道有资源可读）")
        self.assertIn("prompts", caps)
        self.assertIsInstance(caps["prompts"], dict)
        self.assertTrue(caps["prompts"], "prompts 能力是空的")

    def test_resources_list(self):
        res = self.s.call("resources/list")["result"]["resources"]
        uris = {r["uri"] for r in res}
        self.assertIn("gcm://status", uris)
        self.assertIn("gcm://repos", uris)
        for r in res:
            self.assertIn("mimeType", r)

    def test_read_status_resource(self):
        out = self.s.call("resources/read", {"uri": "gcm://status"})["result"]
        content = out["contents"][0]
        self.assertEqual(content["uri"], "gcm://status")
        self.assertEqual(content["mimeType"], "application/json")
        json.loads(content["text"])          # 必须是合法 JSON

    def test_read_unknown_resource_is_protocol_error(self):
        resp = self.s.call("resources/read", {"uri": "gcm://nope"})
        self.assertEqual(resp["error"]["code"], -32602,
                         "未知资源必须是 JSON-RPC 参数错误，而不是把通道带崩")

    def test_prompts_list_and_get(self):
        prompts = self.s.call("prompts/list")["result"]["prompts"]
        names = {p["name"] for p in prompts}
        self.assertIn("clone-batch", names)
        self.assertIn("diagnose-failure", names)
        got = self.s.call("prompts/get", {
            "name": "clone-batch",
            "arguments": {"urls": "https://github.com/octocat/Hello-World"}})["result"]
        text = got["messages"][0]["content"]["text"]
        self.assertIn("dry_run", text, "提示词应引导 agent 先预览")
        self.assertIn("Hello-World", text)

    def test_prompt_missing_argument_is_protocol_error(self):
        resp = self.s.call("prompts/get", {"name": "clone-batch", "arguments": {}})
        self.assertEqual(resp["error"]["code"], -32602)

    def test_secrets_are_redacted_in_resources(self):
        """资源文本必须打码：库里存过 URL 内嵌凭据的历史行不能原样吐出去。"""
        from gcm.db.repo_db import Database
        from gcm.models import RepoSpec
        db = Database(self.data / "repos.db")
        try:
            spec = RepoSpec("o", "leaky", "https://user:sup3rs3cret@github.com/o/leaky.git")
            db.upsert_repo(spec, str(self.data / "o__leaky"))
        finally:
            db.close()
        blob = json.dumps(self.s.call("resources/read", {"uri": "gcm://repos"}
                                      )["result"], ensure_ascii=False)
        self.assertNotIn("sup3rs3cret", blob, "资源输出泄漏了 URL 内嵌口令")


if __name__ == "__main__":
    unittest.main()
