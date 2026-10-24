"""G56-4 本地 HTTP API（默认只读）：python -m gcm --cli --serve 127.0.0.1:8765。

端点：
- GET  /version              版本与只读模式声明
- GET  /api/repos            仓库列表（host 过滤，敏感列剔除）
- GET  /api/repos/search     关键字搜索（q 必填）
- GET  /api/status           全局统计概览
- GET  /api/report           报表（format=csv|markdown|json，内存生成，不写盘）
- GET  /api/events           **SSE 事件流**（G71-1：批次/任务/重试/错误，长连接）
- GET  /api/events/snapshot  最近事件快照（JSON，供不便维持长连接的 agent）
- POST /api/clone            批量克隆（默认 403；仅 --allow-mutate 时执行，审计落盘）

安全边界：
- 只绑定 127.0.0.1；无 CORS 头（跨域 JS 读不到）；
- 默认只读：无任何写端点；POST 需显式 --allow-mutate；
- 输出全部 redact 打码，不泄露 token/host_tokens/ssh_key；
- 每次请求记审计日志（仅 allow_mutate 时落盘到 <root>/.gcm-api-audit.jsonl）。
- SSE 事件在入总线前已打码；本服务无鉴权，仍只允许回环客户端。
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .util.redact import redact, redact_row

_SENSITIVE_COLS = ("token", "host_tokens", "ssh_key", "url")

# G80-B4-3：写端点请求体上限（1 MiB）。每行一个仓库地址，正常批量远小于此。
_MAX_BODY_BYTES = 1 << 20

# G66-6：仅允许回环绑定（服务本身无鉴权，非回环等于把仓库清单与写操作对全网开放）。
# G67-5 修正：**移除 `::1`** —— `GcmApiServer` 继承 `ThreadingHTTPServer`，
# `address_family` 固定为 `AF_INET`，把 IPv6 字面量交给 AF_INET 的 getaddrinfo
# 必然抛 `gaierror`（实测 `serve("::1:8791")` → `[Errno 11001] getaddrinfo failed`），
# 而原来只捕 `ValueError` → 整个命令以 traceback 崩溃（rc=1），违反 docstring 承诺的
# 「绑定失败返回 2」。IPv6 绑定需改 `AF_INET6`，超出本次范围，故明确不支持并给出可读错误。
_LOOPBACK_HOSTS = frozenset({
    "127.0.0.1", "127.0.0.2", "127.0.0.3", "localhost",
})


def _looks_like_ipv6(host: str) -> bool:
    """粗判 IPv6 字面量（含方括号写法），用于给出明确的不支持提示。"""
    h = host.strip()
    return h.startswith("[") or h.count(":") >= 2


def _version() -> str:
    """项目版本号（延迟导入避免循环）。"""
    from gcm import __version__
    return __version__


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    """剔除敏感列，并对**所有文本值**打码（G80-B1：message/detail 里也有凭据）。"""
    if not isinstance(row, dict):
        return row
    kept = {k: v for k, v in row.items() if k not in _SENSITIVE_COLS or k == "url"}
    return redact_row(kept)


class _ApiHandler(BaseHTTPRequestHandler):
    """请求处理器：通过 self.server 读取 root / allow_mutate。"""

    server: GcmApiServer  # type: ignore[misc]

    def log_message(self, fmt: str, *args: Any) -> None:  # 抑制默认 stderr 访问日志
        return

    # ------------------------------------------------------------ 辅助
    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _log_server_error(self, prefix: str, exc: Exception) -> None:
        """G70：内部异常只写服务端日志，不回显给回环客户端（可能含路径/SQL）。"""
        try:
            from .app.applog import warn as _warn
            _warn(f"[http_api] {prefix}：{exc}")
        except Exception:
            pass

    def _host_is_loopback(self) -> bool:
        """请求的 Host 头是否解析到回环（G69-8）。

        只绑回环**不足以**防 DNS rebinding：攻击者把自己的域名解析到 127.0.0.1，
        浏览器就会与本地服务**同源**（CORS 不参与），页面即可读取仓库清单
        （实测伪造 `Host: evil.example.com` 仍返回 200；`local_path` 里的
        用户目录名也随之泄露）。所以必须校验 Host 头本身。

        `--allow-mutate` 下更严重：`POST /api/clone` 是 simple request，
        任意网页无需 CORS 即可盲发写请求。
        """
        raw = (self.headers.get("Host") or "").strip()
        if not raw:
            return False          # 缺 Host 头 → 保守拒绝
        host = raw
        if host.startswith("["):  # IPv6 字面量（本服务不支持绑定，但不误判）
            host = host.split("]", 1)[0][1:]
        elif ":" in host:
            host = host.split(":", 1)[0]
        return host.strip().lower() in _LOOPBACK_HOSTS

    def _origin_ok(self) -> bool:
        """Origin 必须缺失（非浏览器）或指向回环。

        浏览器发起的跨站写请求一定带 Origin；非浏览器客户端（curl / agent SDK）
        通常不带 —— 因此「缺失」放行、「跨站」拒绝。
        """
        origin = (self.headers.get("Origin") or "").strip()
        if not origin:
            return True
        try:
            from urllib.parse import urlparse
            oh = (urlparse(origin).hostname or "").strip().lower()
        except Exception:
            return False
        return oh in _LOOPBACK_HOSTS

    def _reject_cross_origin(self) -> bool:
        """跨站/非回环 Host 直接 403；返回 True 表示已拒绝。"""
        if self._host_is_loopback() and self._origin_ok():
            return False
        self._error(403, "拒绝：Host/Origin 非回环（防 DNS rebinding）", status=403)
        return True

    def _error(self, code: int, message: str, status: int = 400) -> None:
        self._send_json({"error": {"code": code, "message": message,
                                   "redacted": redact(message)}}, status=status)

    def _audit(self, method: str, path: str) -> None:
        """写操作审计（仅 allow_mutate 时落盘；只读模式跳过）。"""
        server: GcmApiServer = self.server  # type: ignore[assignment]
        if not server.allow_mutate:
            return
        entry = {
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "method": method,
            "path": redact(path),
        }
        try:
            log = Path(server.root) / ".gcm-api-audit.jsonl"
            log.parent.mkdir(parents=True, exist_ok=True)
            with log.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------ G71-1 SSE
    @staticmethod
    def _bounded_int(qs: dict[str, list[str]], key: str, default: int,
                     lo: int, hi: int) -> int:
        """解析有界整数查询参数；越界/非整数抛 ValueError（由调用方转 400）。"""
        raw = (qs.get(key) or [str(default)])[0]
        if len(str(raw)) > 12:      # 防超长数字触发 O(n²) 十进制转换
            raise ValueError(f"{key} 过大")
        val = int(raw)
        if not (lo <= val <= hi):
            raise ValueError(f"{key} 必须在 {lo}..{hi} 之间")
        return val

    @staticmethod
    def _parse_types(qs: dict[str, list[str]]) -> list[str] | None:
        raw = (qs.get("types") or [""])[0].strip()
        if not raw:
            return None
        # 去重且保持顺序；未知类型不报错 —— 订阅未来新增类型时向后兼容。
        seen: list[str] = []
        for part in raw.split(","):
            t = part.strip()
            if t and t not in seen:
                seen.append(t)
        return seen or None

    def _events_snapshot(self, qs: dict[str, list[str]]) -> None:
        """最近事件快照（JSON）：不适合维持 SSE 长连接的 agent 用。"""
        from .app.event_bus import get_event_bus
        try:
            limit = self._bounded_int(qs, "limit", 100, 1, 500)
        except (TypeError, ValueError) as e:
            self._error(2, str(e))
            return
        server: GcmApiServer = self.server  # type: ignore[assignment]
        bus = getattr(server, "event_bus", None) or get_event_bus()
        events = bus.snapshot(limit=limit, types=self._parse_types(qs))
        self._send_json({
            "events": [e.to_dict() for e in events],
            "count": len(events),
            "stats": bus.stats(),
        })

    def _client_gone(self) -> bool:
        """非阻塞探测客户端是否已断开（G71-11，审查 S5）。

        只靠 `wfile.write` 抛 BrokenPipe 有个盲区：客户端断开后若服务端一直
        没有事件/心跳，写操作不会发生，订阅会挂到下一次心跳才清理。
        用 `select + MSG_PEEK` 立即判断 EOF，不消费任何数据。
        """
        try:
            import select
            import socket
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            return not self.connection.recv(1, socket.MSG_PEEK)
        except (OSError, ValueError):
            return True
        except Exception:
            return False

    @staticmethod
    def _parse_last_event_id(raw: str | None) -> int | None:
        """解析 SSE `Last-Event-ID` 请求头；非法/越界 → None（退回 replay）。"""
        text = (raw or "").strip()
        if not text or len(text) > 20:
            return None
        try:
            value = int(text)
        except (TypeError, ValueError):
            return None
        return value if 0 <= value <= 2**63 - 1 else None

    def _stream_events(self, qs: dict[str, list[str]]) -> None:
        """SSE 长连接：心跳 + 客户端断开自动清理订阅。

        为什么用 HTTP/1.0 + `Connection: close`：`BaseHTTPRequestHandler` 不会
        自动分块编码（no chunked support），而 SSE 没有 Content-Length。HTTP/1.0
        的「读到连接关闭为止」语义与 EventSource / curl -N 都兼容，且实现最简单。
        """
        from .app.event_bus import get_event_bus
        try:
            replay = self._bounded_int(qs, "replay", 0, 0, 500)
            heartbeat = self._bounded_int(qs, "heartbeat", 15, 1, 60)
        except (TypeError, ValueError) as e:
            self._error(2, str(e))
            return
        types = self._parse_types(qs)
        # G72：浏览器 EventSource 重连会自动带 Last-Event-ID，优先级高于 replay
        last_id = self._parse_last_event_id(self.headers.get("Last-Event-ID"))

        server: GcmApiServer = self.server  # type: ignore[assignment]
        stop = getattr(server, "_stop_event", None)
        # G72：限制并发 SSE 订阅，防止慢/挂死客户端把线程堆满
        with server._sse_lock:
            if server._sse_active >= int(getattr(server, "sse_max", 16)):
                self.send_response(503)
                self.send_header("Retry-After", "5")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            server._sse_active += 1
        bus = getattr(server, "event_bus", None) or get_event_bus()
        try:
            sub = bus.subscribe(
                replay=0 if last_id is not None else replay,
                types=types, label="sse")
        except Exception:
            with server._sse_lock:
                server._sse_active = max(0, server._sse_active - 1)
            raise
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self.send_header("Connection", "close")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            self.close_connection = True
            try:
                # 写超时：慢/挂死客户端不会永久占用一个 handler 线程
                self.connection.settimeout(30.0)
            except OSError:
                pass
            # EventSource 重连间隔（毫秒）+ 一条注释帧证明连接已建立
            self.wfile.write(b"retry: 3000\n\n: connected\n\n")
            last_sent = 0
            if last_id is not None:
                oldest = bus.oldest_seq(types)
                if oldest and last_id < oldest - 1:
                    self.wfile.write(b": history-truncated\n\n")
                for ev in bus.since(last_id, limit=500, types=types):
                    self.wfile.write(ev.sse_frame())
                    last_sent = max(last_sent, ev.seq)
            self.wfile.flush()
            last_write = time.monotonic()
            while (stop is None or not stop.is_set()) and not sub.closed:
                events = sub.wait(timeout=min(1.0, float(heartbeat)))
                if events:
                    for ev in events:
                        if ev.seq <= last_sent:
                            continue      # 已由 Last-Event-ID 补发过
                        self.wfile.write(ev.sse_frame())
                        last_sent = ev.seq
                    self.wfile.flush()
                    last_write = time.monotonic()
                elif time.monotonic() - last_write >= heartbeat:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_write = time.monotonic()
                if self._client_gone():
                    break
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError,
                TimeoutError, ValueError, OSError):
            pass                      # 客户端正常断开（关页面 / curl 退出）
        finally:
            sub.close()
            with server._sse_lock:
                server._sse_active = max(0, server._sse_active - 1)

    # ------------------------------------------------------------ GET
    def do_GET(self) -> None:
        server: GcmApiServer = self.server  # type: ignore[assignment]
        if self._reject_cross_origin():   # G69-8
            return
        self._audit("GET", self.path)
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        # G71-1：SSE / 版本查询**不依赖数据库** —— 必须在打开 DB 之前处理，
        # 否则一条长连接会白占一个 SQLite 连接（SSE 可能持续数小时）。
        if parsed.path == "/version":
            self._send_json({"version": _version(),
                             "readonly": not server.allow_mutate})
            return
        if parsed.path == "/api/events":
            self._stream_events(qs)
            return
        if parsed.path == "/api/events/snapshot":
            self._events_snapshot(qs)
            return
        try:
            from .db.repo_db import Database
            # G70：读端点走只读连接，跳过建表/迁移写事务（并发读不再被串行化）。
            # 首次运行 DB 尚不存在时 mode=ro 会失败 → 回退到普通连接建库。
            try:
                db = Database(server.data_dir / "repos.db", readonly=True)
            except Exception:
                db = Database(server.data_dir / "repos.db")
        except Exception as e:  # noqa: BLE001
            self._log_server_error("打开数据库失败", e)
            self._error(-32603, "打开数据库失败", status=500)
            return
        try:
            if parsed.path == "/api/repos":
                host = qs.get("host", [None])[0]
                # G70：分页（默认 500，最大 5000）—— 仓库数上千时避免一次性序列化
                try:
                    limit_raw = (qs.get("limit") or ["500"])[0]
                    offset_raw = (qs.get("offset") or ["0"])[0]
                    # G70：先限长度再 int() —— Python 3.10 没有 4300 位上限，
                    # 超长纯数字查询会触发 O(n²) 十进制转换并持有 GIL。
                    if len(str(limit_raw)) > 12 or len(str(offset_raw)) > 12:
                        self._error(2, "limit/offset 过大")
                        return
                    limit = int(limit_raw)
                    offset = int(offset_raw)
                except (TypeError, ValueError):
                    self._error(2, "limit/offset 必须是整数")
                    return
                if not (1 <= limit <= 5000):
                    self._error(2, "limit 必须在 1..5000 之间")
                    return
                if offset < 0:
                    self._error(2, "offset 必须 ≥0")
                    return
                # G70：分页下推到 SQL（避免每次取全表再切片；5000 仓时差一个数量级）
                page, total = db.list_repos_page(host=host, limit=limit, offset=offset)
                rows = [_sanitize_row(r) for r in page]
                self._send_json({"repos": rows, "count": len(rows), "total": total,
                                 "limit": limit, "offset": offset,
                                 "truncated": total > offset + len(rows)})
            elif parsed.path == "/api/repos/search":
                q = (qs.get("q") or [""])[0].strip().lower()
                if not q:
                    self._error(2, "缺少 q 参数")
                    return
                # G80-B4-3：下推成 SQL（原先取全表再在内存里过滤）
                hits = db.search_repos(q, limit=500)
                self._send_json({"repos": [_sanitize_row(r) for r in hits],
                                 "count": len(hits), "limit": 500,
                                 "truncated": len(hits) >= 500})
            elif parsed.path == "/api/status":
                self._send_json({"status": db.stats_overview(),
                                 "readonly": not server.allow_mutate})
            elif parsed.path == "/api/report":
                fmt = (qs.get("format") or ["json"])[0].strip().lower()
                # G80-B4-3：原来非法 format 会**静默回落 JSON**（调用方以为自己拿到 csv）
                if fmt not in ("json", "csv", "markdown"):
                    self._error(2, f"format 只支持 json/csv/markdown，收到 {fmt!r}", status=400)
                    return
                # G69-10：原实现用 `db.list_repos()`（repos 表）却按 sync_history 的列名
                # 取字段 → status/action/message/commits/duration_ms/started_at **恒为空**
                # （实测输出 `a,b,github.com,,,,,,`），与 agent-contract 的「报表」承诺不符。
                # 改走报表自己的数据源（repos LEFT JOIN sync_history），并复用同一套列定义，
                # 避免第二份列名再漂移。
                from .reports import EXPORT_COLUMNS, _fetch_rows, sanitize_cell
                rows = _fetch_rows(db, None)
                if fmt == "csv":
                    import io
                    buf = io.StringIO()
                    cols = tuple(EXPORT_COLUMNS)
                    buf.write(",".join(cols) + "\r\n")
                    for r in rows:
                        buf.write(",".join(
                            str(sanitize_cell(r.get(c)) if r.get(c) is not None else "")
                            for c in cols) + "\r\n")
                    body = buf.getvalue().encode("utf-8-sig")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/csv; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                elif fmt == "markdown":
                    lines = ["| owner | repo | host | folder |",
                             "|------|------|------|--------|"]
                    for r in rows:
                        lines.append(f"| {r.get('owner') or ''} | {r.get('repo') or ''} | "
                                     f"{r.get('host') or ''} | {r.get('folder_name') or ''} |")
                    body = ("\n".join(lines) + "\n").encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/markdown; charset=utf-8")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self._send_json({"repos": [_sanitize_row(r) for r in rows],
                                     "count": len(rows)})
            else:
                self._error(404, f"未知路径：{parsed.path}", status=404)
        except Exception as e:  # noqa: BLE001
            self._log_server_error("处理失败", e)
            self._error(-32603, "处理失败", status=500)
        finally:
            db.close()

    # ------------------------------------------------------------ POST（写，默认拒绝）
    def do_POST(self) -> None:
        server: GcmApiServer = self.server  # type: ignore[assignment]
        if self._reject_cross_origin():   # G69-8
            return
        if not server.allow_mutate:
            self._error(403, "写操作被拒绝：当前为只读模式（--allow-mutate 显式开启）", status=403)
            return
        self._audit("POST", self.path)
        from urllib.parse import urlparse
        parsed = urlparse(self.path)
        if parsed.path != "/api/clone":
            self._error(404, f"未知路径：{parsed.path}", status=404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        # G80-B4-3：请求体上限（原来无限制，一个 `Content-Length: 2GB` 就能把内存吃光）。
        # 这里**先看头再决定读不读**，超限直接 413，不读 body。
        if length > _MAX_BODY_BYTES:
            self._error(-32600, f"请求体过大：{length} 字节 > 上限 {_MAX_BODY_BYTES}",
                        status=413)
            return
        body = self.rfile.read(length).decode("utf-8", errors="replace") if length else ""
        if not body.strip():
            self._error(2, "请求体为空（每行一个仓库地址）")
            return
        # 复用 CLI --json 子进程执行克隆（触网/写盘仅此路径，且已显式放行）
        cmd = [sys.executable, "-m", "gcm", "--cli", "--json",
               "--dir", str(server.root)]
        try:
            proc = subprocess.run(cmd, input=body, capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", timeout=1200)
        except Exception as e:  # noqa: BLE001
            self._error(-32603, f"克隆调度失败:{e}", status=500)
            return
        try:
            payload = json.loads(proc.stdout or "{}")
        except ValueError:
            payload = {"error": {"code": -32603, "message": "克隆输出非 JSON",
                                 "redacted": redact(proc.stdout or "")}}
        self._send_json(payload,
                        status=201 if proc.returncode == 0 else 200)


class GcmApiServer(ThreadingHTTPServer):
    """带 root/data_dir/allow_mutate 的线程化 HTTP 服务器。

    G69-14（压测抓出）：`socketserver.TCPServer.request_queue_size` 默认只有 **5**，
    即 `listen()` 的 backlog = 5。50 个并发连接实测出现
    `WinError 10061 由于目标计算机积极拒绝` —— 超出 backlog 的连接被**直接拒绝**
    （客户端看到的是「连不上」，不是「服务忙」，很难自查）。
    本地 agent/脚本批量并发调用时完全可能超过 5，故显式抬高。
    """

    root: Path = Path.cwd()
    data_dir: Path = Path.cwd()
    allow_mutate: bool = False
    event_bus: Any = None             # G71-1：可注入（测试隔离）；None = 进程级单例
    sse_max: int = 16                 # G72：并发 SSE 订阅上限（超限 503 + Retry-After）
    request_queue_size = 128          # 默认 5 太小：并发调用会被内核直接拒绝
    daemon_threads = True             # 线程随进程退出，避免 shutdown 时挂住

    def __init__(self, *args: Any, **kwargs: Any):
        # 必须在 `super().__init__` 之前创建：绑定失败时 socketserver 会在
        # 构造过程中回调 `server_close()`，那时本属性必须已存在。
        self._stop_event = threading.Event()
        self._sse_lock = threading.Lock()
        self._sse_active = 0
        super().__init__(*args, **kwargs)

    def server_close(self) -> None:
        self._stop_event.set()
        super().server_close()


def create_server(bind: str = "127.0.0.1:8765", root: Path | str = Path.cwd(),
                  data_dir: Path | str | None = None,
                  allow_mutate: bool = False,
                  event_bus: Any = None) -> GcmApiServer:
    """创建本地 HTTP 服务器（默认只读）；测试可直接关闭释放端口。

    G66-6 安全加固：本服务**无鉴权**（设计如此：仅面向本机 agent 集成）。
    因此任何非回环绑定都会把「仓库清单 / 报表 / 克隆写操作」暴露给同网段任何人。
    此处强制拒绝非回环 host（不再静默接受 `0.0.0.0`）。
    """
    host, _, port = str(bind).rpartition(":")
    host = host or "127.0.0.1"
    if _looks_like_ipv6(host):
        raise ValueError(
            f"IPv6 绑定暂不支持（{host!r}）：本地 HTTP API 基于 AF_INET 实现。"
            "请用 127.0.0.1 / localhost。")
    if host not in _LOOPBACK_HOSTS:
        raise ValueError(
            f"拒绝绑定非回环地址 {host!r}：本地 HTTP API 无鉴权，"
            f"只允许 {sorted(_LOOPBACK_HOSTS)}（默认 127.0.0.1）。"
            "如需远程访问，请自行用带鉴权的反向代理转发到回环地址。")
    try:
        port_num = int(port or 8765)
    except ValueError as e:
        raise ValueError(f"非法端口 {port!r}（应为 0-65535 的整数）") from e
    # 0 = 让内核分配空闲端口（测试/临时实例用），其余须在合法区间。
    if not (0 <= port_num <= 65535):
        raise ValueError(f"非法端口 {port_num}（应为 0-65535）")
    httpd = GcmApiServer((host, port_num), _ApiHandler)
    httpd.root = Path(root)
    httpd.data_dir = Path(data_dir) if data_dir else Path(root)
    httpd.allow_mutate = bool(allow_mutate)
    httpd.event_bus = event_bus        # None → SSE 使用进程级单例
    return httpd


def serve(bind: str = "127.0.0.1:8765", root: Path | str = Path.cwd(),
          data_dir: Path | str | None = None,
          allow_mutate: bool = False, json_mode: bool = False) -> int:
    """启动本地 API 并阻塞（Ctrl-C 退出）；返回退出码。

    G66-6：非回环绑定 / 非法端口 → 打印错误并返回退出码 2（不再静默起服务）。
    G67-5：同时捕获 `OSError` —— 绑定失败（端口被占、地址不可用等）原本会以
    traceback 崩溃（rc=1），与 docstring 承诺的「返回 2」不符。
    """
    try:
        httpd = create_server(bind, root, data_dir, allow_mutate)
    except (ValueError, OSError) as e:
        print(f"[SERVE] 启动被拒绝：{e}", file=sys.stderr)
        if json_mode:
            import json as _json
            print(_json.dumps({"version": _version(), "ok": False,
                               "error": str(e)}, ensure_ascii=False))
        return 2
    host, _, port = str(bind).rpartition(":")
    if json_mode:
        import json as _json
        print(_json.dumps({"version": _version(), "serving": f"http://{bind}",
                           "readonly": not allow_mutate}, ensure_ascii=False))
    else:
        print(f"[SERVE] 只读本地 API 已启动：http://{host or '127.0.0.1'}:{port or 8765} "
              f"（--allow-mutate 开启写）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(serve())
