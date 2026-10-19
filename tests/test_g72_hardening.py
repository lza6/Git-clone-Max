"""G72 加固测试：日志落盘打码 / 原子写重试 / 诊断包并发 / 重试抖动 / SSE 续传与背压。

来源：独立调研 agent（WebFetch 规范与厂商文档）+ 代码静态确认，见
`reports/change-report-2026-10-19-g71.html` 与 `workflow_status.md` §19。
"""
from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gcm.app.event_bus import EventBus, EventType  # noqa: E402
from gcm.app.retry_queue import RetryQueue  # noqa: E402
from gcm.models import RepoSpec  # noqa: E402
from gcm.util.atomic_io import (  # noqa: E402
    replace_with_retry,
    write_text_atomic,
)
from gcm.util.redact import redact  # noqa: E402


def _spec(owner="o", repo="r") -> RepoSpec:
    return RepoSpec(owner=owner, repo=repo,
                    url_https=f"https://github.com/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


class TestLogRedaction(unittest.TestCase):
    def setUp(self):
        from gcm.app import applog
        applog.reset_for_tests()
        self.applog = applog

    def tearDown(self):
        self.applog.reset_for_tests()

    def test_credentials_are_masked_on_disk(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "app.log"
            self.applog.info(
                "clone https://user:pass@github.com/a/b "
                "Authorization: Bearer supersecret "
                "PRIVATE-TOKEN: abc123 "
                "ghp_" + "A" * 30, log)
            self.applog.info("普通日志 should stay readable", log)
            text = log.read_text(encoding="utf-8")
            # Windows 上 RotatingFileHandler 持有句柄，必须在临时目录清理前关闭
            self.applog.reset_for_tests()
        self.assertNotIn("user:pass", text)
        self.assertNotIn("supersecret", text)
        self.assertNotIn("abc123", text)
        self.assertNotIn("ghp_" + "A" * 30, text)
        self.assertIn("普通日志 should stay readable", text)

    def test_plain_log_is_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "app.log"
            self.applog.warn("disk usage 42%", log)
            text = log.read_text(encoding="utf-8")
            self.applog.reset_for_tests()
        self.assertIn("disk usage 42%", text)


class TestRedactG72(unittest.TestCase):
    def test_windows_path_variants(self):
        for text in (
            r"C:\Users\Alice\AppData\Roaming",
            "C:/Users/Alice/AppData/Roaming",
            r"\\fileserver\users\Bob\docs",
            r"C:\Documents and Settings\Carol\Desktop",
        ):
            out = redact(text, userprofile=True)
            for name in ("Alice", "Bob", "Carol"):
                self.assertNotIn(name, out, (text, out))

    def test_high_confidence_tokens(self):
        cases = [
            "ghp_" + "A" * 30,
            "github_pat_" + "B" * 30,
            "glpat-" + "C" * 25,
            "xoxb-" + "1" * 12,
            "AKIA" + "D" * 16,
            "AIza" + "E" * 35,
        ]
        for token in cases:
            self.assertNotIn(token, redact(f"token={token}"), token)

    def test_commit_sha_is_not_masked(self):
        sha = "deadbeef" * 5
        self.assertEqual(redact(sha), sha)


class TestAtomicHardening(unittest.TestCase):
    def test_replace_retries_transient_permission_error(self):
        real_replace = os.replace
        calls = {"n": 0}

        def flaky(src, dst):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise PermissionError("busy")
            return real_replace(src, dst)

        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "x.json"
            with mock.patch("gcm.util.atomic_io.os.replace", side_effect=flaky):
                write_text_atomic(target, '{"ok": 1}', retries=4)
            self.assertEqual(target.read_text(encoding="utf-8"), '{"ok": 1}')
            self.assertEqual(calls["n"], 3)
            self.assertEqual([p.name for p in Path(td).iterdir()
                              if p.name.endswith(".tmp")], [])

    def test_non_transient_error_cleans_tmp(self):
        with tempfile.TemporaryDirectory() as td:
            target = Path(td) / "x.json"
            with mock.patch("gcm.util.atomic_io.os.replace",
                            side_effect=OSError(13, "denied")), self.assertRaises(OSError):
                write_text_atomic(target, "x", retries=3)
            self.assertFalse(target.exists())
            self.assertEqual([p.name for p in Path(td).iterdir()
                              if p.name.endswith(".tmp")], [])

    def test_replace_with_retry_non_transient_raises(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "a.tmp"
            dst = Path(td) / "a"
            src.write_text("x", encoding="utf-8")
            with mock.patch("gcm.util.atomic_io.os.replace",
                            side_effect=OSError(13, "denied")), self.assertRaises(OSError):
                replace_with_retry(src, dst, retries=2)
            self.assertFalse(src.exists())


class TestDiagHardening(unittest.TestCase):
    def test_concurrent_exports_to_same_path(self):
        from gcm.app.diag_bundle import build_diag_bundle
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            data.mkdir()
            (data / "app.log").write_text("hello", encoding="utf-8")
            out = Path(td) / "d.zip"
            errors: list[Exception] = []

            def worker() -> None:
                try:
                    build_diag_bundle(out, data_dir=data, report_text="r",
                                      event_bus=EventBus())
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            ts = [threading.Thread(target=worker) for _ in range(4)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(30)
            self.assertEqual(errors, [])
            with zipfile.ZipFile(out) as zf:
                self.assertIn("manifest.json", zf.namelist())
            self.assertEqual([p.name for p in Path(td).iterdir()
                              if p.name.endswith(".tmp")], [])

    def test_report_truncation_and_manifest_fields(self):
        from gcm.app.diag_bundle import build_diag_bundle
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            data.mkdir()
            big = "x" * (2 * 1024 * 1024 + 10)
            out = build_diag_bundle(Path(td) / "d.zip", data_dir=data,
                                    report_text=big, event_bus=EventBus())
            with zipfile.ZipFile(out) as zf:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                report = zf.read("report.txt").decode("utf-8")
            self.assertTrue(manifest["report_truncated"])
            self.assertIn("timezone", manifest)
            self.assertEqual(manifest["redaction_version"], "2")
            self.assertIn("[TRUNCATED", report)
            self.assertLessEqual(len(report.encode("utf-8")), 2 * 1024 * 1024 + 80)


class TestRetryJitter(unittest.TestCase):
    def test_jitter_is_injectable(self):
        q = RetryQueue(path=None, enabled=True, now=lambda: 1000.0,
                       jitter=lambda d: d / 2)
        q.record_failure(_spec(), "Connection timed out")
        self.assertEqual(q.snapshot()[0]["next_retry_at"], 1015.0)

    def test_due_tasks_limit(self):
        q = RetryQueue(path=None, enabled=True, now=lambda: 1000.0,
                       jitter=lambda d: 0)
        for i in range(5):
            q.record_failure(_spec(f"o{i}", "r"), "Connection timed out")
        self.assertEqual(len(q.due_tasks(now=9999, limit=2)), 2)
        self.assertEqual(len(q.due_tasks(now=9999)), 5)


class TestEventBusG72(unittest.TestCase):
    def test_since_and_oldest(self):
        bus = EventBus()
        e1 = bus.publish("a", {"n": 1})
        e2 = bus.publish("b", {"n": 2})
        e3 = bus.publish("c", {"n": 3})
        self.assertEqual([e.seq for e in bus.since(e1.seq)], [e2.seq, e3.seq])
        self.assertEqual(bus.oldest_seq(), e1.seq)
        self.assertEqual(bus.since(e3.seq), [])

    def test_subscriber_metrics(self):
        bus = EventBus(maxlen=10, max_queue=1)
        sub = bus.subscribe(label="slow")
        for i in range(5):
            bus.publish("a", {"i": i})
        detail = bus.stats()["subscribers_detail"]
        self.assertEqual(len(detail), 1)
        self.assertEqual(detail[0]["label"], "slow")
        self.assertGreaterEqual(detail[0]["dropped"], 1)
        self.assertLessEqual(detail[0]["qsize"], 1)
        sub.close()


class _Sse:
    def __init__(self, tmp: Path):
        from gcm.http_api import create_server
        self.bus = EventBus()
        self.httpd = create_server("127.0.0.1:0", root=tmp, data_dir=tmp,
                                   event_bus=self.bus)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(5.0)


class TestSseG72(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_g72_sse_"))
        self.h = _Sse(self.tmp)
        self.addCleanup(self.h.close)

    def test_last_event_id_resumes_exactly(self):
        e1 = self.h.bus.publish(EventType.TASK_RESULT, {"key": "a/r", "status": "success"})
        self.h.bus.publish(EventType.TASK_ERROR, {"key": "a/r", "title": "boom"})
        self.assertIsNotNone(e1)
        conn = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=5)
        try:
            conn.request("GET", "/api/events?heartbeat=1",
                         headers={"Last-Event-ID": str(e1.seq)})
            resp = conn.getresponse()
            text = b""
            deadline = time.time() + 5
            while time.time() < deadline:
                line = resp.readline()
                if not line:
                    break
                text += line
                if b"task_error" in text and line.strip() == b"":
                    break
        finally:
            conn.close()
        self.assertIn(b"task_error", text)
        self.assertNotIn(b"task_result", text)

    def test_sse_concurrency_limit_returns_503(self):
        self.h.httpd.sse_max = 1
        first = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=5)
        try:
            first.request("GET", "/api/events?heartbeat=5")
            r1 = first.getresponse()
            self.assertEqual(r1.status, 200)
            second = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=5)
            try:
                second.request("GET", "/api/events?heartbeat=5")
                r2 = second.getresponse()
                self.assertEqual(r2.status, 503)
                self.assertEqual(r2.getheader("Retry-After"), "5")
                r2.read()
            finally:
                second.close()
        finally:
            first.close()


if __name__ == "__main__":
    unittest.main()
