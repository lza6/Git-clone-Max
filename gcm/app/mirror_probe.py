"""G58-2 镜像测速：用 `git ls-remote` 探测候选前缀，并做短期缓存。

为什么不用 HTTP HEAD
--------------------
实测（2026-10-18）：`https://ghfast.top` / `https://ghproxy.net` 对 HEAD 返回
403，但 `git ls-remote` 完全正常；反之 `https://ghproxy.com` 的 HEAD 是 200，
git 却因为 302 重定向失败。**唯一可信的判据是 git 自己能不能连上。**

因此探测命令固定为：

    git -c credential.helper= ls-remote --exit-code <url> HEAD

- `credential.helper=` + `GIT_TERMINAL_PROMPT=0` + `GIT_ASKPASS=echo`：绝不弹窗、
  绝不去系统钥匙串取凭据（镜像探测不应携带任何凭据）。
- 退出码 0 = 正常；2 = 远端可达但没有任何 ref（空仓库，克隆仍会成功）。
- 超时/非 0 → 视为不可用。

缓存 10 分钟，避免一次批量任务里每个仓库都重复探测。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable
from typing import Optional

from .mirrors import build_mirror_urls

PROBE_TIMEOUT_SEC = 8.0
CACHE_TTL_SEC = 600.0

# runner(url, timeout) -> (ok, elapsed_ms, error)
Runner = Callable[[str, float], tuple[bool, int, str]]

_cache: dict[tuple[str, float], tuple[float, bool, int, str]] = {}
_cache_lock = threading.Lock()


def _probe_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    # 不读系统/全局 credential 配置；镜像探测不允许带任何凭据
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    return env


def default_runner(url: str, timeout: float) -> tuple[bool, int, str]:
    """真实探测：`git ls-remote --exit-code <url> HEAD`。"""
    started = time.time()
    try:
        proc = subprocess.run(
            ["git", "-c", "credential.helper=", "ls-remote", "--exit-code", url, "HEAD"],
            capture_output=True, text=True, timeout=max(1.0, float(timeout)),
            encoding="utf-8", errors="replace", env=_probe_env(),
        )
        elapsed = int((time.time() - started) * 1000)
        if proc.returncode in (0, 2):
            # 2 = 远端可达但没有匹配 ref（空仓库）
            return True, elapsed, ""
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, elapsed, (tail[-1] if tail else f"rc={proc.returncode}")
    except subprocess.TimeoutExpired:
        return False, int((time.time() - started) * 1000), "探测超时"
    except Exception as exc:  # noqa: BLE001 —— 探测失败一律视为不可用
        return False, int((time.time() - started) * 1000), str(exc)


def probe_git_url(url: str, timeout: float = PROBE_TIMEOUT_SEC,
                  runner: Optional[Runner] = None,
                  cancelled: Optional[Callable[[], bool]] = None,
                  use_cache: bool = True) -> tuple[bool, int, str]:
    """探测一个 git URL 是否可用；返回 (ok, elapsed_ms, error)。"""
    if not url:
        return False, 0, "空 URL"
    if cancelled is not None and cancelled():
        return False, 0, "已取消"
    key = (url, float(timeout))
    now = time.time()
    if use_cache:
        with _cache_lock:
            hit = _cache.get(key)
        if hit is not None and now - hit[0] < CACHE_TTL_SEC:
            return hit[1], hit[2], hit[3]
    run = runner or default_runner
    ok, ms, err = run(url, float(timeout))
    if cancelled is not None and cancelled():
        return False, ms, "已取消"
    if use_cache:
        with _cache_lock:
            _cache[key] = (now, bool(ok), int(ms), str(err or ""))
    return bool(ok), int(ms), str(err or "")


def rank_mirrors(source_url: str, prefixes, timeout: float = PROBE_TIMEOUT_SEC,
                 runner: Optional[Runner] = None,
                 cancelled: Optional[Callable[[], bool]] = None,
                 include_source: bool = True) -> list[dict]:
    """按当前网络给候选镜像排序；返回每个候选的探测结果。

    排序规则：可用的在前（按延迟升序），不可用的在后（保持原顺序）。
    源站（若 `include_source`）也会被测，但标记 `is_source=True`，不参与排序。
    """
    urls = build_mirror_urls(source_url, prefixes)
    results: list[dict] = []
    for url in urls:
        if cancelled is not None and cancelled():
            break
        ok, ms, err = probe_git_url(url, timeout, runner=runner,
                                    cancelled=cancelled)
        results.append({"url": url, "ok": ok, "ms": ms, "error": err,
                        "is_source": False})
    results.sort(key=lambda r: (0 if r["ok"] else 1, r["ms"] if r["ok"] else 0))
    if include_source and source_url and (cancelled is None or not cancelled()):
        ok, ms, err = probe_git_url(source_url, timeout, runner=runner,
                                    cancelled=cancelled)
        results.append({"url": source_url, "ok": ok, "ms": ms, "error": err,
                        "is_source": True})
    return results


def reset_cache_for_tests() -> None:
    with _cache_lock:
        _cache.clear()
