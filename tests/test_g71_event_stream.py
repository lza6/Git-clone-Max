"""G71 事件流 / 错误模型 / 原子写 / 诊断包 / 重试可见性测试。

覆盖这一轮新增的四条接缝：
1. 引擎 → EventBus（发布/回放/过滤/打码/丢弃计数）
2. EventBus → HTTP SSE（`/api/events` 长连接 + `/api/events/snapshot`）
3. 错误分类 → 重试队列（可重试 vs 永久失败）
4. 原子写 → progress/history/diag bundle（唯一 tmp + 无残留）
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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gcm.app.errors import (  # noqa: E402
    CAT_AUTH,
    CAT_LOCAL,
    CAT_NETWORK,
    CAT_NOT_FOUND,
    CAT_PLATFORM,
    classify_error,
)
from gcm.app.event_bus import EventBus, EventType  # noqa: E402
from gcm.app.retry_queue import RetryQueue  # noqa: E402
from gcm.models import RepoSpec  # noqa: E402
from gcm.util.atomic_io import write_json_atomic, write_text_atomic  # noqa: E402


def _spec(owner="o", repo="r") -> RepoSpec:
    return RepoSpec(owner=owner, repo=repo,
                    url_https=f"https://github.com/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


class TestErrorModel(unittest.TestCase):
    def test_network_is_retryable(self):
        info = classify_error("fatal: unable to access ... Connection timed out")
        self.assertEqual(info.category, CAT_NETWORK)
        self.assertTrue(info.retryable)
        self.assertTrue(info.title and info.cause and info.action)

    def test_rate_limit_429_is_retryable(self):
        info = classify_error("HTTP 429 Too Many Requests")
        self.assertEqual(info.category, CAT_NETWORK)
        self.assertTrue(info.retryable)
        self.assertEqual(info.http_status, 429)

    def test_auth_403_is_not_retryable(self):
        info = classify_error("remote: HTTP 403 Forbidden")
        self.assertEqual(info.category, CAT_AUTH)
        self.assertFalse(info.retryable)

    def test_not_found_404_is_not_retryable(self):
        info = classify_error("remote: Repository not found")
        self.assertEqual(info.category, CAT_NOT_FOUND)
        self.assertFalse(info.retryable)

    def test_disk_full_is_not_retryable(self):
        info = classify_error("fatal: No space left on device")
        self.assertEqual(info.category, CAT_LOCAL)
        self.assertFalse(info.retryable)

    def test_platform_limit_is_not_retryable(self):
        info = classify_error("error: invalid path 'a:b'")
        self.assertEqual(info.category, CAT_PLATFORM)
        self.assertFalse(info.retryable)

    def test_retry_override(self):
        self.assertFalse(classify_error("Connection timed out", retry=False).retryable)
        self.assertTrue(classify_error("Authentication failed", retry=True).retryable)

    def test_numeric_substrings_are_not_status_codes(self):
        """审查 S6：端口号/提交号里的 403/404 不能被当成 HTTP 状态码。"""
        self.assertEqual(classify_error("listen failed on 127.0.0.1:4031").category,
                         "other")
        self.assertEqual(classify_error("commit deadbeef404 not applied").category,
                         "other")

    def test_chinese_network_failure_is_retryable(self):
        """`service._classify_failure` 的中文网络文案必须被分类器识别为可重试。"""
        from gcm.git.service import _classify_failure
        msg, detail = _classify_failure(
            "fatal: unable to access 'https://github.com/a/b': Failed to connect")
        self.assertIn("网络", msg)
        info = classify_error(msg, detail)
        self.assertEqual(info.category, CAT_NETWORK)
        self.assertTrue(info.retryable)

    def test_display_lines_three_parts(self):
        lines = classify_error("Connection timed out").display_lines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("发生了什么："))
        self.assertTrue(lines[1].startswith("为什么："))
        self.assertTrue(lines[2].startswith("怎么办："))


class TestEventBus(unittest.TestCase):
    def test_publish_subscribe_and_order(self):
        bus = EventBus()
        with bus.subscribe() as sub:
            bus.publish("a", {"n": 1})
            bus.publish("b", {"n": 2})
            events = sub.wait(1.0)
        self.assertEqual([e.type for e in events], ["a", "b"])
        self.assertLess(events[0].seq, events[1].seq)

    def test_replay_and_filter(self):
        bus = EventBus()
        bus.publish("a", {"n": 1})
        bus.publish("b", {"n": 2})
        with bus.subscribe(replay=2, types=["b"]) as sub:
            events = sub.wait(1.0)
        self.assertEqual([e.type for e in events], ["b"])   # 回放也遵守类型过滤

    def test_redaction_at_publish_boundary(self):
        bus = EventBus()
        with bus.subscribe() as sub:
            bus.publish("x", {
                "url": "https://user:pass@github.com/a/b",
                "message": "Authorization: Bearer supersecret",
                "nested": {"token": "https://token@host/x"},
            })
            ev = sub.wait(1.0)[0]
        self.assertNotIn("user:pass", json.dumps(ev.data, ensure_ascii=False))
        self.assertNotIn("supersecret", json.dumps(ev.data, ensure_ascii=False))
        self.assertNotIn("token@host", json.dumps(ev.data, ensure_ascii=False))

    def test_snapshot_and_stats(self):
        bus = EventBus()
        bus.publish("a")
        bus.publish("b")
        snap = bus.snapshot(limit=1)
        self.assertEqual([e.type for e in snap], ["b"])
        st = bus.stats()
        self.assertEqual(st["published"], 2)
        self.assertEqual(st["buffered"], 2)

    def test_slow_subscriber_drops_without_blocking(self):
        bus = EventBus(maxlen=10, max_queue=2)
        sub = bus.subscribe()
        for i in range(10):
            bus.publish("a", {"i": i})
        self.assertGreaterEqual(bus.stats()["dropped"], 1)
        # 慢订阅者仍能拿到「最新的」事件，而不是被永久阻塞
        events = sub.wait(0.1)
        self.assertTrue(events)
        self.assertEqual(events[-1].data["i"], 9)
        sub.close()

    def test_close_wakes_waiters(self):
        bus = EventBus()
        sub = bus.subscribe()
        threading.Timer(0.05, bus.close).start()
        self.assertEqual(sub.wait(1.0), [])

    def test_progress_does_not_evict_errors(self):
        """审查 S1：进度事件单独缓冲，不能把 500 条黑匣子历史冲光。"""
        bus = EventBus(maxlen=5, progress_maxlen=3)
        bus.publish(EventType.TASK_ERROR, {"title": "boom"})
        for i in range(50):
            bus.publish(EventType.TASK_PROGRESS, {"percent": i})
        snap = bus.snapshot(limit=10)
        self.assertEqual([e.type for e in snap], [EventType.TASK_ERROR])
        prog = bus.snapshot(limit=10, types=[EventType.TASK_PROGRESS])
        self.assertEqual(len(prog), 3)
        self.assertEqual(prog[-1].data["percent"], 49)
        st = bus.stats()
        self.assertEqual(st["buffered_events"], 1)
        self.assertEqual(st["buffered_progress"], 3)

    def test_sse_frame_format(self):
        bus = EventBus()
        with bus.subscribe() as sub:
            bus.publish("task_result", {"status": "success"})
            frame = sub.wait(1.0)[0].sse_frame().decode("utf-8")
        self.assertIn("event: task_result\n", frame)
        self.assertIn("data: {", frame)
        self.assertTrue(frame.endswith("\n\n"))
        self.assertEqual(frame.count("data: "), 1)      # data 必须是单行


class TestAtomicWrites(unittest.TestCase):
    def test_write_text_atomic_leaves_no_tmp(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            write_text_atomic(p, '{"a": 1}')
            self.assertEqual(p.read_text(encoding="utf-8"), '{"a": 1}')
            leftovers = [q.name for q in Path(td).iterdir() if q.name.endswith(".tmp")]
            self.assertEqual(leftovers, [])

    def test_concurrent_json_writes_stay_valid(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "x.json"
            errors: list[Exception] = []

            def worker(n: int) -> None:
                try:
                    for i in range(30):
                        write_json_atomic(p, {"n": n, "i": i})
                except Exception as e:  # noqa: BLE001
                    errors.append(e)

            ts = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
            for t in ts:
                t.start()
            for t in ts:
                t.join(15)
            self.assertEqual(errors, [])
            payload = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {"n", "i"})
            self.assertEqual([q.name for q in Path(td).iterdir()
                              if q.name.endswith(".tmp")], [])

    def test_progress_mark_finished_loses_no_updates(self):
        from gcm.db.progress_file import load_progress, mark_finished
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "progress.json"
            specs = [_spec(f"o{n}", "r") for n in range(8)]

            def worker(sp: RepoSpec) -> None:
                mark_finished(p, sp, {"status": "success", "message": "ok"})

            ts = [threading.Thread(target=worker, args=(s,)) for s in specs]
            for t in ts:
                t.start()
            for t in ts:
                t.join(15)
            data = load_progress(p)
            keys = {row["key"] for row in data["finished"]}
            self.assertEqual(keys, {f"o{n}/r" for n in range(8)})


class TestRetryQueueVisibility(unittest.TestCase):
    def test_non_retryable_removes_stale_entry(self):
        """审查 R1：先网络失败入队，重投变 404 时必须摘除，不能每 15s 无限重投。"""
        q = RetryQueue(path=None, enabled=True, now=lambda: 1000.0)
        self.assertTrue(q.record_failure(_spec(), "Connection timed out"))
        self.assertEqual(q.count(), 1)
        out = q.record_failure_ex(_spec(), "remote: Repository not found")
        self.assertFalse(out["queued"])
        self.assertEqual(out["reason"], "non_retryable")
        self.assertTrue(out.get("removed"))
        self.assertEqual(q.count(), 0)
        self.assertEqual(q.due_tasks(now=99999), [])

    def test_retryable_queues_and_non_retryable_does_not(self):
        q = RetryQueue(path=None, enabled=True)
        ok = q.record_failure_ex(_spec("a", "r"), "Connection timed out")
        self.assertTrue(ok["queued"])
        self.assertEqual(ok["category"], CAT_NETWORK)
        bad = q.record_failure_ex(_spec("b", "r"), "Authentication failed")
        self.assertFalse(bad["queued"])
        self.assertEqual(bad["reason"], "non_retryable")
        self.assertEqual(q.count(), 1)

    def test_exhausted_is_reported(self):
        q = RetryQueue(path=None, enabled=True, max_retries=0)
        out = q.record_failure_ex(_spec(), "Connection timed out")
        self.assertFalse(out["queued"])
        self.assertEqual(out["reason"], "exhausted")

    def test_retry_now_remove_and_stats(self):
        q = RetryQueue(path=None, enabled=True, now=lambda: 1000.0)
        q.record_failure(_spec("a", "r"), "Connection timed out")
        snap = q.snapshot()
        self.assertEqual(snap[0]["state"], "queued")
        self.assertEqual(snap[0]["next_retry_at"], 1030.0)
        self.assertTrue(q.retry_now("a/r"))
        self.assertEqual(q.snapshot()[0]["next_retry_at"], 0.0)
        st = q.stats()
        self.assertEqual(st["count"], 1)
        self.assertEqual(st["due"], 1)
        self.assertEqual(st["by_category"][CAT_NETWORK], 1)
        self.assertTrue(q.remove("a/r"))
        self.assertFalse(q.remove("a/r"))
        self.assertEqual(q.count(), 0)

    def test_retry_all_now(self):
        q = RetryQueue(path=None, enabled=True)
        q.record_failure(_spec("a", "r"), "Connection timed out")
        q.record_failure(_spec("b", "r"), "HTTP 429 Too Many Requests")
        self.assertEqual(q.retry_all_now(), 2)
        self.assertEqual(q.stats()["due"], 2)

    def test_link_error_is_not_retried(self):
        """远程仓库不存在（404）不能被自动重投 —— 原实现只看 networkish，会漏。"""
        q = RetryQueue(path=None, enabled=True)
        self.assertFalse(q.record_failure(_spec(), "remote: Repository not found"))


class _SseHarness:
    """起一个注入独立 EventBus 的本地 API server。"""

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


class TestSseEndpoint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_g71_sse_"))
        self.h = _SseHarness(self.tmp)
        self.addCleanup(self.h.close)

    def test_snapshot_endpoint(self):
        self.h.bus.publish(EventType.TASK_RESULT, {"key": "a/r", "status": "success"})
        conn = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=5)
        try:
            conn.request("GET", "/api/events/snapshot?limit=10")
            resp = conn.getresponse()
            payload = json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()
        self.assertEqual(resp.status, 200)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["events"][0]["type"], EventType.TASK_RESULT)
        self.assertIn("stats", payload)

    def test_sse_streams_published_event(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=5)
        try:
            conn.request("GET", "/api/events?heartbeat=1")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200)
            self.assertIn("text/event-stream", resp.getheader("Content-Type", ""))
            # 首行是 EventSource 重连间隔注释，第二帧是 connected 注释
            self.assertEqual(resp.readline().strip(), b"retry: 3000")
            self.assertEqual(resp.readline().strip(), b"")
            self.assertEqual(resp.readline().strip(), b": connected")
            # 连接建立后再发布 → 必须能读到 event/data/id 帧
            self.h.bus.publish(EventType.TASK_RESULT,
                               {"key": "a/r", "status": "success"})
            lines: list[bytes] = []
            deadline = time.time() + 5
            while time.time() < deadline:
                line = resp.readline()
                if not line:
                    break
                lines.append(line)
                if line.strip() == b"" and any(x.startswith(b"data:") for x in lines):
                    break
            text = b"".join(lines).decode("utf-8")
        finally:
            conn.close()
        self.assertIn("event: task_result", text)
        self.assertIn('"key":"a/r"', text)
        self.assertRegex(text, r"id: \d+")

    def test_sse_replay(self):
        self.h.bus.publish(EventType.SYSTEM, {"message": "before-connect"})
        conn = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=5)
        try:
            conn.request("GET", "/api/events?replay=5&heartbeat=1")
            resp = conn.getresponse()
            data = b""
            deadline = time.time() + 5
            while time.time() < deadline:
                line = resp.readline()
                if not line:
                    break
                data += line
                if b"before-connect" in data:
                    break
        finally:
            conn.close()
        self.assertIn(b"before-connect", data)

    def test_sse_types_filter(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=5)
        try:
            conn.request("GET", "/api/events?types=task_error&heartbeat=1")
            resp = conn.getresponse()
            resp.readline()   # retry
            resp.readline()   # blank
            resp.readline()   # : connected
            self.h.bus.publish(EventType.TASK_RESULT, {"key": "a/r", "status": "success"})
            self.h.bus.publish(EventType.TASK_ERROR, {"key": "a/r", "title": "boom"})
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
        self.assertIn(b"event: task_error", text)
        self.assertNotIn(b"event: task_result", text)

    def test_sse_heartbeat_ping(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=5)
        try:
            conn.request("GET", "/api/events?heartbeat=1")
            resp = conn.getresponse()
            got = b""
            deadline = time.time() + 4
            while time.time() < deadline:
                line = resp.readline()
                if not line:
                    break
                got += line
                if b": ping" in got:
                    break
        finally:
            conn.close()
        self.assertIn(b": ping", got)

    def test_sse_subscription_cleaned_on_disconnect(self):
        """真实 socket 断开后订阅必须被清理（不依赖 http.client 缓冲）。"""
        import socket
        s = socket.create_connection(("127.0.0.1", self.h.port), timeout=5)
        try:
            s.sendall(b"GET /api/events?heartbeat=5 HTTP/1.0\r\n"
                      b"Host: 127.0.0.1\r\n\r\n")
            s.recv(1024)
            self.assertGreaterEqual(self.h.bus.stats()["subscribers"], 1)
        finally:
            s.close()
        deadline = time.time() + 4
        while time.time() < deadline and self.h.bus.stats()["subscribers"] > 0:
            time.sleep(0.05)
        self.assertEqual(self.h.bus.stats()["subscribers"], 0)

    def test_events_do_not_open_database(self):
        """事件端点不得因数据库不可用而失败（长连接不该占 SQLite 连接）。"""
        from gcm.http_api import create_server
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing_dir"
            bus = EventBus()
            httpd = create_server("127.0.0.1:0", root=Path(td),
                                  data_dir=missing, event_bus=bus)
            port = httpd.server_address[1]
            th = threading.Thread(target=httpd.serve_forever, daemon=True)
            th.start()
            try:
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    conn.request("GET", "/api/events/snapshot?limit=1")
                    resp = conn.getresponse()
                    self.assertEqual(resp.status, 200)
                    resp.read()
                finally:
                    conn.close()
            finally:
                httpd.shutdown()
                httpd.server_close()
                th.join(5.0)

    def test_bad_params_are_400(self):
        for path in ("/api/events?replay=-1", "/api/events?heartbeat=999",
                     "/api/events/snapshot?limit=0"):
            conn = http.client.HTTPConnection("127.0.0.1", self.h.port, timeout=5)
            try:
                conn.request("GET", path)
                resp = conn.getresponse()
                self.assertEqual(resp.status, 400, path)
                resp.read()
            finally:
                conn.close()


class TestDiagBundle(unittest.TestCase):
    def test_bundle_contents_and_redaction(self):
        from gcm.app.diag_bundle import build_diag_bundle
        with tempfile.TemporaryDirectory() as td:
            data = Path(td) / "data"
            data.mkdir()
            (data / "app.log").write_text(
                "Authorization: Bearer supersecret\nhttps://user:pass@github.com/a/b\n",
                encoding="utf-8")
            bus = EventBus()
            bus.publish(EventType.TASK_ERROR, {
                "url": "https://token@host/x",
                "message": "PRIVATE-TOKEN: abc123",
            })
            out = build_diag_bundle(Path(td) / "d.zip", data_dir=data,
                                    report_text=(
                                        "报告 token=xyz\n"
                                        r"数据目录：C:\Users\Administrator\AppData\Local\gcm"),
                                    event_bus=bus,
                                    extra_manifest={
                                        "path": r"C:\Users\Administrator\x",
                                    })
            self.assertTrue(out.exists())
            with zipfile.ZipFile(out) as zf:
                names = set(zf.namelist())
                self.assertTrue({"manifest.json", "report.txt", "events.jsonl",
                                 "app.log", "notify.log"} <= names)
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                self.assertEqual(manifest["tool"], "Git-clone-Max")
                self.assertEqual(manifest["events"], 1)
                all_text = "".join(zf.read(n).decode("utf-8", errors="replace")
                                   for n in names)
            self.assertNotIn("supersecret", all_text)
            self.assertNotIn("user:pass", all_text)
            self.assertNotIn("token@host", all_text)
            self.assertNotIn("abc123", all_text)
            # 审查 R3：本机用户名/目录必须打码
            self.assertNotIn("Administrator", all_text)
            self.assertIn("~", all_text)


class TestRetryPanelRows(unittest.TestCase):
    def test_retry_rows_old_record_gets_classified(self):
        from gcm.ui.retry_panel import retry_rows

        class Q:
            def snapshot(self):
                return [{"key": "a/r", "display": "a/r", "attempts": 1,
                         "next_retry_at": 0, "message": "Connection timed out"}]

        rows = retry_rows(Q())
        self.assertEqual(rows[0]["category"], CAT_NETWORK)
        self.assertTrue(rows[0]["title"])

    def test_disabled_queue_does_not_fake_success(self):
        """审查 R5：自动重试关闭时，面板不能报「已请求立即重试」。"""
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from gcm.ui.retry_panel import RetryQueuePanel
        q = RetryQueue(path=None, enabled=True)
        q.record_failure(_spec("a", "r"), "Connection timed out")
        q.set_enabled(False)
        panel = RetryQueuePanel(owner=None, queue=q)
        panel.refresh()
        self.assertFalse(panel.btn_retry.isEnabled())
        self.assertFalse(panel.btn_retry_all.isEnabled())
        self.assertIn("未启用", panel.summary_lbl.text())
        panel.table.selectRow(0)
        self.assertEqual(panel.retry_selected(), 0)
        self.assertEqual(panel.retry_all(), 0)
        panel.close()

    def test_panel_refresh_and_actions(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from gcm.ui.retry_panel import RetryQueuePanel
        q = RetryQueue(path=None, enabled=True)
        q.record_failure(_spec("a", "r"), "Connection timed out")
        panel = RetryQueuePanel(owner=None, queue=q)
        self.assertEqual(panel.refresh(), 1)
        self.assertIn("1", panel.summary_lbl.text())
        panel.table.selectRow(0)
        self.assertEqual(panel.retry_selected(), 1)
        self.assertEqual(q.snapshot()[0]["next_retry_at"], 0.0)
        panel.table.selectRow(0)
        self.assertEqual(panel.remove_selected(), 1)
        self.assertEqual(q.count(), 0)
        panel.refresh()
        panel.close()


class TestOnboardingSemanticMode(unittest.TestCase):
    def test_selected_mode_value_is_stable(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from gcm.ui.onboarding import MODE_SHALLOW, OnboardingDialog
        dlg = OnboardingDialog(default_dir="C:/tmp")
        self.assertEqual(dlg.selected_mode_value, "full")
        dlg.mode_combo.setCurrentIndex(1)
        self.assertEqual(dlg.selected_mode_value, MODE_SHALLOW)
        self.assertEqual(dlg.selected_mode, "浅克隆")   # zh 模式仍返回中文显示
        dlg.close()


class TestEnginePublishesEvents(unittest.TestCase):
    """引擎真实回调 → 事件总线（不启动 worker，只验证发布契约）。"""

    def test_result_and_error_events(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from gcm.app.engine import SyncEngine
        from gcm.models import SyncAction, SyncResult, SyncStatus
        with tempfile.TemporaryDirectory() as td:
            bus = EventBus()
            eng = SyncEngine(Path(td), event_bus=bus)
            eng._batch_id = "batch-test"
            eng._batch_counts = {}
            sub = bus.subscribe()
            try:
                ok = SyncResult(spec=_spec("a", "r"), status=SyncStatus.SUCCESS,
                                action=SyncAction.CLONED, message="ok")
                eng._on_result(0, ok)
                bad = SyncResult(spec=_spec("b", "r"), status=SyncStatus.FAILED,
                                 action=SyncAction.FAILED,
                                 message="Connection timed out")
                eng._on_result(1, bad)
                events = sub.wait(1.0)
            finally:
                sub.close()
            types = [e.type for e in events]
            self.assertEqual(types.count(EventType.TASK_RESULT), 2)
            self.assertEqual(types.count(EventType.TASK_ERROR), 1)
            err = next(e for e in events if e.type == EventType.TASK_ERROR)
            self.assertEqual(err.data["category"], CAT_NETWORK)
            self.assertTrue(err.data["retryable"])
            self.assertEqual(err.data["batch_id"], "batch-test")
            self.assertEqual(eng._batch_counts["success"], 1)
            self.assertEqual(eng._batch_counts["failed"], 1)
            eng.shutdown()

    def test_toggle_retry_queue_creates_it(self):
        """审查 R5：引擎启动时重试关，用户中途打开必须就地创建队列并轮询。"""
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from gcm.app.engine import SyncEngine
        with tempfile.TemporaryDirectory() as td:
            eng = SyncEngine(Path(td), retry_enabled=False)
            try:
                self.assertIsNone(eng.retry_queue)
                eng.configure_retry_queue(Path(td) / "rq.json", True, 2, 30)
                self.assertIsNotNone(eng.retry_queue)
                self.assertTrue(eng.retry_queue.enabled)
                self.assertTrue(eng._retry_timer.isActive())
                eng.configure_retry_queue(Path(td) / "rq.json", False)
                self.assertFalse(eng.retry_queue.enabled)
            finally:
                eng.shutdown()

    def test_retry_queue_event_on_failure(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from gcm.app.engine import SyncEngine
        from gcm.models import SyncAction, SyncResult, SyncStatus
        with tempfile.TemporaryDirectory() as td:
            bus = EventBus()
            eng = SyncEngine(
                Path(td), event_bus=bus,
                retry_queue_path=Path(td) / "retry.json",
                retry_enabled=True, retry_max=2)
            eng._batch_id = "b"
            sub = bus.subscribe()
            try:
                eng._on_result(0, SyncResult(
                    spec=_spec("a", "r"), status=SyncStatus.FAILED,
                    action=SyncAction.FAILED, message="Connection timed out"))
                events = sub.wait(1.0)
            finally:
                sub.close()
            retry = [e for e in events if e.type == EventType.RETRY_QUEUED]
            self.assertEqual(len(retry), 1)
            self.assertEqual(retry[0].data["attempts"], 1)
            self.assertEqual(eng.retry_queue.count(), 1)
            eng.shutdown()


class TestBatchState(unittest.TestCase):
    def test_normal_lifecycle(self):
        from gcm.app.batch_state import BatchState, TaskState
        st = BatchState(total=2)
        st.register("a/r")
        st.register("b/r")
        self.assertEqual(st.summary()["active"], 2)
        self.assertTrue(st.mark("a/r", "running"))
        self.assertTrue(st.mark("a/r", "success"))
        self.assertTrue(st.mark("b/r", "running"))
        self.assertTrue(st.mark("b/r", "failed"))
        s = st.summary()
        self.assertEqual(s["terminal"], 2)
        self.assertEqual(s["active"], 0)
        self.assertEqual(s["counts"][TaskState.SUCCESS], 1)
        self.assertEqual(s["counts"][TaskState.FAILED], 1)

    def test_terminal_is_immutable_and_idempotent(self):
        from gcm.app.batch_state import BatchState
        st = BatchState(total=1)
        st.register("a/r")
        self.assertTrue(st.mark("a/r", "success"))
        self.assertTrue(st.mark("a/r", "success"))   # 幂等
        self.assertFalse(st.mark("a/r", "running"))  # 终态不可回退
        self.assertEqual(st.invalid_transitions, 1)

    def test_queued_can_skip_or_cancel(self):
        from gcm.app.batch_state import BatchState
        st = BatchState(total=2)
        st.register("a/r")
        st.register("b/r")
        self.assertTrue(st.mark("a/r", "skipped"))
        self.assertTrue(st.mark("b/r", "cancelled"))
        self.assertEqual(st.summary()["terminal"], 2)

    def test_unknown_key_is_registered_on_demand(self):
        from gcm.app.batch_state import BatchState
        st = BatchState(total=0)
        self.assertTrue(st.mark("late/r", "failed"))
        self.assertEqual(st.summary()["counts"]["failed"], 1)


    def test_same_repo_different_tags_do_not_collide(self):
        """审查 R2：同一仓库 @v1/@v2 同批时状态机必须分别记账。"""
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from gcm.app.batch_state import BatchState
        from gcm.app.engine import SyncEngine
        from gcm.models import SyncAction, SyncResult, SyncStatus
        with tempfile.TemporaryDirectory() as td:
            eng = SyncEngine(Path(td))
            eng._batch_id = "b"
            s1 = RepoSpec(owner="a", repo="r",
                          url_https="https://github.com/a/r.git",
                          folder_name="a__r@v1")
            s2 = RepoSpec(owner="a", repo="r",
                          url_https="https://github.com/a/r.git",
                          folder_name="a__r@v2")
            eng._batch_state = BatchState(total=2)
            eng._batch_state.register(s1.folder_name)
            eng._batch_state.register(s2.folder_name)
            eng._on_result(0, SyncResult(spec=s1, status=SyncStatus.SUCCESS,
                                         action=SyncAction.CLONED, message="ok"))
            eng._on_result(1, SyncResult(spec=s2, status=SyncStatus.FAILED,
                                         action=SyncAction.FAILED,
                                         message="Connection timed out"))
            summary = eng._batch_state.summary()
            self.assertEqual(summary["terminal"], 2)
            self.assertEqual(summary["invalid_transitions"], 0)
            self.assertEqual(summary["counts"]["success"], 1)
            self.assertEqual(summary["counts"]["failed"], 1)
            eng.shutdown()


class TestEngineFactory(unittest.TestCase):
    """审查 S2：engine_factory 的参数映射必须有断言，否则「用户改了没用」会复发。"""

    def test_mapping_from_settings(self):
        from types import SimpleNamespace

        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from gcm.db.settings import Settings
        from gcm.ui.engine_factory import build_sync_engine
        with tempfile.TemporaryDirectory() as td:
            s = Settings()
            s.concurrency = 7
            s.fetch_timeout = 111
            s.clone_timeout = 222
            s.retries = 3
            s.proxy = "http://127.0.0.1:7890"
            s.token = "tok"
            s.rate_limit_kbps = 512
            s.lfs_enabled = True
            s.post_clone_hook = "echo hi"
            s.ssh_key_path = "C:/keys/id"
            s.retry_enabled = True
            s.retry_max = 3
            s.retry_backoff_base_sec = 45
            s.watchdog_enabled = True
            s.watchdog_interval_min = 15
            s.mirror_use_presets = False
            s.mirror_fallback = False
            s.mirror_probe_timeout = 5
            s.host_tokens = {"gitlab.com": "x"}
            bus = EventBus()
            owner = SimpleNamespace(
                settings=s, db=None, progress_path=Path(td) / "p.json",
                data_dir=Path(td), session_store=None,
                _progress_gc_mod=lambda: None)
            eng = build_sync_engine(owner, Path(td) / "out",
                                    single_branch=True, mirror=True,
                                    event_bus=bus)
            try:
                self.assertEqual(eng.concurrency, 7)
                self.assertEqual(eng.fetch_timeout, 111)
                self.assertEqual(eng.clone_timeout, 222)
                self.assertEqual(eng.retries, 3)
                self.assertEqual(eng.proxy, "http://127.0.0.1:7890")
                self.assertEqual(eng.token, "tok")
                self.assertEqual(eng._rate_limit_kbps, 512)
                self.assertTrue(eng._lfs)
                self.assertEqual(eng._post_clone_hook, "echo hi")
                self.assertEqual(eng._ssh_key_global, "C:/keys/id")
                self.assertTrue(eng._mirror_launch)
                self.assertFalse(eng.mirror_use_presets)
                self.assertFalse(eng.mirror_fallback)
                self.assertEqual(eng.mirror_probe_timeout, 5.0)
                self.assertEqual(eng.host_tokens, {"gitlab.com": "x"})
                self.assertTrue(eng.watchdog_enabled)
                self.assertEqual(eng.watchdog_interval_min, 15)
                self.assertIsNotNone(eng.retry_queue)
                self.assertTrue(eng.retry_queue.enabled)
                self.assertEqual(eng.retry_queue.max_retries, 3)
                self.assertEqual(eng.retry_queue.backoff_base_sec, 45)
                self.assertIs(eng._event_bus, bus)
            finally:
                eng.shutdown()


class TestLocalApiSettings(unittest.TestCase):
    def test_defaults_are_off_and_loopback_port(self):
        from gcm.db.settings import Settings
        s = Settings()
        self.assertFalse(s.local_api_enabled)
        self.assertEqual(s.local_api_port, 8765)

    def test_save_callbacks_toggle_and_apply(self):
        """写入侧 + 消费侧接线：改设置必须真的触发启停，而不是只存字段。"""
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from gcm.ui.settings_panel import SettingsPanel

        class Store:
            def __init__(self):
                self.saved = 0

            def save(self, settings):
                self.saved += 1

        class Owner:
            def __init__(self):
                from gcm.db.settings import Settings
                self.settings = Settings()
                self.settings_store = Store()
                self.applied = 0

            def _apply_local_api_settings(self):
                self.applied += 1

        owner = Owner()
        panel = SettingsPanel(owner)
        panel._save_local_api_enabled(True)
        self.assertTrue(owner.settings.local_api_enabled)
        self.assertEqual(owner.settings_store.saved, 1)
        self.assertEqual(owner.applied, 1)
        panel._save_local_api_port(9876)
        self.assertEqual(owner.settings.local_api_port, 9876)
        self.assertEqual(owner.applied, 2)


class TestEventPanel(unittest.TestCase):
    def test_filter_events_modes(self):
        from gcm.app.event_bus import Event
        from gcm.ui.event_panel import filter_events
        evs = [
            Event("batch_started", 1, 0.0, {}),
            Event("task_progress", 2, 0.0, {}),
            Event("task_result", 3, 0.0, {}),
            Event("task_error", 4, 0.0, {}),
            Event("retry_queued", 5, 0.0, {}),
        ]
        self.assertEqual([e.type for e in filter_events(evs, "default")],
                         ["batch_started", "task_result", "task_error", "retry_queued"])
        self.assertEqual([e.type for e in filter_events(evs, "progress")],
                         ["task_progress"])
        self.assertEqual([e.type for e in filter_events(evs, "errors")],
                         ["task_error"])
        self.assertEqual([e.type for e in filter_events(evs, "retry")],
                         ["retry_queued"])
        self.assertEqual([e.type for e in filter_events(evs, "batch")],
                         ["batch_started"])

    def test_panel_refresh_and_copy(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from gcm.ui.event_panel import EventStreamPanel
        bus = EventBus()
        bus.publish(EventType.BATCH_STARTED, {"total": 2})
        bus.publish(EventType.TASK_RESULT, {"key": "a/r", "status": "success",
                                            "action": "cloned", "message": "ok"})
        bus.publish(EventType.TASK_PROGRESS, {"key": "a/r", "percent": 50})
        panel = EventStreamPanel(bus=bus)
        self.assertEqual(panel.refresh(), 2)     # 默认隐藏进度
        panel.mode_combo.setCurrentIndex(1)      # 任务进度
        self.assertEqual(panel.refresh(), 1)
        panel.mode_combo.setCurrentIndex(0)
        panel.refresh()
        self.assertEqual(panel.copy_json(), 2)
        panel.close()


class TestGuiHostsLocalApi(unittest.TestCase):
    """GUI 进程内托管本地 API：真实 start → /version → stop。"""

    def test_start_and_stop(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            store = SettingsStore(tmp / "settings.json")
            s = store.load()
            s.local_api_enabled = True
            s.local_api_port = 0          # 内核分配，避免测试抢端口
            store.save(s)
            win = MainWindow(data_dir=tmp, db=Database(tmp / "t.db"), settings=store)
            try:
                win._apply_local_api_settings()
                srv = win._local_api_server
                self.assertIsNotNone(srv, "设置开启后必须真的启动服务器")
                port = srv.server_address[1]
                conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    conn.request("GET", "/version")
                    resp = conn.getresponse()
                    payload = json.loads(resp.read().decode("utf-8"))
                finally:
                    conn.close()
                from gcm import __version__
                self.assertEqual(payload["version"], __version__)
                self.assertTrue(payload["readonly"])
            finally:
                win._stop_local_api()
                self.assertIsNone(win._local_api_server)
                win.close()


if __name__ == "__main__":
    unittest.main()
