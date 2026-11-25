#!/usr/bin/env python
"""Rev21 P21-3：agent 接入三段（CLI / MCP stdio / HTTP-SSE）的真实调用证据。

只做可复跑的**真实调用**：dry-run 预览（零副作用）、MCP 只读握手、本地回环 HTTP。
全部使用临时 `GCM_DATA_DIR`，不碰真实数据目录；证据写入
`evidence/rev21-quickstart.json`（命令 + 退出码 + 输出尾部）。

用法：`.venv/Scripts/python.exe scripts/realcalls_quickstart.py`
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = ROOT / "evidence" / "rev21-quickstart.json"
PY = sys.executable


def _env(data_dir: Path, **extra: str) -> dict[str, str]:
    env = {**os.environ, "GCM_DATA_DIR": str(data_dir),
           "PYTHONIOENCODING": "utf-8", **extra}
    return env


def _tail(text: str, limit: int = 500) -> str:
    text = (text or "").strip()
    return text[-limit:]


def _scrubbed(doc: dict) -> str:
    """序列化证据，并把本机绝对路径替换为 `<repo>` 占位。

    证据文件进仓库：`root` / `command` / 应用输出的目标路径都带本机绝对路径，
    公开前必须去个人化。三种形态都要替换：原样、应用输出的全小写、以及被
    内层 JSON 转义过的双反斜杠变体（`stdout_tail` 里是 CLI 的 JSON 文本）。
    """
    raw = str(ROOT)
    needles = [raw, raw.lower(), raw.replace("\\", "\\\\"),
               raw.lower().replace("\\", "\\\\")]

    def scrub(obj):
        if isinstance(obj, str):
            for n in needles:
                obj = obj.replace(n, "<repo>")
            return obj
        if isinstance(obj, list):
            return [scrub(x) for x in obj]
        if isinstance(obj, dict):
            return {k: scrub(v) for k, v in obj.items()}
        return obj

    return json.dumps(scrub(doc), ensure_ascii=False, indent=2)


def segment_cli(data_dir: Path) -> dict:
    cmd = [PY, "-m", "gcm", "--cli", "--json", "--dry-run", "-"]
    proc = subprocess.run(
        cmd, cwd=str(ROOT), env=_env(data_dir),
        input="https://github.com/octocat/Hello-World\n",
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=180)
    ok = False
    doc: dict = {}
    try:
        doc = json.loads(proc.stdout)
        ok = (proc.returncode == 0 and doc.get("mode") == "dry_run"
              and doc.get("ok") is True)
    except ValueError:
        pass
    return {"name": "cli_dry_run", "command": " ".join(cmd),
            "exit_code": proc.returncode, "ok": ok,
            "expected": "exit 0 且 stdout 为单个 JSON（mode=dry_run, ok=true）",
            "stdout_tail": _tail(proc.stdout), "stderr_tail": _tail(proc.stderr)}


def segment_mcp(data_dir: Path) -> dict:
    cmd = [PY, "-m", "gcm.mcp_server"]
    lines = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "rev21-quickstart", "version": "1.0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    payload = "".join(json.dumps(x, ensure_ascii=False) + "\n" for x in lines)
    proc = subprocess.run(
        cmd, cwd=str(ROOT), env=_env(data_dir, GCM_MCP_ENABLE="1"),
        input=payload, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=120)
    tool_count = -1
    init_ok = False
    for raw in (proc.stdout or "").splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            doc = json.loads(raw)
        except ValueError:
            continue
        if doc.get("id") == 1 and "result" in doc:
            init_ok = "serverInfo" in doc["result"] or "protocolVersion" in doc["result"]
        if doc.get("id") == 2 and "result" in doc:
            tool_count = len(doc["result"].get("tools") or [])
    ok = proc.returncode == 0 and init_ok and tool_count >= 5
    return {"name": "mcp_handshake", "command": " ".join(cmd),
            "exit_code": proc.returncode, "ok": ok, "tool_count": tool_count,
            "expected": ("exit 0；initialize 有响应；tools/list 至少 5 个只读工具"
                         "（7 个含 --allow-mutate 的写工具）"),
            "stdout_tail": _tail(proc.stdout), "stderr_tail": _tail(proc.stderr)}


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])
    finally:
        s.close()


def segment_http(data_dir: Path) -> dict:
    port = _free_port()
    bind = f"127.0.0.1:{port}"
    cmd = [PY, "-m", "gcm", "--cli", "--serve", bind]
    proc = subprocess.Popen(cmd, cwd=str(ROOT), env=_env(data_dir),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace")
    version_ok = False
    sse_ok = False
    version_body = ""
    sse_head = ""
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                        f"http://{bind}/version", timeout=2) as resp:
                    version_body = resp.read(2048).decode("utf-8", "replace")
                    version_ok = resp.status == 200 and "version" in version_body
                    break
            except OSError:
                time.sleep(0.2)
        sse_deadline = time.time() + 10
        while time.time() < sse_deadline:
            try:
                with urllib.request.urlopen(
                        f"http://{bind}/api/events", timeout=5) as resp:
                    buf = b""
                    # SSE 无 Content-Length，read(n) 会阻塞等满 n 字节；
                    # read1 只做「有多少拿多少」的读，才能及时拿到首帧。
                    while len(buf) < 256:
                        chunk = resp.read1(64)
                        if not chunk:
                            break
                        buf += chunk
                        if b"\n\n" in buf:
                            break
                    sse_head = buf.decode("utf-8", "replace")
                    sse_ok = "retry:" in sse_head
                    break
            except OSError:
                time.sleep(0.3)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
    ok = version_ok and sse_ok
    return {"name": "http_sse", "command": " ".join(cmd),
            "exit_code": proc.returncode, "ok": ok,
            "expected": "/version 返回 JSON；/api/events 返回 SSE 帧",
            "version_tail": _tail(version_body), "sse_head": _tail(sse_head)}


def main() -> int:
    results = []
    with tempfile.TemporaryDirectory(prefix="gcm-quickstart-") as td:
        base = Path(td)
        for fn in (segment_cli, segment_mcp, segment_http):
            d = base / fn.__name__
            d.mkdir(parents=True, exist_ok=True)
            try:
                results.append(fn(d))
            except Exception as e:  # noqa: BLE001 - 证据脚本：失败也要落盘
                results.append({"name": fn.__name__, "ok": False,
                                "error": f"{type(e).__name__}: {e}"})
    for r in results:
        print(f"[{ 'OK' if r.get('ok') else 'FAIL' }] {r.get('name')}")
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    doc = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "root": "<repo>", "ok": all(r.get("ok") for r in results),
           "segments": results}
    EVIDENCE.write_text(_scrubbed(doc), encoding="utf-8")
    print(f"evidence -> {EVIDENCE.relative_to(ROOT)}")
    return 0 if doc["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
