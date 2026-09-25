"""G56-4 本地 HTTP API（默认只读）：python -m gcm --cli --serve 127.0.0.1:8765。

端点：
- GET  /version              版本与只读模式声明
- GET  /api/repos            仓库列表（host 过滤，敏感列剔除）
- GET  /api/repos/search     关键字搜索（q 必填）
- GET  /api/status           全局统计概览
- GET  /api/report           报表（format=csv|markdown|json，内存生成，不写盘）
- POST /api/clone            批量克隆（默认 403；仅 --allow-mutate 时执行，审计落盘）

安全边界：
- 只绑定 127.0.0.1；无 CORS 头（跨域 JS 读不到）；
- 默认只读：无任何写端点；POST 需显式 --allow-mutate；
- 输出全部 redact 打码，不泄露 token/host_tokens/ssh_key；
- 每次请求记审计日志（仅 allow_mutate 时落盘到 <root>/.gcm-api-audit.jsonl）。
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .util.redact import redact

_SENSITIVE_COLS = ("token", "host_tokens", "ssh_key", "url")


def _version() -> str:
    """项目版本号（延迟导入避免循环）。"""
    from gcm import __version__
    return __version__


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    """剔除敏感列并打码 URL。"""
    if not isinstance(row, dict):
        return row
    return {k: (redact(str(v)) if k == "url" else v) for k, v in row.items()
            if k not in _SENSITIVE_COLS or k == "url"}


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

    # ------------------------------------------------------------ GET
    def do_GET(self) -> None:
        server: GcmApiServer = self.server  # type: ignore[assignment]
        self._audit("GET", self.path)
        from urllib.parse import parse_qs, urlparse
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        try:
            from .db.repo_db import Database
            db = Database(server.data_dir / "repos.db")
        except Exception as e:  # noqa: BLE001
            self._error(-32603, f"打开数据库失败：{e}", status=500)
            return
        try:
            if parsed.path == "/version":
                self._send_json({"version": _version(),
                                 "readonly": not server.allow_mutate})
            elif parsed.path == "/api/repos":
                host = qs.get("host", [None])[0]
                rows = [_sanitize_row(r) for r in db.list_repos(host=host)]
                self._send_json({"repos": rows, "count": len(rows)})
            elif parsed.path == "/api/repos/search":
                q = (qs.get("q") or [""])[0].strip().lower()
                if not q:
                    self._error(2, "缺少 q 参数")
                    return
                rows = db.list_repos(host=None)
                hits = [r for r in rows if q in " ".join(
                    str(r.get(k) or "") for k in
                    ("owner", "repo", "host", "folder_name", "tags", "note")).lower()]
                self._send_json({"repos": [_sanitize_row(r) for r in hits],
                                 "count": len(hits)})
            elif parsed.path == "/api/status":
                self._send_json({"status": db.stats_overview(),
                                 "readonly": not server.allow_mutate})
            elif parsed.path == "/api/report":
                fmt = (qs.get("format") or ["json"])[0]
                rows = db.list_repos(host=None)
                if fmt == "csv":
                    import io
                    buf = io.StringIO()
                    cols = ("owner", "repo", "host", "status", "action",
                            "message", "commits", "duration_ms", "started_at")
                    buf.write(",".join(cols) + "\r\n")
                    for r in rows:
                        buf.write(",".join(str(r.get(c) or "") for c in cols) + "\r\n")
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
            self._error(-32603, f"处理失败:{e}", status=500)
        finally:
            db.close()

    # ------------------------------------------------------------ POST（写，默认拒绝）
    def do_POST(self) -> None:
        server: GcmApiServer = self.server  # type: ignore[assignment]
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
    """带 root/data_dir/allow_mutate 的线程化 HTTP 服务器。"""

    root: Path = Path.cwd()
    data_dir: Path = Path.cwd()
    allow_mutate: bool = False


def create_server(bind: str = "127.0.0.1:8765", root: Path | str = Path.cwd(),
                  data_dir: Path | str | None = None,
                  allow_mutate: bool = False) -> GcmApiServer:
    """创建本地 HTTP 服务器（默认只读）；测试可直接关闭释放端口。"""
    host, _, port = str(bind).rpartition(":")
    httpd = GcmApiServer((host or "127.0.0.1", int(port or 8765)), _ApiHandler)
    httpd.root = Path(root)
    httpd.data_dir = Path(data_dir) if data_dir else Path(root)
    httpd.allow_mutate = bool(allow_mutate)
    return httpd


def serve(bind: str = "127.0.0.1:8765", root: Path | str = Path.cwd(),
          data_dir: Path | str | None = None,
          allow_mutate: bool = False, json_mode: bool = False) -> int:
    """启动本地 API 并阻塞（Ctrl-C 退出）；返回退出码。"""
    httpd = create_server(bind, root, data_dir, allow_mutate)
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
