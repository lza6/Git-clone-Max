# -*- coding: utf-8 -*-
"""G49-7 完成通知 Webhook：payload 结构 + redact + mock HTTP + 显式开启才发送。"""
from __future__ import annotations
import json
import unittest
from unittest import mock

from gcm.app.notify import build_payload, maybe_notify, send_webhook


class TestBuildPayload(unittest.TestCase):
    def test_structure_and_redact(self):
        p = build_payload(ok=2, fail=1, conflict=1, cancelled=0, total=4,
                          failed_repos=["https://token@github.com/o/r",
                                        "  ", "plain/repo"])
        self.assertEqual(p["ok"], 2)
        self.assertEqual(p["fail"], 1)
        self.assertEqual(p["conflict"], 1)
        self.assertEqual(p["total"], 4)
        self.assertEqual(p["failed_repos"], ["https://***@github.com/o/r",
                                             "plain/repo"])
        self.assertIn("https://***@", p["text"])
        self.assertNotIn("token@github", json.dumps(p))


class TestSendWebhook(unittest.TestCase):
    def test_invalid_url_no_send(self):
        with mock.patch("gcm.app.notify.urllib.request.Request") as req:
            self.assertFalse(send_webhook("", "tok", {}))
            self.assertFalse(send_webhook("ftp://x", "tok", {}))
        req.assert_not_called()

    def test_success(self):
        with mock.patch("gcm.app.notify.urllib.request.urlopen") as urlopen:
            ok = send_webhook("https://example.com/hook", "tok123",
                              {"ok": 1})
        self.assertTrue(ok)
        req = urlopen.call_args.args[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer tok123")
        self.assertEqual(req.get_header("Content-type"),
                         "application/json; charset=utf-8")

    def test_network_failure_returns_false(self):
        with mock.patch("gcm.app.notify.urllib.request.urlopen",
                        side_effect=Exception("timeout")):
            self.assertFalse(send_webhook("https://example.com/hook", "", {}))


class TestMaybeNotify(unittest.TestCase):
    def test_disabled_does_not_send(self):
        s = mock.Mock()
        s.notify_webhook_enabled = False
        s.notify_webhook_url = "https://example.com/hook"
        with mock.patch("gcm.app.notify.send_webhook") as send:
            self.assertFalse(maybe_notify(s, 1, 0))
        send.assert_not_called()

    def test_enabled_but_empty_url_does_not_send(self):
        s = mock.Mock()
        s.notify_webhook_enabled = True
        s.notify_webhook_url = ""
        with mock.patch("gcm.app.notify.send_webhook") as send:
            self.assertFalse(maybe_notify(s, 1, 0))
        send.assert_not_called()

    def test_enabled_sends(self):
        s = mock.Mock()
        s.notify_webhook_enabled = True
        s.notify_webhook_url = "https://example.com/hook"
        s.notify_webhook_token = "tok"
        with mock.patch("gcm.app.notify.send_webhook", return_value=True) as send:
            self.assertTrue(maybe_notify(s, 1, 0, total=1))
        send.assert_called_once()
        payload = send.call_args.args[2]
        self.assertEqual(payload["ok"], 1)
        self.assertEqual(payload["fail"], 0)
