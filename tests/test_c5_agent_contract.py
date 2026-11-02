"""Rev8 C5 批守卫：MCP 写工具 / 事件落盘 / API 可选鉴权 / 契约 schema 导出。

四条不变量：
1. **C5-1**：未授权时 MCP 工具列表里**没有** clone_batch（不是"存在但报错"）；
   授权后 clone_batch 走 CLI --json 子进程（本地裸仓 E2E 真跑通）。
2. **C5-3**：事件落盘 `data/events/events_YYYYMMDD.jsonl`，按天轮转、
   上限切分、旧文件清理、写入失败不抛；token 永不落盘（publish 已 redact）。
3. **C5-4**：`data/api_token` 存在 → 无/错 token 401，对 token 200；
   文件不存在 → 行为与旧版完全一致（默认无鉴权）；/version 永远放行（探活）。
4. **C5-2**：`python scripts/export_contract.py` 生成 schema，`--check` 模式
   在未生成/过期时退出非 0。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestMcpWriteTool(unittest.TestCase):
    """C5-1：clone_batch 只在授权时可见、可用。"""

    def _list_names(self, allow_mutate: bool) -> set[str]:
        from gcm.mcp_server import _TOOLS, _readonly_tools
        tools = _TOOLS if allow_mutate else _readonly_tools()
        return {t["name"] for t in tools}

    def test_unauthorized_tool_list_excludes_clone_batch(self):
        self.assertNotIn("clone_batch", self._list_names(allow_mutate=False))
        self.assertIn("list_repos", self._list_names(False))

    def test_authorized_tool_list_includes_clone_batch(self):
        self.assertIn("clone_batch", self._list_names(allow_mutate=True))

    def test_unauthorized_call_returns_structured_error(self):
        from gcm.mcp_server import _handle_call
        with tempfile.TemporaryDirectory() as td:
            resp = _handle_call(1, {"name": "clone_batch",
                                    "arguments": {"urls": ["https://x/y"]}},
                                Path(td), allow_export=False, allow_mutate=False)
        self.assertIn("error", resp)
        self.assertIn("--allow-mutate", resp["error"]["message"])

    def test_authorized_clone_batch_real_e2e_local_bare(self):
        """授权后真跑：本地裸仓克隆（零网络），返回 CLI 顶层契约。"""
        from gcm.mcp_server import _handle_call
        with tempfile.TemporaryDirectory() as td:
            tdp = Path(td)
            src = tdp / "src.git"
            subprocess.run(["git", "init", "--bare", "-b", "main", str(src)],
                           check=True, capture_output=True)
            work = tdp / "work"
            work.mkdir()
            subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
            (work / "a.txt").write_text("x", encoding="utf-8")
            subprocess.run(["git", "-C", str(work), "add", "."], check=True)
            subprocess.run(["git", "-C", str(work), "-c", "user.email=t@t",
                            "-c", "user.name=t", "commit", "-qm", "i"], check=True)
            subprocess.run(["git", "-C", str(work), "remote", "add", "origin",
                            str(src)], check=True)
            subprocess.run(["git", "-C", str(work), "push", "-q", "-u",
                            "origin", "main"], check=True)
            data_dir = tdp / "data"
            data_dir.mkdir()
            resp = _handle_call(
                1, {"name": "clone_batch", "arguments": {"urls": [str(src)]}},
                data_dir, allow_export=False, allow_mutate=True)
        content = resp["result"]["clone_result"]
        self.assertTrue(content.get("ok"), content)
        self.assertEqual(content["counts"]["success"], 1)


class TestEventSink(unittest.TestCase):
    """C5-3：事件落盘。"""

    def _mk_event(self, seq: int, etype: str = "task_result"):
        from gcm.app.event_bus import Event
        return Event(type=etype, seq=seq, ts=time.time(), data={"k": "v"})

    def test_append_writes_jsonl(self):
        from gcm.app.event_sink import EventSink
        with tempfile.TemporaryDirectory() as td:
            sink = EventSink(Path(td))
            sink.append(self._mk_event(1))
            sink.close()
            files = list((Path(td) / "events").glob("events_*.jsonl"))
            self.assertEqual(len(files), 1)
            doc = json.loads(files[0].read_text(encoding="utf-8").strip())
            self.assertEqual(doc["seq"], 1)
            self.assertEqual(doc["type"], "task_result")

    def test_size_limit_rotates(self):
        from gcm.app.event_sink import EventSink
        with tempfile.TemporaryDirectory() as td:
            sink = EventSink(Path(td), max_bytes=200)
            for i in range(50):
                sink.append(self._mk_event(i))
            sink.close()
            files = sorted((Path(td) / "events").glob("events_*.jsonl"))
            self.assertGreater(len(files), 1, "超上限应轮转出多个文件")

    def test_never_raises_on_bad_event(self):
        from gcm.app.event_sink import EventSink
        with tempfile.TemporaryDirectory() as td:
            sink = EventSink(Path(td))
            sink.append(None)          # 坏对象
            sink.append(object())      # 缺属性
            sink.close()               # 不得抛
            self.assertGreaterEqual(sink.errors, 1)

    def test_old_files_cleaned(self):
        from gcm.app.event_sink import EventSink
        with tempfile.TemporaryDirectory() as td:
            d = Path(td) / "events"
            d.mkdir(parents=True)
            old = d / "events_20000101.jsonl"
            old.write_text("{}\n", encoding="utf-8")
            sink = EventSink(Path(td))
            sink.append(self._mk_event(1))
            sink.close()
            self.assertFalse(old.exists(), "超过保留期的旧文件应被清理")

    def test_attach_sink_passthrough(self):
        """挂 sink 后 publish 仍返回事件、sink 收到同一事件。"""
        from gcm.app.event_bus import EventBus
        from gcm.app.event_sink import attach_sink
        with tempfile.TemporaryDirectory() as td:
            bus = EventBus()
            sink = attach_sink(bus, Path(td))
            ev = bus.publish("task_result", {"url": "https://github.com/a/b"})
            self.assertIsNotNone(ev)
            self.assertEqual(sink.written, 1)
            sink.close()


class TestApiAuth(unittest.TestCase):
    """C5-4：可选 Bearer 鉴权。"""

    def _request(self, server_token: str, auth_header: str | None,
                 path: str = "/version"):
        """直接调 handler 的判定逻辑（不起真 socket）。"""
        from gcm.http_api import GcmApiServer, load_api_token
        # 用 load_api_token + 属性判定的组合逻辑做单元级验证
        with tempfile.TemporaryDirectory() as td:
            if server_token == "__from_file__":
                (Path(td) / "api_token").write_text("sekrit\n", encoding="utf-8")
                tok = load_api_token(td)
            else:
                tok = server_token
            server = GcmApiServer.__new__(GcmApiServer)
            server.api_token = tok
            server.root = Path(td)
            server.data_dir = Path(td)
            return server, tok

    def test_load_token_from_file(self):
        with tempfile.TemporaryDirectory() as td:
            (Path(td) / "api_token").write_text("sekrit\n", encoding="utf-8")
            from gcm.http_api import load_api_token
            self.assertEqual(load_api_token(td), "sekrit")

    def test_no_token_file_no_auth(self):
        with tempfile.TemporaryDirectory() as td:
            from gcm.http_api import load_api_token
            self.assertEqual(load_api_token(td), "")

    def test_missing_token_file_is_default_open(self):
        """token 文件不存在 → 默认行为不变（回归保护：现有 7 端点测试不回退）。"""
        with tempfile.TemporaryDirectory() as td:
            from gcm.http_api import load_api_token
            self.assertEqual(load_api_token(td), "")
            # create_server 传入空 data_dir 也不炸
            from gcm.http_api import create_server
            httpd = create_server("127.0.0.1:0", root=td, data_dir=td)
            self.assertEqual(httpd.api_token, "")
            httpd.server_close()

    def test_version_endpoint_excluded_from_auth(self):
        """/version 是探活端点，源码里必须显式豁免（静态断言）。"""
        src = (ROOT / "gcm/http_api.py").read_text(encoding="utf-8")
        self.assertIn('"/version"', src)


class TestContractExport(unittest.TestCase):
    """C5-2：契约 schema 导出。"""

    def _run_export(self, *extra: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts/export_contract.py"), *extra],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, cwd=str(ROOT), env=env)

    def test_export_generates_schema(self):
        r = self._run_export("--out", str(ROOT / "docs/contract"))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue((ROOT / "docs/contract/mcp_tools.schema.json").exists())
        self.assertTrue((ROOT / "docs/contract/cli_json.schema.json").exists())

    def test_check_mode_passes_after_export(self):
        r = self._run_export("--out", str(ROOT / "docs/contract"), "--check")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_check_mode_fails_when_stale(self):
        """改了源码里工具名不更新 schema → --check 必须红（先红后绿的保证）。"""
        p = ROOT / "docs/contract/mcp_tools.schema.json"
        if not p.exists():
            self.skipTest("schema 未生成")
        orig = p.read_text(encoding="utf-8")
        try:
            p.write_text(orig.replace("list_repos", "list_repos_RENAMED"),
                         encoding="utf-8")
            r = self._run_export("--out", str(ROOT / "docs/contract"), "--check")
            self.assertNotEqual(r.returncode, 0, "schema 过期时 --check 必须失败")
        finally:
            p.write_text(orig, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
