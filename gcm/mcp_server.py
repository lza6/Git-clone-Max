"""G56-2 MCP server（只读）：python -m gcm.mcp_server [--transport stdio]。

暴露只读工具（除 health/info 外不写盘、不触网）：
- list_repos  列出数据库仓库（默认数据目录 repos.db，GCM_DATA_DIR 覆盖）
- search_repos 按 owner/repo/host/tag 关键字搜索
- export_report 导出 CSV/Markdown 报表到输出目录（写文件，但只读数据源
  repos.db PRIMARY；默认关闭，需显式 GCM_MCP_ENABLE=1 + --allow-export）
- status 读取统计概览

安全边界：
- 默认只读：不暴露克隆/更新/删除等任何写工具；
- 显式启用：环境变量 GCM_MCP_ENABLE=1 才启动（否则退出码 3）；
- 无凭据输出：返回行均打码 + 剔除 token/host_tokens/ssh_key 敏感列；
- 绑定 stdio：MCP 客户端（Claude Desktop/Cursor）注册
  `python -m gcm.mcp_server` 即可握手。

实现：不依赖第三方 mcp 包，直接实现 MCP stdio 协议最小子集
（initialize / notifications/initialized / tools/list / tools/call），
保证无网络环境下可测可跑；协议字段与官方 MCP 规范对齐。
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .util.redact import redact

_MCP_DISABLED_RC = 3  # 与 gcm/cli.py ECODE_MCP_DISABLED 一致
_SENSITIVE_COLS = ("token", "host_tokens", "ssh_key", "url")

# 只读工具定义（工具列表即契约，docs/agent-contract.md 同步）
_TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_repos",
        "description": "列出数据库中的仓库（只读；支持 host 过滤）",
        "inputSchema": {
            "type": "object",
            "properties": {"host": {"type": "string", "description": "host 过滤（如 github.com）"}},
        },
    },
    {
        "name": "search_repos",
        "description": "按关键字搜索仓库（owner/repo/host/tag/note，只读）",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "搜索关键字"}},
            "required": ["query"],
        },
    },
    {
        "name": "export_report",
        "description": "导出报表 CSV/Markdown（只读数据源，写产出文件）",
        "inputSchema": {
            "type": "object",
            "properties": {
                "format": {"enum": ["csv", "markdown"], "description": "导出格式"},
                "out_dir": {"type": "string", "description": "输出目录（默认数据目录/reports）"},
            },
        },
    },
    {
        "name": "status",
        "description": "读取全局统计概览（仓库数/同步次数/成功失败，只读）",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


@dataclass
class McpRequest:
    """已解析的 MCP JSON-RPC 请求。"""

    id: Any
    method: str
    params: dict[str, Any]


def _log(text: str) -> None:
    """诊断日志走 stderr（stdio 通道只允许协议 JSON）。"""
    print(f"[mcp] {text}", file=sys.stderr)


def _read_request(line_iter) -> McpRequest | None:
    """读取一行 JSON-RPC 请求；EOF 返回 None。"""
    line = next(line_iter, "")
    if not line or not line.strip():
        return None
    try:
        raw = json.loads(line)
    except (ValueError, TypeError):
        return None
    return McpRequest(raw.get("id"), raw.get("method"), raw.get("params") or {})


def _result(msg_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    """JSON-RPC 成功响应。"""
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _error(msg_id: Any, code: int, message: str) -> dict[str, Any]:
    """JSON-RPC 错误响应（message 已打码）。"""
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": redact(message)}}


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    """剔除敏感列并打码 URL；非 dict 原样返回。"""
    if not isinstance(row, dict):
        return row
    return {k: (redact(str(v)) if k == "url" else v) for k, v in row.items()
            if k not in _SENSITIVE_COLS or k == "url"}


def _list_repos(db, host: str | None = None) -> list[dict[str, Any]]:
    """只读：列出仓库（host 过滤，敏感列剔除）。"""
    return [_sanitize_row(r) for r in db.list_repos(host=host)]


def _search_repos(db, query: str) -> list[dict[str, Any]]:
    """只读：关键字搜索（owner/repo/host/folder_name/tag/note）。"""
    q = (query or "").strip().lower()
    if not q:
        return []
    try:
        rows = db.list_repos(host=None)
    except Exception:  # noqa: BLE001
        return []
    hits = []
    for r in rows:
        blob = " ".join(str(r.get(k) or "") for k in
                        ("owner", "repo", "host", "folder_name", "tags", "note"))
        if q in blob.lower():
            hits.append(_sanitize_row(r))
    return hits

def _open_db(data_dir: Path):
    """惰性打开仓库数据库（只读用途；注意其线程锁与 WAL）。"""
    from .db.repo_db import Database
    return Database(data_dir / "repos.db")


def _export_report(db, fmt: str, out_dir: str | None) -> str:
    """G56-2 报表导出：写产出文件到 out_dir（默认数据目录/reports）。"""
    from .reports import export_csv, export_markdown
    base = Path(out_dir) if out_dir else db.path.parent / "reports"
    base.mkdir(parents=True, exist_ok=True)
    if fmt == "csv":
        rows = export_csv(db, base / "repos_report.csv")
        return f"已导出 {rows} 行到 {base / 'repos_report.csv'}"
    rows = export_markdown(db, base / "repos_report.md")
    return f"已导出 {rows} 行到 {base / 'repos_report.md'}"


def _handle_call(msg_id: Any, params: dict[str, Any], data_dir: Path,
                 allow_export: bool) -> dict[str, Any]:
    """处理 tools/call：只读工具 + 显式开关的 export_report。"""
    name = str(params.get("name") or "")
    args = params.get("arguments") or {}
    try:
        db = _open_db(data_dir)
    except Exception as e:  # noqa: BLE001
        return _error(msg_id, -32603, f"打开数据库失败：{e}")
    try:
        if name == "list_repos":
            rows = _list_repos(db, host=args.get("host"))
            return _result(msg_id, {"repos": rows, "count": len(rows)})
        if name == "search_repos":
            rows = _search_repos(db, args.get("query") or "")
            return _result(msg_id, {"repos": rows, "count": len(rows)})
        if name == "status":
            return _result(msg_id, {"status": db.stats_overview()})
        if name == "export_report":
            if not allow_export:
                return _error(msg_id, -32000,
                            "export_report 需要显式启用（GCM_MCP_ENABLE=1 + --allow-export）")
            fmt = str(args.get("format") or "csv")
            if fmt not in ("csv", "markdown"):
                return _error(msg_id, -32602, f"不支持的格式：{fmt}")
            out = _export_report(db, fmt, args.get("out_dir"))
            return _result(msg_id, {"message": out})
        return _error(msg_id, -32601, f"未知工具：{name}")
    finally:
        db.close()


def run_stdio(data_dir: Path, allow_export: bool) -> int:
    """stdio 循环：逐行读 JSON-RPC，写响应；Ctrl-C/EOF 退出 0。"""
    out = sys.stdout
    try:
        for raw in sys.stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                req = json.loads(raw)
            except ValueError:
                _log("收到非法 JSON，忽略")
                continue
            msg_id = req.get("id")
            method = req.get("method")
            if method == "initialize":
                out.write(json.dumps(_result(msg_id, {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {},
                        "prompts": {},
                    },
                    "serverInfo": {"name": "gcm", "version": "8.0.2"},
                }), ensure_ascii=False) + "\n")
                out.flush()
            elif method == "notifications/initialized":
                pass  # 客户端确认，无响应体
            elif method == "tools/list":
                out.write(json.dumps(_result(msg_id, {"tools": _TOOLS}), ensure_ascii=False) + "\n")
                out.flush()
            elif method == "tools/call":
                resp = _handle_call(msg_id, req.get("params") or {}, data_dir, allow_export)
                out.write(json.dumps(resp, ensure_ascii=False) + "\n")
                out.flush()
            else:
                _log(f"忽略未知方法：{method}")
    except KeyboardInterrupt:
        return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    """入口：GCM_MCP_ENABLE=1 才启动；支持 --allow-export 显式放行报表导出。"""
    args = list(sys.argv[1:] if argv is None else argv)
    allow_export = "--allow-export" in args
    if "--allow-export" in args:
        args.remove("--allow-export")
    if str(os.environ.get("GCM_MCP_ENABLE") or "").strip() != "1":
        _log(f"MCP 默认不启用：设置 GCM_MCP_ENABLE=1 后再运行（退出码 {_MCP_DISABLED_RC}）")
        return _MCP_DISABLED_RC
    base_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir = Path(os.environ.get("GCM_DATA_DIR") or base_dir)
    return run_stdio(data_dir, allow_export)


if __name__ == "__main__":
    sys.exit(main())
