"""批次① MCP 生态对齐守卫：ToolAnnotations / server.json / 版本载体。

- tools/list 返回的每个工具必须带 `annotations` 四件套（readOnlyHint 必有；
  非只读工具必须带 destructive/idempotent），与 modelcontextprotocol/servers 口径一致；
- server.json 存在、版本与 `gcm.__version__` 单源一致、mcpServers 入口指向
  `python -m gcm.mcp_server`（GCM_MCP_ENABLE=1）；
- bump_version.py 的载体表包含 server.json（防发版漏改）。
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402


class TestToolAnnotations(unittest.TestCase):
    def _tools(self, allow_mutate: bool) -> list[dict]:
        from gcm.mcp_server import _TOOLS, _readonly_tools
        raw = _TOOLS if allow_mutate else _readonly_tools()
        return [{k: v for k, v in t.items() if k != "mutate"} for t in raw]

    def test_every_tool_has_annotations(self):
        for t in self._tools(allow_mutate=True):
            self.assertIn("annotations", t, f"{t['name']} 缺 annotations")
            ann = t["annotations"]
            self.assertIn("readOnlyHint", ann, t["name"])
            self.assertIn("openWorldHint", ann, t["name"])
            if not ann["readOnlyHint"]:
                self.assertIn("destructiveHint", ann, t["name"])
                self.assertIn("idempotentHint", ann, t["name"])

    def test_readonly_set_semantics(self):
        tools = {t["name"]: t["annotations"] for t in self._tools(allow_mutate=True)}
        for name in ("list_repos", "search_repos", "status", "job_status"):
            self.assertTrue(tools[name]["readOnlyHint"], name)
            self.assertTrue(tools[name]["idempotentHint"], name)
            self.assertFalse(tools[name]["openWorldHint"], name)
        # clone_batch：写 + 触外网；dry_run 也是同一条工具（不能标只读）
        self.assertFalse(tools["clone_batch"]["readOnlyHint"])
        self.assertTrue(tools["clone_batch"]["openWorldHint"])
        # job_cancel：破坏性（终止子进程树）、非幂等
        self.assertFalse(tools["job_cancel"]["readOnlyHint"])
        self.assertTrue(tools["job_cancel"]["destructiveHint"])
        self.assertFalse(tools["job_cancel"]["idempotentHint"])
        # export_report：读 DB 写产出文件 → 非只读但幂等、不触外网
        self.assertFalse(tools["export_report"]["readOnlyHint"])
        self.assertTrue(tools["export_report"]["idempotentHint"])
        self.assertFalse(tools["export_report"]["openWorldHint"])

    def test_dry_run_tool_still_marked_mutating(self):
        """clone_batch 带 dry_run 也不得标 readOnly——写/读由参数决定时按保守口径。"""
        tools = {t["name"]: t["annotations"] for t in self._tools(allow_mutate=True)}
        self.assertFalse(tools["clone_batch"]["readOnlyHint"])


class TestServerJson(unittest.TestCase):
    def _load(self) -> dict:
        return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))

    def test_version_single_source(self):
        from gcm import __version__
        self.assertEqual(self._load()["version"], __version__)

    def test_server_entry_points_to_mcp(self):
        srv = self._load()["mcpServers"]["gcm"]
        self.assertEqual(srv["command"], "python")
        self.assertIn("-m", srv["args"])
        self.assertIn("gcm.mcp_server", srv["args"])
        self.assertEqual(srv["env"].get("GCM_MCP_ENABLE"), "1")

    def test_bump_carrier_covers_server_json(self):
        from scripts.bump_version import CARRIERS
        self.assertTrue(any(path == "server.json" for path, _, _ in CARRIERS),
                        "bump_version 载体表必须包含 server.json，否则发版漏改")


if __name__ == "__main__":
    unittest.main()
