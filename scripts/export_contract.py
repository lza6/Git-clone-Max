#!/usr/bin/env python
"""Rev8 C5-2：对外契约 schema 自动导出。

从三个对外面的**源码**生成 JSON schema，供 `docs/agent-contract.md` 对照与
CI `--check` 守卫：
- MCP 工具列表（gcm/mcp_server._TOOLS / _readonly_tools）
- CLI --json 顶层契约（gcm.cli 的文档化 schema，取自 agent-contract 定义处）
- HTTP 端点清单（gcm/http_api 的 GET/POST 路径表）

用法：
    python scripts/export_contract.py --out docs/contract           # 生成/更新
    python scripts/export_contract.py --out docs/contract --check   # CI 守卫
退出码：0 一致 / 1 有差异或生成失败。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def export_mcp_tools() -> dict:
    """MCP 工具 / 资源 / 提示词清单（含授权差异：写工具仅 --allow-mutate 暴露）。"""
    from gcm.mcp_server import _PROMPTS, _RESOURCES, _TOOLS, _readonly_tools

    def _public(tools: list[dict]) -> list[dict]:
        """剥掉内部授权标记（`mutate`），与运行时 `tools/list` 输出保持一致。

        审查发现：运行时已经剥了，导出却没剥 → 契约文件里留着 MCP `Tool` 没有的字段，
        严格校验 additionalProperties=false 的客户端会拒绝。
        """
        return [{k: v for k, v in t.items() if k != "mutate"} for t in tools]

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "gcm MCP surface",
        "always_available": _public(_readonly_tools()),
        "requires_allow_mutate": _public([
            t for t in _TOOLS if t["name"] not in
            {x["name"] for x in _readonly_tools()}]),
        # G84-23：资源与提示词也是契约的一部分（此前 initialize 里是空对象）
        "resources": _RESOURCES,
        "prompts": _PROMPTS,
    }


def export_cli_json() -> dict:
    """CLI --json 顶层契约（从 docs/agent-contract.md 的定义源码化）。"""
    from gcm import __version__
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "gcm CLI --json top-level contract",
        "version": __version__,
        "type": "object",
        "required": ["version", "ok", "counts", "results", "invalid"],
        "properties": {
            "version": {"type": "string"},
            "ok": {"type": "boolean"},
            "counts": {"type": "object",
                       "description": "success/failed/other/total"},
            "results": {"type": "array", "items": {"type": "object"}},
            "invalid": {"type": "array", "items": {"type": "object"}},
        },
        "exit_codes": {"0": "全部成功", "1": "有失败", "2": "无有效输入",
                       "3": "MCP 未启用"},
    }


def export_http_endpoints() -> dict:
    """HTTP 端点清单（静态登记；新增端点必须同步这里，否则 --check 红）。"""
    from gcm import __version__
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "gcm local HTTP API endpoints",
        "version": __version__,
        "get": [
            {"path": "/version", "auth": "never"},
            {"path": "/api/repos", "auth": "bearer-if-configured"},
            {"path": "/api/status", "auth": "bearer-if-configured"},
            {"path": "/api/repos/search", "auth": "bearer-if-configured"},
            {"path": "/api/events", "auth": "bearer-if-configured"},
            {"path": "/api/events/snapshot", "auth": "bearer-if-configured"},
            {"path": "/api/export/report", "auth": "bearer-if-configured"},
        ],
        "post": [
            {"path": "/api/clone", "auth": "bearer-if-configured",
             "requires": "--allow-mutate"},
        ],
        "binding": "loopback-only (127.0.0.1 / localhost)",
    }


EXPORTERS = {
    "mcp_tools.schema.json": export_mcp_tools,
    "cli_json.schema.json": export_cli_json,
    "http_endpoints.schema.json": export_http_endpoints,
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "docs/contract"))
    ap.add_argument("--check", action="store_true",
                    help="只校验不写入；有差异退出 1")
    args = ap.parse_args(argv)
    out_dir = Path(args.out)
    diffs: list[str] = []
    for name, fn in EXPORTERS.items():
        want = json.dumps(fn(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        p = out_dir / name
        if args.check:
            have = p.read_text(encoding="utf-8") if p.exists() else ""
            if have != want:
                diffs.append(name)
        else:
            out_dir.mkdir(parents=True, exist_ok=True)
            p.write_text(want, encoding="utf-8")
            print(f"written: {p}")
    if args.check:
        if diffs:
            print("DRIFT: " + ", ".join(diffs))
            print("运行 python scripts/export_contract.py --out docs/contract 更新")
            return 1
        print("CONTRACT OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
