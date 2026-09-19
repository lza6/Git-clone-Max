"""G49-7 完成通知 Webhook（Server酱 / Telegram Bot 兼容 JSON POST）。

红线约定（与规划一致）：
- 仅当设置页「显式开启」且 URL 非空时才发送（调用方先判断）；
- 网络失败绝不抛异常、绝不阻塞主流程（后台线程调用）；
- payload 中失败列表经 util.redact 打码，杜绝 token/URL 凭据外泄；
- 单测全 mock（urllib），真实外部调用需用户显式批准。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any


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


def maybe_notify(settings, ok: int, fail: int, conflict: int = 0,
                 cancelled: int = 0, total: int = 0,
                 failed_repos: list[str] | None = None) -> bool:
    """设置页显式开启 + URL 非空时发送；否则不发送（返回 False）。"""
    try:
        enabled = bool(getattr(settings, "notify_webhook_enabled", False))
        url = str(getattr(settings, "notify_webhook_url", "") or "").strip()
        if not enabled or not url:
            return False
        token = str(getattr(settings, "notify_webhook_token", "") or "").strip()
        payload = build_payload(ok, fail, conflict, cancelled, total, failed_repos)
        return send_webhook(url, token, payload)
    except Exception:  # noqa: BLE001
        return False
