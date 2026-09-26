"""G59-7 企业 IM 告警推送：钉钉 / 飞书 / 企微 webhook 模板，复用 G53-2 告警与 G49-7 发送链路。"""

from __future__ import annotations

from typing import Any

from .notify import send_webhook

IM_CHANNELS = ("dingtalk", "feishu", "wecom")


def build_im_payload(channel: str, alerts: list[dict[str, Any]]) -> dict[str, Any]:
    """按渠道构造企业 IM webhook 请求体。"""
    channel = (channel or "").lower().strip()
    lines = [
        f"[{a.get('level', 'info').upper()}] {a.get('title', '')}: {a.get('message', '')}"
        for a in alerts if a.get("title") or a.get("message")
    ]
    text = "Git-clone-Max 告警\n" + "\n".join(lines) if lines else "Git-clone-Max 告警：无内容"
    if channel == "dingtalk":
        return {"msgtype": "text", "text": {"content": text}}
    if channel == "feishu":
        return {"msg_type": "text", "content": {"text": text}}
    # 企微：text 模板（markdown 也兼容）
    return {"msgtype": "text", "text": {"content": text}}


def send_im_alerts(channel: str, url: str, alerts: list[dict[str, Any]],
                   token: str = "", timeout: int = 15) -> bool:
    """发送企业 IM 告警；网络失败返回 False，绝不抛出。"""
    if channel not in IM_CHANNELS or not url:
        return False
    payload = build_im_payload(channel, alerts)
    return send_webhook(url, token, payload, timeout=timeout)


if __name__ == "__main__":
    print("im notify helpers OK")
