"""R20-1/R20-2/R20-3 引擎级守卫：感知调度真实生效 + 批次终态原因。

R20-1：同 host 连续失败后，待发项不被投放且保留在队列；退避过期或成功清零后恢复；
        其他 host 的待发项不受牵连。
R20-2：慢 worker 判据必须有生产消费点：发 task_slow 事件（含 elapsed/median）并记日志。
R20-3：BATCH_FINISHED 必须携带 StopReason（取消 / 全失败 / 混合成功）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402

from gcm.app.event_bus import EventBus, EventType  # noqa: E402
from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus  # noqa: E402


def _spec(i: int, host_url: str | None = None) -> RepoSpec:
    url = host_url or f"https://github.com/o{i}/r{i}.git"
    parts = url.rstrip("/").split("/")
    owner, repo = parts[-2], parts[-1].removesuffix(".git")
    return RepoSpec(owner, repo, url, folder_name=f"{owner}__{repo}")


def _result(spec: RepoSpec, status: SyncStatus, ms: int = 1000) -> SyncResult:
    action = (SyncAction.CANCELLED if status == SyncStatus.CANCELLED
              else SyncAction.FAILED if status == SyncStatus.FAILED
              else SyncAction.CLONED)
    return SyncResult(spec=spec, status=status, action=action,
                      message="x", duration_ms=ms,
                      started=time.time(), ended=time.time() + ms / 1000)


class _EngineHarness(unittest.TestCase):
    """共用脚手架：真引擎 + mock 真实 worker 启动（不碰网络/线程池）。"""

    def setUp(self):
        from gcm.app.engine import SyncEngine
        # concurrency=1 → 首批只投一个，后续走待发队列（退避的真实作用面）
        self.eng = SyncEngine(Path(tempfile.mkdtemp()) / "clones", concurrency=1)
        self.started: list[int] = []

        def _fake_start(i, spec, flag):
            self.started.append(i)
            self.eng._running = self.eng._running_count() + 1
            self.eng.flags[i] = flag
            self.eng.specs[i] = spec

        self.eng._start_worker = mock.Mock(side_effect=_fake_start)

    def tearDown(self):
        self.eng._cancelled = True
        self.eng._pending_queue = []
        try:
            self.eng._stop_buffers()
        except Exception:
            pass

    def _feed(self, spec: RepoSpec, status: SyncStatus, ms: int = 1000) -> None:
        self.eng._on_result(0, _result(spec, status, ms))

    def _complete(self, index: int) -> None:
        """模拟一个 worker 真正收工：递减在跑与待完成，再触发补投。"""
        self.eng._pending = max(0, self.eng._pending - 1)
        self.eng._running = max(0, self.eng._running - 1)
        self.eng._replenish()


class TestHostBackoffGatesDispatch(_EngineHarness):
    def test_failed_host_stops_receiving_new_dispatches(self):
        a1, a2, a3 = _spec(1), _spec(2), _spec(3)  # 全部 github.com
        b1 = _spec(4, "https://gitlab.com/b/x.git")
        self.eng.launch([a1, a2, a3, b1], check_existing=False)
        self.assertEqual(len(self.eng._pending_queue), 3)
        self._feed(a1, SyncStatus.FAILED)
        self._feed(a2, SyncStatus.FAILED)
        self._complete(0)
        hosts = [self.eng._host_for(self.eng.specs[i]) for i in self.started[1:]]
        queued_hosts = {self.eng._host_for(s)
                        for (_i, s, _f) in self.eng._pending_queue}
        self.assertNotIn("github.com", hosts,
                         "退避窗口内不得再向 github.com 投放新任务")
        self.assertIn("github.com", queued_hosts,
                      "被跳过的任务必须保留在待发队列，不得丢弃")
        self.assertIn("gitlab.com", hosts, "其他 host 的任务必须照常投放")
        self.assertEqual(self.eng._host_backoff.streak("github.com"), 2)

    def test_backoff_expires_and_dispatch_resumes(self):
        clock = {"t": 1000.0}
        from gcm.app.adaptive import HostBackoffTable
        self.eng._host_backoff = HostBackoffTable(clock=lambda: clock["t"])
        a1, a2 = _spec(1), _spec(2)
        self.eng.launch([a1, a2], check_existing=False)
        before = len(self.started)
        self._feed(a1, SyncStatus.FAILED)
        self._complete(0)
        self.assertEqual(len(self.started), before,
                         "退避窗口内不得补投同 host 新任务")
        self.assertEqual(len(self.eng._pending_queue), 1, "任务保留在待发队列")
        clock["t"] += 6.0
        self.eng._replenish()
        self.assertEqual(len(self.started), before + 1, "退避到期后恢复投放")
        self.assertEqual(len(self.eng._pending_queue), 0)

    def test_success_clears_backoff_immediately(self):
        a1, a2 = _spec(1), _spec(2)
        self.eng.launch([a1, a2], check_existing=False)
        self._feed(a1, SyncStatus.FAILED)
        self._feed(a1, SyncStatus.SUCCESS)
        self.assertEqual(self.eng._host_backoff.delay_for("github.com"), 0.0)
        before = len(self.started)
        self._complete(0)
        self.assertEqual(len(self.started), before + 1, "成功清零后照常补投")


class TestSlowWorkerEvent(_EngineHarness):
    def test_slow_repo_publishes_task_slow_event(self):
        bus = EventBus()
        self.eng._event_bus = bus
        fast1, fast2, slow = _spec(1), _spec(2), _spec(3)
        self.eng.launch([fast1, fast2, slow], check_existing=False)
        self.eng._slow_monitor._floor = 0.0
        self._feed(fast1, SyncStatus.SUCCESS, ms=1000)
        self._feed(fast2, SyncStatus.SUCCESS, ms=1200)
        with bus.subscribe(types=[EventType.TASK_SLOW]) as sub:
            self._feed(slow, SyncStatus.SUCCESS, ms=100_000)
            got = sub.wait(0.2)
        self.assertEqual(len(got), 1, "慢仓库必须发 task_slow 事件")
        payload = got[0].data
        self.assertEqual(payload.get("folder_name"), slow.folder_name)
        self.assertEqual(int(payload.get("elapsed_ms", 0)), 100_000)
        self.assertGreater(float(payload.get("median_ms", 0)), 0)

    def test_normal_repo_never_publishes_task_slow(self):
        bus = EventBus()
        self.eng._event_bus = bus
        fast1, fast2, normal = _spec(1), _spec(2), _spec(3)
        self.eng.launch([fast1, fast2, normal], check_existing=False)
        self.eng._slow_monitor._floor = 0.0
        self._feed(fast1, SyncStatus.SUCCESS, ms=1000)
        self._feed(fast2, SyncStatus.SUCCESS, ms=1200)
        with bus.subscribe(types=[EventType.TASK_SLOW]) as sub:
            self._feed(normal, SyncStatus.SUCCESS, ms=2000)
            got = sub.wait(0.2)
        self.assertEqual(got, [], "正常耗时不得误报 task_slow")

    def test_event_type_registered(self):
        self.assertIn(EventType.TASK_SLOW, EventType.ALL)


class TestBatchStopReason(_EngineHarness):
    def _finish_payload(self, bus: EventBus) -> dict:
        with bus.subscribe(types=[EventType.BATCH_FINISHED]) as sub:
            self.eng._pending = 0
            self.eng._on_worker_done()
            events = sub.wait(0.2)
        self.assertEqual(len(events), 1)
        return events[0].data

    def test_cancelled_batch(self):
        bus = EventBus()
        self.eng._event_bus = bus
        self.eng.launch([_spec(1), _spec(2)], check_existing=False)
        self.eng._cancelled = True
        self._feed(_spec(1), SyncStatus.CANCELLED)
        payload = self._finish_payload(bus)
        self.assertEqual(payload["stop_reason"], "cancelled")

    def test_all_failed_batch(self):
        bus = EventBus()
        self.eng._event_bus = bus
        self.eng.launch([_spec(1), _spec(2)], check_existing=False)
        self._feed(_spec(1), SyncStatus.FAILED)
        self._feed(_spec(2), SyncStatus.FAILED)
        payload = self._finish_payload(bus)
        self.assertEqual(payload["stop_reason"], "failed")

    def test_mixed_success_batch(self):
        bus = EventBus()
        self.eng._event_bus = bus
        self.eng.launch([_spec(1), _spec(2)], check_existing=False)
        self._feed(_spec(1), SyncStatus.SUCCESS)
        self._feed(_spec(2), SyncStatus.FAILED)
        payload = self._finish_payload(bus)
        self.assertEqual(payload["stop_reason"], "completed")


if __name__ == "__main__":
    unittest.main()
