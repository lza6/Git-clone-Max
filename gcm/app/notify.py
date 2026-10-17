"""G49-7 / G67-6 完成通知 Webhook（Server酱 / Telegram Bot 兼容 JSON POST）。

红线约定（与规划一致）：
- 仅当设置页「显式开启」且 URL 非空时才发送（调用方先判断）；
- 网络失败绝不抛异常、绝不阻塞主流程（后台线程调用）；
- payload 中失败列表经 util.redact 打码，杜绝 token/URL 凭据外泄；
- 单测全 mock（urllib），真实外部调用需用户显式批准。

G67-6 / G69-18 补全（原实现只有「发一次」）：
1. **幂等去重**：同一 payload 在 `DEDUP_WINDOW_SEC` 内只发一次 ——
   引擎重投/多批次收尾时不会把同一条完成通知刷屏。
2. **审计台账**：每次尝试（含被去重跳过、失败重试）追加一行 JSONL 到
   `<data_dir>/notify.log`；URL 与 token 经 `redact` 打码，可安全查看与转发。
3. **失败重试 1 次**：短暂退避后重试，仍失败则记为失败并返回 False。
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

# 同一 payload 的去重窗口（秒）。10 分钟足以覆盖「同批次重投/多次收尾」。
DEDUP_WINDOW_SEC = 600.0
# 审计文件上限（字节）。超出后滚动为 .1（只保留一代），避免无限增长。
AUDIT_MAX_BYTES = 512 * 1024
# 每个 Webhook 的默认重试次数（不含首次）。
DEFAULT_RETRIES = 1

_dedup_lock = threading.Lock()
# G69-18：审计是**追加写**，Windows 下多线程 open('a') 并非原子 ——
# 40 线程并发实测会丢行/写出空行。用独立写锁串行化（不去抢去重锁，避免互相拖慢）。
_audit_lock = threading.Lock()
_recent: dict[str, float] = {}


def build_payload(ok: int, fail: int, conflict: int = 0, cancelled: int = 0,
                  total: int = 0, failed_repos: list[str] | None = None) -> dict[str, Any]:
    """构造通知摘要（失败列表打码）。"""
    from ..util.redact import redact
    failed = [redact(str(x)) for x in (failed_repos or []) if str(x).strip()]
    return {
        "ok": int(ok),
        "fail": int(fail),
        "conflict": int(conflict),
        "cancelled": int(cancelled),
        "total": int(total),
        "failed_repos": failed,
        "text": f"Git-clone-Max 完成：成功 {ok} · 失败 {fail}"
                + (f" · 冲突 {conflict}" if conflict else "")
                + (f" · 失败列表：{'、'.join(failed)}" if failed else ""),
    }


def _fingerprint(payload: dict[str, Any], url: str = "") -> str:
    """payload + 目标 URL 的稳定指纹（用于幂等去重，不含时间戳）。

    G69-18：**必须把 url 也算进去** —— 否则用户在去重窗口内把 Webhook 换成
    另一个服务、而统计数字恰好相同，第二条会被静默跳过（换服务等于没换）。
    """
    raw = json.dumps({"url": str(url or ""), "payload": payload},
                     ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _redact_url(url: str) -> str:
    """审计里**只保留 scheme://host**，丢弃 path 与 query。

    为什么不能只用 `redact()`：它按**已知参数名**（token/api_key…）打码，
    而 Webhook URL 的凭据常常在**路径**里（Server酱 `.../<KEY>.send`、
    Telegram `/bot<TOKEN>/sendMessage`），或用了自定义参数名（`?k=SECRET`）。
    这类值 redact 认不出来 → 会明文落进审计台账。

    台账的用途是「排查有没有发、发了几次、成不成功」，
    只需要知道**发往哪个服务**，不需要完整 URL。故只留 scheme://host。
    """
    try:
        from urllib.parse import urlsplit
        parts = urlsplit(str(url))
        if parts.scheme and parts.hostname:
            return f"{parts.scheme}://{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    except Exception:
        pass
    return "(invalid-url)"


def _audit(data_dir: str | Path | None, record: dict[str, Any]) -> None:
    """追加一行审计（best-effort：**任何**异常都不得影响主流程）。"""
    if not data_dir:
        return
    try:
        base = Path(data_dir)
        base.mkdir(parents=True, exist_ok=True)
        path = base / "notify.log"
        # 滚动：超限则保留一代 .1
        try:
            if path.exists() and path.stat().st_size > AUDIT_MAX_BYTES:
                path.replace(path.with_suffix(".log.1"))
        except Exception:
            pass
        line = json.dumps(record, ensure_ascii=False)
        with _audit_lock, path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def read_audit(data_dir: str | Path, limit: int = 200) -> list[dict[str, Any]]:
    """读取审计记录（供设置页「查看通知审计」；坏行跳过，不抛）。"""
    out: list[dict[str, Any]] = []
    try:
        path = Path(data_dir) / "notify.log"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-max(1, int(limit)):]:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    except Exception:
        return []
    return out


def send_webhook(url: str, token: str, payload: dict[str, Any],
                 timeout: int = 15) -> bool:
    """POST JSON 到 Webhook；任何异常返回 False（绝不抛出）。"""
    if not url or not str(url).strip().lower().startswith(("http://", "https://")):
        return False
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": "Git-clone-Max/notify"})
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read()
        return True
    except Exception:  # noqa: BLE001
        return False


def send_with_retry(url: str, token: str, payload: dict[str, Any],
                    timeout: int = 15, retries: int = DEFAULT_RETRIES,
                    backoff: float = 1.0) -> tuple[bool, int]:
    """发送，失败重试 `retries` 次（短暂退避）。返回 (是否成功, 尝试次数)。"""
    attempts = 0
    total = max(0, int(retries)) + 1
    while attempts < total:
        attempts += 1
        if send_webhook(url, token, payload, timeout=timeout):
            return True, attempts
        if attempts < total:
            try:
                time.sleep(max(0.0, float(backoff)))
            except Exception:
                pass
    return False, attempts


def maybe_notify(settings, ok: int, fail: int, conflict: int = 0,
                 cancelled: int = 0, total: int = 0,
                 failed_repos: list[str] | None = None,
                 data_dir: str | Path | None = None,
                 retries: int = DEFAULT_RETRIES) -> bool:
    """设置页显式开启 + URL 非空时发送；否则不发送（返回 False）。

    返回 True 表示「已完成通知职责」—— 包括**被幂等去重跳过**的情况
    （那不是错误，而是有意不重复打扰）。

    幂等窗口 `DEDUP_WINDOW_SEC` 是**进程内**的（`_recent` 是模块级字典）：
    应用重启后失效、多实例之间互不共享。这是有意取舍 —— 跨进程去重需要
    额外锁与持久化，而重复通知的代价远小于漏发。
    """
    try:
        enabled = bool(getattr(settings, "notify_webhook_enabled", False))
        url = str(getattr(settings, "notify_webhook_url", "") or "").strip()
        if not enabled or not url:
            return False
        token = str(getattr(settings, "notify_webhook_token", "") or "").strip()
        payload = build_payload(ok, fail, conflict, cancelled, total, failed_repos)
        fp = _fingerprint(payload, url)
        now = time.time()

        # 幂等：同 payload 在窗口内只发一次。
        # G69-18 **压测修复**：原实现是「先查后写」—— 查完到发送完成之间没有占位，
        # 40 个并发同 payload 实测发了 13 次。改为**先占位再发送**：
        # 在锁内一次性完成「查重 + 占位」，发送失败再撤销占位（允许稍后重试）。
        with _dedup_lock:
            last = _recent.get(fp)
            if last is not None and now - last < DEDUP_WINDOW_SEC:
                _audit(data_dir, {
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "event": "dedup-skip", "url": _redact_url(url),
                    "fingerprint": fp[:12], "ok": ok, "fail": fail,
                })
                return True
            _recent[fp] = now          # ← 占位：发送期间并发调用会被去重

        sent, attempts = send_with_retry(url, token, payload, retries=retries)
        _audit(data_dir, {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "sent" if sent else "failed",
            "url": _redact_url(url),
            "fingerprint": fp[:12],
            "attempts": attempts,
            "ok": ok, "fail": fail, "conflict": conflict,
        })
        with _dedup_lock:
            if not sent:
                # 发送失败：撤销自己的占位，让后续调用还能重试
                if _recent.get(fp) == now:
                    _recent.pop(fp, None)
            else:
                # 清理过期条目，防止长期运行无限增长
                for k in [k for k, v in list(_recent.items())
                          if now - v >= DEDUP_WINDOW_SEC]:
                    _recent.pop(k, None)
        return bool(sent)
    except Exception as exc:  # noqa: BLE001
        # 兜底不能静默：写一条**不含凭据**的审计记录（只记异常类型）。
        # 这条规则是被自己踩出来的 —— 上一轮测试里的 NameError 就是这样被吞掉的，
        # 表现为「发送成功但审计记 failed」。
        _audit(data_dir, {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": "error", "error": type(exc).__name__,
        })
        return False


def reset_dedup_for_tests() -> None:
    """清空去重记忆（仅测试用）。"""
    with _dedup_lock:
        _recent.clear()
