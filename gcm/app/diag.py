"""G48-8 网络诊断：串行探测 git/TCP/GitHub API/代理，输出分级报告（绿/黄/红）。"""
from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


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

# ---------------------------------------------------------------- G54-3 环境体检
def probe_data_dir(data_dir=None) -> dict:
    """G54-3：数据目录可写性。"""
    try:
        p = Path(data_dir) if data_dir else Path.cwd()
        p.mkdir(parents=True, exist_ok=True)
        probe = p / ".gcm_diag_probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink(missing_ok=True)
        return {"name": "数据目录", "status": "ok", "detail": f"可写（{p}）"}
    except Exception as e:
        return {"name": "数据目录", "status": "err", "detail": f"不可写：{e}"}


def probe_disk(data_dir=None, min_free_gb: float = 1.0) -> dict:
    """G54-3：磁盘剩余空间。"""
    try:
        p = Path(data_dir) if data_dir else Path.cwd()
        free_gb = shutil.disk_usage(str(p)).free / 1073741824.0
        st = "ok" if free_gb >= min_free_gb else "warn"
        return {"name": "磁盘空间", "status": st, "detail": f"剩余 {free_gb:.1f} GB"}
    except Exception as e:
        return {"name": "磁盘空间", "status": "err", "detail": f"检测失败：{e}"}


def probe_python() -> dict:
    """G54-3：Python/Qt 版本。"""
    try:
        from PyQt6.QtCore import QT_VERSION_STR
        detail = f"Python {sys.version.split()[0]} · Qt {QT_VERSION_STR}"
        return {"name": "运行环境", "status": "ok", "detail": detail}
    except Exception as e:
        return {"name": "运行环境", "status": "warn", "detail": f"Qt 版本不可用：{e}"}


def run_env_diagnostics(data_dir=None) -> list:
    """G54-3：环境体检（数据目录/磁盘/Python-Qt）+ 网络诊断。"""
    reports = [probe_git(), probe_data_dir(data_dir), probe_disk(data_dir), probe_python()]
    reports += run_diagnostics()
    return reports


def copy_paste_report(reports, app_version: str = "") -> str:
    """G54-5：组装可复制的诊断文本（redact 后）。"""
    redactor = str
    try:
        from ..util.redact import redact as _r
        redactor = _r
    except Exception:
        pass
    out = [f"Git-clone-Max 诊断（版本 {app_version or chr(117)+chr(110)+chr(107)+chr(110)+chr(111)+chr(119)+chr(110)}）"]
    for item in reports or []:
        nm = item.get("name")
        st = item.get("status")
        dt = redactor(str(item.get("detail") or ""))
        out.append(f"[{nm}] {st} :: {dt}")
    return chr(10).join(out)
