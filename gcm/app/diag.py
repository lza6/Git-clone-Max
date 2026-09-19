"""G48-8 网络诊断：串行探测 git/TCP/GitHub API/代理，输出分级报告（绿/黄/红）。"""
from __future__ import annotations

import socket
import subprocess
import time
import urllib.request


def probe_git() -> dict:
    try:
        r = subprocess.run(["git", "--version"], capture_output=True, text=True, timeout=8)
        return {"name": "git", "status": "ok" if r.returncode == 0 else "err",
                "detail": (r.stdout or r.stderr or "").strip()[:60]}
    except Exception as e:
        return {"name": "git", "status": "err", "detail": f"未找到 git：{e}"}


def _tcp_ok(host: str, port: int, timeout: float = 4.0) -> tuple[bool, str]:
    try:
        t0 = time.monotonic()
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} 可达（{time.monotonic() - t0:.1f}s）"
    except Exception as e:
        return False, f"{host}:{port} 不可达：{e}"


def probe_github_api() -> dict:
    t0 = time.monotonic()
    try:
        req = urllib.request.Request("https://api.github.com/zen", headers={"User-Agent": "gcm"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            lat = time.monotonic() - t0
            return {"name": "GitHub API", "status": "ok" if lat < 3 else "warn",
                    "detail": f"HTTP {resp.status} · 延迟 {lat:.1f}s"}
    except Exception as e:
        return {"name": "GitHub API", "status": "err", "detail": f"请求失败：{str(e)[:80]}"}


def run_diagnostics(proxy: str = "", host: str = "github.com") -> list[dict]:
    """串行探测（每项自带超时，不会死锁）。"""
    reports = [probe_git()]
    ok, detail = _tcp_ok(host, 443)
    reports.append({"name": f"{host}:443", "status": "ok" if ok else "err", "detail": detail})
    reports.append(probe_github_api())
    if proxy:
        reports.append({"name": "代理", "status": "warn", "detail": f"已配置代理（{proxy[:40]}）"})
    return reports


def grade(reports: list[dict]) -> str:
    """绿=全部 ok；黄=含 warn 无 err；红=含 err。"""
    st = {r.get("status") for r in reports}
    if "err" in st:
        return "红（存在不可用项）"
    if "warn" in st:
        return "黄（部分缓慢/受限）"
    return "绿（全部正常）"
