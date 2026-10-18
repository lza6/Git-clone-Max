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

缓存分两层：
- 单仓库负结果缓存 10 分钟（避免同一仓库被重复探测）；
- 前缀级健康状态缓存 120 秒（挂掉的镜像在整批任务里只探一次），
  并记住「上次成功的前缀」供后续仓库优先复用。
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
HEALTH_TTL_SEC = 120.0

# runner(url, timeout) -> (ok, elapsed_ms, error)
Runner = Callable[[str, float], tuple[bool, int, str]]

_cache: dict[tuple[str, float], tuple[float, bool, int, str]] = {}
_cache_lock = threading.Lock()

# 前缀级健康状态（连通性）：批量克隆时避免对已知挂掉的镜像反复探测。
_health: dict[str, tuple[float, bool]] = {}
_health_lock = threading.Lock()

# 进程级「上次成功的镜像」：每个仓库的 GitService 是独立实例，实例字段记不住；
# 放在模块级才能让同一批任务里的后续仓库优先复用已验证可用的镜像。
_preferred: dict[str, str] = {"prefix": ""}
_preferred_lock = threading.Lock()


def _probe_env(extra: Optional[dict[str, str]] = None) -> dict[str, str]:
    env = dict(os.environ)
    if extra:
        # 只接受网络相关变量，绝不把认证头/凭据经 env 带进探测
        for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                    "no_proxy", "NO_PROXY"):
            if extra.get(key):
                env[key] = str(extra[key])
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "echo"
    # 清理父进程继承的 GIT_CONFIG_*（可能带认证头）——镜像探测绝不允许携带凭据。
    for key in [k for k in env if k.startswith("GIT_CONFIG_")]:
        env.pop(key, None)
    env["GIT_CONFIG_COUNT"] = "0"
    # 不强制禁用系统/用户配置：代理与 CA 常在这里，禁用会让镜像在代理环境里全挂。
    # 凭据安全由 `global_auth_header_configured()` 统一拦截。
    return env


_AUTH_CACHE: dict[str, object] = {"ts": 0.0, "value": False}
_AUTH_LOCK = threading.Lock()
_AUTH_TTL = 600.0


def global_auth_header_configured() -> bool:
    """全局/系统 git 配置里是否有 http.*extraHeader（有则禁止一切镜像操作）。

    测速路径也必须用这个守卫：否则用户 `~/.gitconfig` 里的认证头会被发给镜像。
    """
    now = time.time()
    with _AUTH_LOCK:
        if now - float(_AUTH_CACHE["ts"] or 0) < _AUTH_TTL:
            return bool(_AUTH_CACHE["value"])
    found = False
    for scope in ("--global", "--system"):
        if found:
            break
        try:
            proc = subprocess.run(
                ["git", "config", scope, "--get-regexp", r"^http\..*extraheader$"],
                capture_output=True, text=True, timeout=5,
                encoding="utf-8", errors="replace",
            )
            for line in (proc.stdout or "").splitlines():
                parts = line.split(None, 1)
                if len(parts) == 2 and parts[1].strip():
                    found = True
                    break
        except Exception:
            continue
    with _AUTH_LOCK:
        _AUTH_CACHE["ts"] = now
        _AUTH_CACHE["value"] = found
    return found


def reset_auth_cache_for_tests() -> None:
    with _AUTH_LOCK:
        _AUTH_CACHE["ts"] = 0.0
        _AUTH_CACHE["value"] = False


def probe_state(prefix: str):
    """返回前缀健康状态：True=最近可用，False=最近不可用，None=未知。"""
    with _health_lock:
        hit = _health.get(prefix)
    if hit is None:
        return None
    ts, ok = hit
    if time.time() - ts > HEALTH_TTL_SEC:
        return None
    return ok


def probe_health(prefix: str) -> bool:
    """该前缀最近是否可用（未知 = 可用，交给真实探测）。"""
    with _health_lock:
        hit = _health.get(prefix)
    if hit is None:
        return True
    ts, ok = hit
    if time.time() - ts > HEALTH_TTL_SEC:
        return True
    return ok


def mark_probe_health(prefix: str, ok: bool) -> None:
    with _health_lock:
        _health[prefix] = (time.time(), bool(ok))
    if ok:
        note_preferred_prefix(prefix)


def note_preferred_prefix(prefix: str) -> None:
    with _preferred_lock:
        _preferred["prefix"] = str(prefix or "")


def preferred_prefix() -> str:
    with _preferred_lock:
        return _preferred["prefix"]


def default_runner(url: str, timeout: float,
                   env: Optional[dict[str, str]] = None) -> tuple[bool, int, str]:
    """真实探测：`git ls-remote --exit-code <url> HEAD`。

    `env` 用于透传应用内代理（GCM 设置里的 proxy 不在系统环境变量里）。
    """
    started = time.time()
    try:
        proc = subprocess.run(
            ["git", "-c", "credential.helper=", "-c", "http.extraHeader=",
             "ls-remote", "--exit-code", url, "HEAD"],
            capture_output=True, text=True, timeout=max(1.0, float(timeout)),
            encoding="utf-8", errors="replace", env=_probe_env(env),
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
                  use_cache: bool = True,
                  env: Optional[dict[str, str]] = None) -> tuple[bool, int, str]:
    """探测一个 git URL 是否可用；返回 (ok, elapsed_ms, error)。

    `env` 只透传网络相关变量（代理），不会把凭据带进探测。
    """
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
    run = runner or (lambda u, t: default_runner(u, t, env))
    ok, ms, err = run(url, float(timeout))
    if cancelled is not None and cancelled():
        return False, ms, "已取消"
    if use_cache:
        with _cache_lock:
            _cache[key] = (now, bool(ok), int(ms), str(err or ""))
            # 长会话里仓库数可能上千：超过 512 条时丢掉最旧的一半，避免无界增长。
            if len(_cache) > 512:
                oldest = sorted(_cache.items(), key=lambda kv: kv[1][0])[: len(_cache) // 2]
                for k, _v in oldest:
                    _cache.pop(k, None)
    return bool(ok), int(ms), str(err or "")


def rank_mirrors(source_url: str, prefixes, timeout: float = PROBE_TIMEOUT_SEC,
                 runner: Optional[Runner] = None,
                 cancelled: Optional[Callable[[], bool]] = None,
                 include_source: bool = True,
                 env: Optional[dict[str, str]] = None) -> list[dict]:
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
                                    cancelled=cancelled, env=env)
        results.append({"url": url, "ok": ok, "ms": ms, "error": err,
                        "is_source": False})
    results.sort(key=lambda r: (0 if r["ok"] else 1, r["ms"] if r["ok"] else 0))
    if include_source and source_url and (cancelled is None or not cancelled()):
        ok, ms, err = probe_git_url(source_url, timeout, runner=runner,
                                    cancelled=cancelled, env=env)
        results.append({"url": source_url, "ok": ok, "ms": ms, "error": err,
                        "is_source": True})
    return results


def reset_cache_for_tests() -> None:
    with _cache_lock:
        _cache.clear()
    with _health_lock:
        _health.clear()
    with _preferred_lock:
        _preferred["prefix"] = ""
    reset_auth_cache_for_tests()
