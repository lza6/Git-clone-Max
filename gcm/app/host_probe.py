"""G58-4 host 探测：HTTPS 可达 / 是否 Git 服务 / git ls-remote 匿名可读 → 建议。"""
from __future__ import annotations

import socket
import subprocess
from typing import Optional


def probe_https(host: str, port: int = 443, timeout: float = 5.0) -> dict:
    """HTTPS 可达性（TCP 握手）。"""
    try:
        t0 = __import__("time").monotonic()
        with socket.create_connection((host, port), timeout=timeout):
            return {"name": "HTTPS", "status": "ok",
                    "detail": f"{host}:{port} 可达（{__import__('time').monotonic() - t0:.1f}s）"}
    except Exception as e:
        return {"name": "HTTPS", "status": "err", "detail": f"{host}:{port} 不可达：{e}"}


def probe_git_service(url: str, timeout: float = 10.0) -> dict:
    """git ls-remote 匿名可读性（判断是否 Git 服务）。"""
    try:
        r = subprocess.run(["git", "ls-remote", url], capture_output=True,
                           text=True, timeout=timeout)
        if r.returncode == 0:
            refs = len([ln for ln in (r.stdout or "").splitlines() if ln.strip()])
            return {"name": "Git 服务", "status": "ok",
                    "detail": f"匿名可读，检测到 {refs} 个 ref"}
        return {"name": "Git 服务", "status": "err",
                "detail": (r.stderr or "").strip()[:80] or "无法匿名读取"}
    except Exception as e:
        return {"name": "Git 服务", "status": "err", "detail": f"探测失败：{e}"}


def suggest_auth_header(git_probe: dict) -> str:
    """根据 git ls-remote 错误内容建议认证头类型（Bearer/PRIVATE-TOKEN/Basic）。"""
    detail = str(git_probe.get("detail") or "").lower()
    if "username" in detail or "password" in detail or "terminal prompts" in detail:
        return "Basic"
    if "private-token" in detail or "glpat" in detail or "gitlab" in detail:
        return "PRIVATE-TOKEN"
    if "authentication" in detail or "authorization" in detail or "bearer" in detail:
        return "Bearer"
    return "Bearer"


def probe_host(host: str, url: Optional[str] = None) -> list[dict]:
    """完整 host 探测：HTTPS → Git 服务 → 头类型建议。"""
    https = probe_https(host)
    reports = [https]
    if https["status"] == "ok":
        git_url = url or f"https://{host}/"
        git = probe_git_service(git_url)
        reports.append(git)
        reports.append({"name": "认证头建议", "status": "info",
                        "detail": suggest_auth_header(git)})
    return reports
