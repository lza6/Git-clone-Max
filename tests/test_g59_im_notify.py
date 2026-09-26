"""G59-7 企业 IM 告警推送测试：钉钉/飞书/企微模板 + AlertScanner 触发推送（全 mock 网络）。"""
from __future__ import annotations

import unittest
from unittest import mock

from gcm.app.alerts import AlertScanner
from gcm.app.im_notify import (IM_CHANNELS, build_im_payload, send_im_alerts)


def _alert(key="k1", level="warning", title="陈旧仓库", message="a/b 30 天未同步"):
    return {"key": key, "kind": "stale", "level": level, "title": title, "message": message}


class TestBuildImPayload(unittest.TestCase):
    def test_dingtalk_template(self):
        p = build_im_payload("dingtalk", [_alert()])
        self.assertEqual(p["msgtype"], "text")
        self.assertIn("陈旧仓库", p["text"]["content"])
        self.assertIn("30 天未同步", p["text"]["content"])

    def test_feishu_template(self):
        p = build_im_payload("feishu", [_alert()])
        self.assertEqual(p["msg_type"], "text")
        self.assertIn("陈旧仓库", p["content"]["text"])

    def test_wecom_template(self):
        p = build_im_payload("wecom", [_alert()])
        self.assertEqual(p["msgtype"], "text")
        self.assertIn("陈旧仓库", p["text"]["content"])

    def test_empty_alerts_fallback_text(self):
        p = build_im_payload("dingtalk", [])
        self.assertIn("无内容", p["text"]["content"])

    def test_unknown_channel_falls_back_wecom(self):
        p = build_im_payload("slack", [_alert()])
        self.assertEqual(p["msgtype"], "text")


class TestSendImAlerts(unittest.TestCase):
    def test_rejects_unknown_channel(self):
        self.assertFalse(send_im_alerts("slack", "https://x", [_alert()]))

    def test_rejects_empty_url(self):
        self.assertFalse(send_im_alerts("dingtalk", "", [_alert()]))

    def test_send_ok(self):
        with mock.patch("gcm.app.im_notify.send_webhook", return_value=True) as sw:
            ok = send_im_alerts("dingtalk", "https://oapi.dingtalk.com/robot/send?access_token=x",
                                [_alert()])
            self.assertTrue(ok)
            sw.assert_called_once()
            payload = sw.call_args.args[2]
            self.assertEqual(payload["msgtype"], "text")

    def test_send_network_failure_returns_false(self):
        with mock.patch("gcm.app.im_notify.send_webhook", return_value=False):
            self.assertFalse(send_im_alerts("feishu", "https://open.feishu.cn/hook/x", [_alert()]))


class TestAlertScannerImPush(unittest.TestCase):
    def test_scanner_sends_im_when_configured(self):
        with mock.patch("gcm.app.im_notify.send_im_alerts") as sim:
            sc = AlertScanner(lambda: [_alert()], now_fn=lambda: 1000.0,
                              im_channel="wecom",
                              im_url="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=x")
            fired = sc.scan_once(1000.0)
            self.assertEqual(len(fired), 1)
            sim.assert_called_once()
            args = sim.call_args.args
            self.assertEqual(args[0], "wecom")
            self.assertEqual(args[2], fired)

    def test_scanner_skips_im_without_url(self):
        with mock.patch("gcm.app.im_notify.send_im_alerts") as sim:
            sc = AlertScanner(lambda: [_alert()], now_fn=lambda: 1000.0,
                              im_channel="dingtalk", im_url="")
            sc.scan_once(1000.0)
            sim.assert_not_called()

    def test_scanner_im_failure_swallowed(self):
        with mock.patch("gcm.app.im_notify.send_im_alerts", side_effect=RuntimeError("net")):
            sc = AlertScanner(lambda: [_alert()], now_fn=lambda: 1000.0,
                              im_channel="feishu",
                              im_url="https://open.feishu.cn/hook/x")
            fired = sc.scan_once(1000.0)
            self.assertEqual(len(fired), 1)  # 推送失败不影响告警返回


if __name__ == "__main__":
    unittest.main()