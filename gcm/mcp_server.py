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
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gcm import __version__ as CONTRACT_VERSION

from .util.redact import redact, redact_row

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


def _force_utf8_streams() -> None:
    """把 stdio 两个流显式设成 UTF-8，避免**因控制台代码页而崩**。

    G69-18 修复（真实产品缺陷）：本服务的 JSON-RPC 响应用
    `ensure_ascii=False` 写 stdout，日志走 stderr，**都含中文**。
    而 Windows 控制台默认是本地代码页（英文系统 cp1252、中文系统 cp936），
    `sys.stdout.write` 遇到无法编码的字符会抛 `UnicodeEncodeError` ——
    实测在英文 Windows runner 上直接以 rc=1 中断，
    **把 JSON-RPC 通道打断在协议中途**，客户端只能拿到半截。

    stdio 服务的正确做法是显式声明 UTF-8（MCP 协议本身即 UTF-8），
    并对无法编码的字符降级替换而不是抛异常。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


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
    """剔除敏感列，并对**所有文本值**打码（G80-B1：message/detail 也要打码）。"""
    if not isinstance(row, dict):
        return row
    kept = {k: v for k, v in row.items() if k not in _SENSITIVE_COLS or k == "url"}
    return redact_row(kept)


def _list_repos(db, host: str | None = None) -> list[dict[str, Any]]:
    """只读：列出仓库（host 过滤，敏感列剔除）。"""
    return [_sanitize_row(r) for r in db.list_repos(host=host)]


def _search_repos(db, query: str) -> list[dict[str, Any]]:
    """只读：关键字搜索（G80-B4-3：下推 SQL，不再取全表在内存里过滤）。"""
    q = (query or "").strip()
    if not q:
        return []
    return [_sanitize_row(r) for r in db.search_repos(q, limit=500)]


class _EmptyDB:
    """空数据目录的只读替身：四个工具都能正常工作，且**不会创建任何文件**。

    G80-B1：原先 `_open_db` 直接建可写连接 —— 对空数据目录等于「列一下仓库」
    顺手创建 repos.db（把只读契约变成写操作）；对旧库则每次请求都跑
    `executescript(_SCHEMA)+_migrate()+commit()`，与 GUI 抢写锁。
    G80-B4-3：补 `search_repos`，与真实 Database 的接口保持一致。
    """

    def list_repos(self, host: str | None = None) -> list[dict[str, Any]]:
        return []

    def search_repos(self, query: str, host: str | None = None,
                     limit: int = 500) -> list[dict[str, Any]]:
        return []

    def stats_overview(self) -> dict[str, Any]:
        return {"total_repos": 0, "total_syncs": 0, "success": 0, "failed": 0}

    def close(self) -> None:
        return None


def _open_db(data_dir: Path):
    """打开仓库库，**只读**（G80-B1）。

    三条路径：
    1. 库文件不存在 → `_EmptyDB`（不创建文件，保持只读契约）；
    2. 库存在且 schema 可用 → `Database(readonly=True)`（mode=ro，无写锁、无建表）；
    3. 库存在但缺表/缺列（v1 旧库）→ 用可写连接做**一次**幂等建表/迁移，
       关闭后立刻回到只读连接（否则每次请求都跑写事务，G70 实测 p50≈7s）。
    """
    from .db.repo_db import Database

    path = data_dir / "repos.db"
    if not path.exists():
        return _EmptyDB()
    try:
        return Database(path, readonly=True)
    except sqlite3.OperationalError:
        # 只读快路径的 `SELECT favorite` 失败 = 旧库/半成品库 → 迁移一次
        writable = Database(path)
        writable.close()
        return Database(path, readonly=True)


def _export_report(db, fmt: str, out_dir: str | None) -> str:
    r"""G56-2 报表导出：写产出文件到 out_dir（默认数据目录/reports）。

    G69-9：`out_dir` **必须限定在数据目录内**。原实现直接 `Path(out_dir)` +
    `mkdir(parents=True)` —— MCP 客户端可传任意绝对路径，在用户可写范围
    任意位置建目录并覆盖同名文件（实测写到了系统临时目录下的深层嵌套路径）。
    报表文件名固定，危害上限是「任意目录创建 + 覆盖同名文件」，但没有任何理由放开。
    """
    from .reports import export_csv, export_markdown
    data_root = Path(db.path).parent.resolve()
    if out_dir:
        cand = Path(out_dir)
        if not cand.is_absolute():
            cand = data_root / cand
        try:
            cand = cand.resolve()
        except Exception as e:
            raise ValueError("out_dir 无法解析") from e
        if cand != data_root and data_root not in cand.parents:
            raise ValueError(f"out_dir 必须位于数据目录内：{data_root}")
        base = cand
    else:
        base = data_root / "reports"
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
    except ValueError as e:
        # G69-18 修复：参数类错误（如 out_dir 越界）必须转成 **JSON-RPC 错误**返回。
        # 原实现让它逃逸出 stdio 循环 → 进程以 traceback 退出、通道中断，
        # MCP 客户端拿到的是裸栈而不是结构化错误，且后续请求再无响应。
        return _error(msg_id, -32602, str(e))
    except Exception as e:  # noqa: BLE001
        # 工具内部异常同理：不能把 stdio 通道带崩
        return _error(msg_id, -32603, f"工具执行失败：{e}")
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
                    "serverInfo": {"name": "gcm", "version": CONTRACT_VERSION},
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
    _force_utf8_streams()      # 必须最先：其后所有输出都是中文/JSON
    args = list(sys.argv[1:] if argv is None else argv)
    allow_export = "--allow-export" in args
    if "--allow-export" in args:
        args.remove("--allow-export")
    if str(os.environ.get("GCM_MCP_ENABLE") or "").strip() != "1":
        _log(f"MCP 默认不启用：设置 GCM_MCP_ENABLE=1 后再运行（退出码 {_MCP_DISABLED_RC}）")
        return _MCP_DISABLED_RC
    # G69-18 修复：数据目录**必须**复用 `gcm.__main__.get_data_dir()`。
    # 原实现自拼 `<repo>/data`，违反 CLAUDE.md §5 红线（数据目录解析唯一来源），
    # 安装版（frozen）会指向 `_internal/data`，读不到 `%APPDATA%/Git-clone-Max`
    # 里的真实库 → MCP 客户端看到的是另一份（甚至新建的空）库。
    from .__main__ import get_data_dir
    data_dir = Path(os.environ.get("GCM_DATA_DIR") or get_data_dir())
    return run_stdio(data_dir, allow_export)


if __name__ == "__main__":
    sys.exit(main())
