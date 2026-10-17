# -*- coding: utf-8 -*-
"""G67-6 / G69-18：完成通知 Webhook 的幂等 / 审计台账 / 失败重试。

原实现只有「发一次」：同批次重投会重复打扰、失败无重试、也没有任何台账
可回答「为什么没收到通知」。本文件把这些行为钉住。

约定：网络一律 mock（`send_webhook`），不产生任何真实外部请求。
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app import notify  # noqa: E402


class _Settings:
    def __init__(self, enabled=True, url="https://hook.example/x", token="tok-secret"):
        self.notify_webhook_enabled = enabled
        self.notify_webhook_url = url
        self.notify_webhook_token = token


class TestRetry(unittest.TestCase):
    def setUp(self):
        notify.reset_dedup_for_tests()

    def test_retry_succeeds_on_second_attempt(self):
        calls = {"n": 0}

        def _fake(url, token, payload, timeout=15):
            calls["n"] += 1
            return calls["n"] >= 2

        with mock.patch.object(notify, "send_webhook", side_effect=_fake), \
                mock.patch.object(notify.time, "sleep", lambda s: None):
            ok, attempts = notify.send_with_retry("https://h/x", "", {"a": 1})
        self.assertTrue(ok)
        self.assertEqual(attempts, 2)

    def test_retry_exhausted_returns_false_with_attempt_count(self):
        with mock.patch.object(notify, "send_webhook", return_value=False), \
                mock.patch.object(notify.time, "sleep", lambda s: None):
            ok, attempts = notify.send_with_retry("https://h/x", "", {"a": 1}, retries=1)
        self.assertFalse(ok)
        self.assertEqual(attempts, 2, "retries=1 表示首次 + 1 次重试")

    def test_zero_retries_means_single_attempt(self):
        with mock.patch.object(notify, "send_webhook", return_value=False):
            ok, attempts = notify.send_with_retry("https://h/x", "", {"a": 1}, retries=0)
        self.assertFalse(ok)
        self.assertEqual(attempts, 1)


class TestIdempotencyAndAudit(unittest.TestCase):
    def setUp(self):
        notify.reset_dedup_for_tests()
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_notify_"))

    def test_success_writes_audit_and_redacts_url(self):
        settings = _Settings(url="https://hook.example/x?token=SUPERSECRET")
        with mock.patch.object(notify, "send_webhook", return_value=True), \
                mock.patch.object(notify.time, "sleep", lambda s: None):
            self.assertTrue(notify.maybe_notify(settings, ok=3, fail=1, data_dir=self.tmp))
        raw = (self.tmp / "notify.log").read_text(encoding="utf-8")
        self.assertNotIn("SUPERSECRET", raw, "审计台账不得含明文凭据")
        rec = json.loads(raw.strip().splitlines()[-1])
        self.assertEqual(rec["event"], "sent")
        self.assertEqual(rec["ok"], 3)
        self.assertEqual(rec["fail"], 1)
        self.assertEqual(rec["attempts"], 1)

    def test_same_payload_is_deduplicated_within_window(self):
        settings = _Settings()
        sent = {"n": 0}

        def _fake(*a, **k):
            sent["n"] += 1
            return True

        with mock.patch.object(notify, "send_webhook", side_effect=_fake), \
                mock.patch.object(notify.time, "sleep", lambda s: None):
            self.assertTrue(notify.maybe_notify(settings, ok=1, fail=0, data_dir=self.tmp))
            # 同一 payload 再来一次 → 不应再发
            self.assertTrue(notify.maybe_notify(settings, ok=1, fail=0, data_dir=self.tmp))
        self.assertEqual(sent["n"], 1, "同 payload 在去重窗口内只能发一次")
        events = [json.loads(x)["event"]
                  for x in (self.tmp / "notify.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events, ["sent", "dedup-skip"])

    def test_different_payload_sends_again(self):
        settings = _Settings()
        sent = {"n": 0}

        def _fake(*a, **k):
            sent["n"] += 1
            return True

        with mock.patch.object(notify, "send_webhook", side_effect=_fake), \
                mock.patch.object(notify.time, "sleep", lambda s: None):
            notify.maybe_notify(settings, ok=1, fail=0, data_dir=self.tmp)
            notify.maybe_notify(settings, ok=2, fail=0, data_dir=self.tmp)
        self.assertEqual(sent["n"], 2)

    def test_failure_is_audited_not_raised(self):
        settings = _Settings()
        with mock.patch.object(notify, "send_webhook", return_value=False), \
                mock.patch.object(notify.time, "sleep", lambda s: None):
            self.assertFalse(notify.maybe_notify(settings, ok=0, fail=2, data_dir=self.tmp))
        rec = json.loads((self.tmp / "notify.log").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(rec["event"], "failed")
        self.assertEqual(rec["attempts"], 2, "失败应记录实际尝试次数")

    def test_disabled_or_empty_url_sends_nothing(self):
        with mock.patch.object(notify, "send_webhook") as m:
            self.assertFalse(notify.maybe_notify(_Settings(enabled=False), ok=1, fail=0,
                                                 data_dir=self.tmp))
            self.assertFalse(notify.maybe_notify(_Settings(url=""), ok=1, fail=0,
                                                 data_dir=self.tmp))
        m.assert_not_called()
        self.assertFalse((self.tmp / "notify.log").exists(), "未发送时不该写审计")

    def test_audit_write_failure_never_breaks_notify(self):
        """审计落盘失败（目录不可写）绝不能把通知主流程带崩。"""
        settings = _Settings()
        with mock.patch.object(notify, "send_webhook", return_value=True), \
                mock.patch.object(notify.time, "sleep", lambda s: None), \
                mock.patch.object(notify.Path, "mkdir", side_effect=OSError("no perm")):
            self.assertTrue(notify.maybe_notify(settings, ok=1, fail=0, data_dir=self.tmp))


class TestAuditNeverLeaksCredentials(unittest.TestCase):
    """审计台账只允许记录 `scheme://host` —— URL 本身就是凭据。

    真实世界里 Webhook 的密钥常常在**路径**或**自定义参数名**里：
    Server酱 `.../<KEY>.send`、Telegram `/bot<TOKEN>/sendMessage`、
    或 `?k=SECRET`。`redact()` 按已知参数名打码，认不出这些形态，
    所以这里**整条 path/query 都不落盘**。
    """

    def setUp(self):
        notify.reset_dedup_for_tests()
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_audit_leak_"))

    def _one(self, url):
        settings = _Settings(url=url)
        with mock.patch.object(notify, "send_webhook", return_value=True),                 mock.patch.object(notify.time, "sleep", lambda s: None):
            notify.maybe_notify(settings, ok=1, fail=0, data_dir=self.tmp)

    def test_path_and_query_credentials_never_reach_disk(self):
        cases = [
            "https://hook.example/x?k=SECRET",
            "https://sctapi.ftqq.com/SCT123KEY.send",
            "https://api.telegram.org/bot999:AAHTOKEN/sendMessage",
        ]
        for url in cases:
            with self.subTest(url=url):
                notify.reset_dedup_for_tests()
                self._one(url)
        raw = (self.tmp / "notify.log").read_text(encoding="utf-8")
        for secret in ("SECRET", "SCT123KEY", "AAHTOKEN", "999:"):
            self.assertNotIn(secret, raw, f"审计台账泄漏了凭据片段 {secret!r}")
        # 但仍要保留「发往哪个服务」这个有用信息
        self.assertIn("https://sctapi.ftqq.com", raw)
        self.assertIn("https://api.telegram.org", raw)


class TestConcurrentIdempotency(unittest.TestCase):
    """压测回归：并发同 payload 只能发一次。

    原实现是「先查后写」——查完到发送完成之间没有占位，
    实测 **40 个并发同 payload 发了 13 次**（通知刷屏 + 重复打扰）。
    现在改为锁内「查重 + 占位」一步完成，失败再撤销占位。
    """

    def test_forty_concurrent_identical_calls_send_once(self):
        import threading

        notify.reset_dedup_for_tests()
        tmp = Path(tempfile.mkdtemp(prefix="gcm_notify_conc_"))
        settings = _Settings(url="https://hook.example/<KEY>.send")
        sent: list[int] = []
        lock = threading.Lock()

        def _fake(*a, **k):
            with lock:
                sent.append(1)
            time.sleep(0.01)          # 放大竞态窗口
            return True

        n = 40
        barrier = threading.Barrier(n)

        def _worker(_i):
            barrier.wait()
            notify.maybe_notify(settings, ok=5, fail=1, data_dir=tmp)

        # 注意：**不要** patch `notify.time.sleep` —— 那会连 `_fake` 里的
        # `time.sleep(0.01)` 一起变成 no-op，竞态窗口消失，测试就不再验证竞态了。
        with mock.patch.object(notify, "send_webhook", side_effect=_fake):
            threads = [threading.Thread(target=_worker, args=(i,)) for i in range(n)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()

        self.assertEqual(len(sent), 1, "并发同 payload 只允许真正发送一次")
        events = [json.loads(x)["event"]
                  for x in (tmp / "notify.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events.count("sent"), 1)
        self.assertEqual(events.count("dedup-skip"), n - 1)

    def test_failed_send_releases_reservation_for_retry(self):
        """发送失败必须撤销占位，否则「一次失败 = 整个窗口内再也不发」。"""
        notify.reset_dedup_for_tests()
        tmp = Path(tempfile.mkdtemp(prefix="gcm_notify_rel_"))
        settings = _Settings()
        with mock.patch.object(notify, "send_webhook", return_value=False),                 mock.patch.object(notify.time, "sleep", lambda s: None):
            self.assertFalse(notify.maybe_notify(settings, ok=0, fail=1, data_dir=tmp))
        # 第二次（仍失败）应当**再次真正尝试**，而不是被当成已发送跳过
        with mock.patch.object(notify, "send_webhook", return_value=False) as m,                 mock.patch.object(notify.time, "sleep", lambda s: None):
            notify.maybe_notify(settings, ok=0, fail=1, data_dir=tmp)
        m.assert_called()
        events = [json.loads(x)["event"]
                  for x in (tmp / "notify.log").read_text(encoding="utf-8").splitlines()]
        self.assertEqual(events.count("failed"), 2)


class TestReadAudit(unittest.TestCase):
    def setUp(self):
        notify.reset_dedup_for_tests()
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_audit_"))

    def test_missing_file_returns_empty(self):
        self.assertEqual(notify.read_audit(self.tmp), [])

    def test_malformed_lines_are_skipped(self):
        p = self.tmp / "notify.log"
        p.write_text('{"event":"sent","ok":1}\n'
                     'not-json\n'
                     '{"event":"failed","ok":0}\n', encoding="utf-8")
        rows = notify.read_audit(self.tmp)
        self.assertEqual([r["event"] for r in rows], ["sent", "failed"])

    def test_limit_returns_tail(self):
        p = self.tmp / "notify.log"
        p.write_text("\n".join(json.dumps({"event": f"e{i}"}) for i in range(50)) + "\n",
                     encoding="utf-8")
        rows = notify.read_audit(self.tmp, limit=5)
        self.assertEqual([r["event"] for r in rows], [f"e{i}" for i in range(45, 50)])

    def test_oversized_log_rolls_over(self):
        p = self.tmp / "notify.log"
        p.write_text("x" * (notify.AUDIT_MAX_BYTES + 10), encoding="utf-8")
        settings = _Settings()
        with mock.patch.object(notify, "send_webhook", return_value=True), \
                mock.patch.object(notify.time, "sleep", lambda s: None):
            notify.maybe_notify(settings, ok=1, fail=0, data_dir=self.tmp)
        self.assertTrue((self.tmp / "notify.log.1").exists(), "超限应滚动保留一代")
        self.assertLess((self.tmp / "notify.log").stat().st_size, notify.AUDIT_MAX_BYTES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
