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
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gcm import __version__ as CONTRACT_VERSION

from .mcp_jobs import cancel_job, job_snapshot, start_job
from .util.redact import redact, redact_row

_MCP_DISABLED_RC = 3  # 与 gcm/cli.py ECODE_MCP_DISABLED 一致
_SENSITIVE_COLS = ("token", "host_tokens", "ssh_key", "url")

# 只读工具定义（工具列表即契约，docs/agent-contract.md 同步）
_TOOLS: list[dict[str, Any]] = [
    {
        "name": "clone_batch",
        "mutate": True,
        "description": (
            "批量克隆仓库到数据目录（**写操作**：触网 + 写盘；仅 --allow-mutate "
            "时暴露）。复用 CLI --json 子进程，返回与 CLI 相同的顶层契约。"
            "传 dry_run=true 则只返回本地预检计划（不联网、不写盘）。"),
        "inputSchema": {
            "type": "object",
            "properties": {
                "urls": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "仓库地址列表（每项一个 URL）",
                },
                # G84-13：让 agent 能"先看看会发生什么"再决定要不要写盘。
                # 与 CLI --dry-run 共用同一份实现（app/batch_plan.py），不是第二套判定。
                "dry_run": {
                    "type": "boolean",
                    "description": "true = 只返回预览计划（将克隆/将更新/目录被占/"
                                   "重复/无效），不执行克隆",
                },
                "async": {
                    "type": "boolean",
                    "description": "true = 立即返回 job_id 并在后台执行（用 "
                                   "job_status 查询、job_cancel 取消）；默认同步等待",
                },
            },
            "required": ["urls"],
        },
    },
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
    {
        # G84-13：异步作业的状态查询（只读，任何时候都可用）
        "name": "job_status",
        "description": ("查询 clone_batch(async=true) 启动作业的进度/结果（只读）。"
                        "未完成时 finished=false；完成后 result 与同步版返回同形。"),
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "作业 ID"}},
            "required": ["job_id"],
        },
    },
    {
        # G84-13：取消作业（写操作 → 与 clone_batch 同一条放行门槛）
        "name": "job_cancel",
        "mutate": True,
        "description": ("取消一个正在跑的 clone_batch 作业（终止子进程树）。"
                        "需 GCM_MCP_ENABLE=1 + --allow-mutate。"),
        "inputSchema": {
            "type": "object",
            "properties": {"job_id": {"type": "string", "description": "作业 ID"}},
            "required": ["job_id"],
        },
    },
]


#: MCP 资源（G84-23）：把「只读事实」直接暴露成可寻址的 URI，
#: 让 agent 不必先调工具再自己拼装。**全部只读、全部过 redact**。
_RESOURCES: list[dict[str, Any]] = [
    {
        "uri": "gcm://status",
        "name": "统计概览",
        "description": "仓库数/同步次数/成功失败（与 status 工具同源）",
        "mimeType": "application/json",
    },
    {
        "uri": "gcm://repos",
        "name": "仓库清单",
        "description": "全部仓库元数据（已剔除 token/host_tokens/ssh_key 敏感列）",
        "mimeType": "application/json",
    },
    {
        "uri": "gcm://report/markdown",
        "name": "Markdown 报表",
        "description": "人类可读的仓库报表（内存生成，不写盘）",
        "mimeType": "text/markdown",
    },
]

#: MCP 提示词（G84-23）：把「怎么用这个服务」写进协议，而不是让人去翻文档。
_PROMPTS: list[dict[str, Any]] = [
    {
        "name": "clone-batch",
        "description": "把一批仓库地址交给 Git-clone-Max 克隆（会先给出预览计划）",
        "arguments": [
            {"name": "urls", "description": "仓库地址，逗号或换行分隔", "required": True},
        ],
    },
    {
        "name": "diagnose-failure",
        "description": "针对某个仓库的同步失败，给出排查顺序（基于错误分类与诊断项）",
        "arguments": [
            {"name": "repo", "description": "owner/repo 或 host/owner/repo",
             "required": True},
        ],
    },
]


def _resource_read(uri: str, data_dir: Path) -> dict[str, Any]:
    """读取一个 `gcm://` 资源，返回 MCP `resources/read` 的 contents 条目。

    **只读**：只查库/内存生成报表，不写盘、不触网；文本出站前过 `redact`。
    未知 URI 抛 ValueError（由调用方转成 JSON-RPC 错误码 -32602）。
    """
    db = _open_db(data_dir)
    try:
        if uri == "gcm://status":
            payload = json.dumps(db.stats_overview(), ensure_ascii=False, indent=2)
            return {"uri": uri, "mimeType": "application/json",
                    "text": redact(payload)}
        if uri == "gcm://repos":
            rows = _list_repos(db, host=None)
            payload = json.dumps({"repos": rows, "count": len(rows)},
                                 ensure_ascii=False, indent=2)
            return {"uri": uri, "mimeType": "application/json",
                    "text": redact(payload)}
        if uri == "gcm://report/markdown":
            if isinstance(db, _EmptyDB):
                return {"uri": uri, "mimeType": "text/markdown",
                        "text": "# Git-clone-Max 报表\n\n暂无数据（该数据目录还没有仓库记录）。\n"}
            import tempfile

            from .reports import export_markdown
            with tempfile.TemporaryDirectory() as td:
                out = Path(td) / "r.md"
                export_markdown(db, out)
                text = out.read_text(encoding="utf-8", errors="replace")
            return {"uri": uri, "mimeType": "text/markdown", "text": redact(text)}
    finally:
        db.close()
    raise ValueError(f"未知资源：{uri}（可用：{', '.join(r['uri'] for r in _RESOURCES)}）")


def _prompt_get(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """取一条提示词（MCP `prompts/get`）：把「怎么用」写进协议而不是文档。"""
    if name == "clone-batch":
        urls = str(arguments.get("urls") or "").strip()
        if not urls:
            raise ValueError("clone-batch 需要 urls 参数")
        steps = (
            "步骤：① 先调 clone_batch 且 dry_run=true 看预览（将克隆/将更新/目录被占/"
            "重复/无效）；② 预览没问题再调 clone_batch（长批次用 async=true + "
            "job_status 轮询）；③ 完成后调 status 或读 gcm://repos 确认入库。"
        )
        text = "用 Git-clone-Max 批量克隆下面这些仓库：" + "\n" + urls + "\n" + steps
        return {"description": "批量克隆（先预览再执行）",
                "messages": [{"role": "user",
                              "content": {"type": "text", "text": redact(text)}}]}
    if name == "diagnose-failure":
        repo = str(arguments.get("repo") or "").strip()
        if not repo:
            raise ValueError("diagnose-failure 需要 repo 参数")
        steps = "".join([
            "1) 读 gcm://repos 确认它是否入库、本地路径是否存在；" + "\n",
            "2) 看 status 的失败计数与最近错误分类（network/auth/platform/local/"
            "not_found/cancelled）；" + "\n",
            "3) auth 类 → 检查设置页「按平台凭据」是否给该 host 配了 token；" + "\n",
            "4) network 类 → 用 CLI `--diag` 跑环境体检，再考虑镜像/代理；" + "\n",
            "5) not_found → 大概率是仓库改名或私有仓无权限；" + "\n",
            "6) 仍然不明 → 导出诊断包（已打码）再分析。",
        ])
        text = "仓库 `" + repo + "` 同步失败时的排查顺序：" + "\n" + steps
        return {"description": "失败排查顺序",
                "messages": [{"role": "user",
                              "content": {"type": "text", "text": redact(text)}}]}
    raise ValueError(f"未知提示词：{name}（可用：{', '.join(p['name'] for p in _PROMPTS)}）")

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


def _readonly_tools() -> list[dict[str, Any]]:
    """未授权 mutate 时暴露的工具集（clone_batch 不存在 —— 不是"存在但报错"）。"""
    return [t for t in _TOOLS if not t.get("mutate")]


# ---------------------------------------------------------------- G84-13 异步作业
# 背景（G84 实测）：`clone_batch` 原来在 stdio 循环里**同步** `subprocess.run(timeout=3600)`
# —— 克隆几百个仓库时，整个 MCP 通道最长 1 小时不响应：没有 progress、不能取消、
# 连 `ping` 都不回。客户端只能超时断开，而进程其实还在跑（没人能停它）。
#
# 修法（契约只增不改）：
#   * `clone_batch` 增加 `async` 参数：true 时立即返回 `{job_id,status:"running"}`；
#   * 新增只读工具 `job_status` 与写工具 `job_cancel`（都需显式放行才出现在列表里）；
#   * 默认行为（不传 async）**完全不变**，老客户端与既有契约测试不受影响。

_WRITE_LOCK = threading.Lock()


def _write_message(obj: dict[str, Any], out: Any = None) -> None:
    """**唯一的 stdout 写入口**（C3 修复）。

    原实现只有 `_emit_notification` 加锁，主循环的 8 处 `out.write(...)` 一处都没加
    —— 作业线程发进度通知与主循环回响应会并发写同一个 stdout，而 `write()` 与
    `flush()` 还是两次调用；一旦交错，客户端收到的是非法 JSON-RPC 帧。
    现在所有写出都经过这里，`_WRITE_LOCK` 才真正覆盖「作业线程 vs 主循环」。
    """
    target = out if out is not None else sys.stdout
    line = json.dumps(obj, ensure_ascii=False) + "\n"
    with _WRITE_LOCK:
        target.write(line)
        target.flush()


def _emit_notification(kind: str, payload: dict[str, Any]) -> None:
    """从工作线程安全地写一条 JSON-RPC 通知（与主循环共用同一个写锁）。"""
    try:
        _write_message({"jsonrpc": "2.0", "method": kind, "params": payload})
    except Exception as e:      # noqa: BLE001 —— 通知失败不得影响作业本身
        _log(f"进度通知发送失败（不影响作业）：{e}")


def _audit_mutate(data_dir: Path, tool: str, args: dict[str, Any]) -> None:
    """Rev16 A7：放行后的写操作审计行（对齐 agent-contract.md §10.2）。

    HTTP 侧每请求落 `.gcm-api-audit.jsonl`，MCP 侧此前无任何审计 ——
    「放行但无声」违反契约。写 `.gcm-mcp-audit.jsonl` 于数据目录，
    负载打码（URL 可能内嵌凭据），追加失败写 applog 留痕（审计不阻断工具语义）。
    """
    entry = {
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tool": tool,
        # 复审 P1 修正：**先打码后截断**。redact 的正则要求完整结构（如
        # `ghp_+20位`），先 [:500] 截断会把凭据拦腰切成残段原样穿过 redact
        # （复审实测：60 个 URL 即 2040 字符，多 URL 批次常态化超阈值）。
        # args 是「打码后的截断文本」，不保证是合法 JSON。
        "args": redact(json.dumps(args, ensure_ascii=False))[:500],
    }
    try:
        log = data_dir / ".gcm-mcp-audit.jsonl"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001 —— audit failure must not break the tool call
        _log(f"mcp audit append failed: {e}")


def _run_sync_clone(msg_id: Any, data_dir: Path, body: str) -> dict[str, Any]:
    """同步执行 clone_batch：起 CLI 子进程（≤1h），返回 CLI 顶层 JSON。"""
    import subprocess as _sp
    cmd = [sys.executable, "-m", "gcm", "--cli", "--json", "--dir", str(data_dir)]
    try:
        proc = _sp.run(cmd, input=body, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=3600)
    except Exception as e:  # noqa: BLE001
        return _error(msg_id, -32603, f"克隆调度失败：{e}")
    try:
        payload = json.loads(proc.stdout or "{}")
    except ValueError:
        payload = {"error": {"code": -32603, "message": "克隆输出非 JSON",
                             "redacted": redact(proc.stdout or "")}}
    return _result(msg_id, {"clone_result": payload,
                            "cli_exit_code": proc.returncode})


def _handle_call(msg_id: Any, params: dict[str, Any], data_dir: Path,
                 allow_export: bool, allow_mutate: bool = False) -> dict[str, Any]:
    """处理 tools/call：只读工具 + 显式开关的 export_report / clone_batch。"""
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
        if name == "clone_batch":
            if not allow_mutate:
                return _error(msg_id, -32000,
                              "clone_batch 需要显式启用（GCM_MCP_ENABLE=1 + --allow-mutate）")
            urls = args.get("urls") or []
            if not isinstance(urls, list) or not urls:
                return _error(msg_id, -32602, "urls 必须是非空字符串数组")
            # Rev16 A7：放行即审计（dry_run 是纯预览，不写盘不触网，不审）。
            # 复审 P2 更正：审计语义 = 「放行后**实际执行**的写」。async 分支
            # 若作业表满 start_job 会抛 ValueError → 返回 -32602，那次调用没有
            # 真正执行写操作，先审后跑会产生假阳性行 —— async 路径移到
            # start_job 成功返回后落审计。
            body = "\n".join(str(u) for u in urls) + "\n"
            if bool(args.get("dry_run")):
                # G84-13：预览不落盘、不触网（连子进程都不起），与 CLI --dry-run 同源
                from .app.batch_plan import plan_batch
                plan = plan_batch(body, data_dir)
                return _result(msg_id, {"dry_run": True, "plan": plan.to_dict()})
            if bool(args.get("async")):
                # G84-13：异步作业 —— 立即返回 job_id，通道不再被阻塞 1 小时
                token = (params.get("_meta") or {}).get("progressToken")

                def _on_progress(n: int, text: str) -> None:
                    # 只有客户端给了 progressToken 才发（MCP 规范；无 token 会污染读流）
                    if token is not None and n % 2 == 0:
                        _emit_notification("notifications/progress",
                                           {"progressToken": token,
                                            "progress": n, "message": text})

                started = start_job(data_dir, body, token, on_progress=_on_progress)
                _audit_mutate(data_dir, "clone_batch", args)
                return _result(msg_id, started)
            _audit_mutate(data_dir, "clone_batch", args)
            return _run_sync_clone(msg_id, data_dir, body)
        if name == "job_status":
            job_id = str(args.get("job_id") or "")
            snap = job_snapshot(job_id)
            if snap is None:
                return _error(msg_id, -32602, f"未知 job_id：{job_id}")
            return _result(msg_id, snap)
        if name == "job_cancel":
            if not allow_mutate:
                return _error(msg_id, -32000,
                              "job_cancel 需要显式启用（GCM_MCP_ENABLE=1 + --allow-mutate）")
            job_id = str(args.get("job_id") or "")
            out = cancel_job(job_id)
            if out is None:
                return _error(msg_id, -32602, f"未知 job_id：{job_id}")
            # Rev16 A7：取消也是写意图（终止子进程树），落审计
            _audit_mutate(data_dir, "job_cancel", args)
            return _result(msg_id, out)
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


def run_stdio(data_dir: Path, allow_export: bool,
              allow_mutate: bool = False) -> int:
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
                _write_message(_result(msg_id, {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {
                        "tools": {"listChanged": False},
                        # G84-23：resources/prompts 此前是空对象（agent 只能人读文档）
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {"name": "gcm", "version": CONTRACT_VERSION},
                }), out)
            elif method == "notifications/initialized":
                pass  # 客户端确认，无响应体
            elif method == "tools/list":
                # Rev8 C5-1：未授权 mutate 时 clone_batch **不出现在列表里**
                tools = _TOOLS if allow_mutate else _readonly_tools()
                # R10：`mutate` 是内部授权标记，MCP `Tool` 对象没有这个字段；
                # 严格校验 additionalProperties=false 的客户端会因此拒绝整份列表。
                tools = [{k: v for k, v in t.items() if k != "mutate"} for t in tools]
                _write_message(_result(msg_id, {"tools": tools}), out)
            elif method == "tools/call":
                resp = _handle_call(msg_id, req.get("params") or {}, data_dir,
                                    allow_export, allow_mutate)
                _write_message(resp, out)
            elif method == "resources/list":
                _write_message(_result(msg_id, {"resources": _RESOURCES}), out)
            elif method == "resources/read":
                uri = str((req.get("params") or {}).get("uri") or "")
                try:
                    content = _resource_read(uri, data_dir)
                    resp = _result(msg_id, {"contents": [content]})
                except ValueError as e:
                    resp = _error(msg_id, -32602, str(e))
                except Exception as e:      # noqa: BLE001 —— 不能把 stdio 通道带崩
                    resp = _error(msg_id, -32603, f"读取资源失败：{e}")
                _write_message(resp, out)
            elif method == "prompts/list":
                _write_message(_result(msg_id, {"prompts": _PROMPTS}), out)
            elif method == "prompts/get":
                params = req.get("params") or {}
                try:
                    resp = _result(msg_id, _prompt_get(str(params.get("name") or ""),
                                                       params.get("arguments") or {}))
                except ValueError as e:
                    resp = _error(msg_id, -32602, str(e))
                except Exception as e:      # noqa: BLE001 —— 同上，通道优先
                    resp = _error(msg_id, -32603, f"读取提示词失败：{e}")
                _write_message(resp, out)
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
    allow_mutate = "--allow-mutate" in args
    if "--allow-mutate" in args:
        args.remove("--allow-mutate")
    if str(os.environ.get("GCM_MCP_ENABLE") or "").strip() != "1":
        _log(f"MCP 默认不启用：设置 GCM_MCP_ENABLE=1 后再运行（退出码 {_MCP_DISABLED_RC}）")
        return _MCP_DISABLED_RC
    # G69-18 修复：数据目录**必须**复用 `gcm.__main__.get_data_dir()`。
    # 原实现自拼 `<repo>/data`，违反 CLAUDE.md §5 红线（数据目录解析唯一来源），
    # 安装版（frozen）会指向 `_internal/data`，读不到 `%APPDATA%/Git-clone-Max`
    # 里的真实库 → MCP 客户端看到的是另一份（甚至新建的空）库。
    from .__main__ import get_data_dir
    data_dir = Path(os.environ.get("GCM_DATA_DIR") or get_data_dir())
    return run_stdio(data_dir, allow_export, allow_mutate)


if __name__ == "__main__":
    sys.exit(main())
