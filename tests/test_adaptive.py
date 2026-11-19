"""批次②（对标 Surge/crawl4ai）守卫：感知调度组件行为。

- SlowWorkerMonitor：0.3x 中位数比判慢 + floor 防噪声 + 空基准不判慢；
- HostBackoffTable：失败翻倍封顶、成功清零、陈旧延迟随时间恢复（注入时钟）；
- 引擎接线：结果回调会更新两张表（失败抬退避、成功清零、耗时入样本）。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402


class TestSlowWorkerMonitor(unittest.TestCase):
    def test_median_and_slow_judgement(self):
        from gcm.app.adaptive import SlowWorkerMonitor
        m = SlowWorkerMonitor(ratio=0.3, floor_seconds=0)
        for i, sec in enumerate((10.0, 12.0, 14.0)):  # 中位数 12
            m.record(f"k{i}", sec)
        self.assertEqual(m.median(), 12.0)
        self.assertFalse(m.is_slow("fast", 15.0))     # 15 < 12/0.3=40
        self.assertTrue(m.is_slow("slow", 41.0))      # 41 > 40

    def test_floor_prevents_noise(self):
        from gcm.app.adaptive import SlowWorkerMonitor
        m = SlowWorkerMonitor(ratio=0.3, floor_seconds=60)
        m.record("a", 1.0)
        m.record("b", 1.2)
        self.assertFalse(m.is_slow("x", 2.0))  # 中位数 1.1/0.3≈3.7 < floor 60

    def test_empty_baseline_never_slow(self):
        from gcm.app.adaptive import SlowWorkerMonitor
        m = SlowWorkerMonitor()
        self.assertEqual(m.median(), 0.0)
        self.assertFalse(m.is_slow("k", 9999.0))

    def test_reset_clears_samples(self):
        from gcm.app.adaptive import SlowWorkerMonitor
        m = SlowWorkerMonitor()
        m.record("a", 100.0)
        m.reset()
        self.assertEqual(m.median(), 0.0)


class TestHostBackoffTable(unittest.TestCase):
    def test_exponential_growth_and_cap(self):
        from gcm.app.adaptive import HostBackoffTable
        t = 100.0
        b = HostBackoffTable(base_seconds=5.0, max_seconds=20.0,
                             clock=lambda: t)
        self.assertEqual(b.on_failure("h1"), 5.0)
        self.assertEqual(b.on_failure("h1"), 10.0)
        self.assertEqual(b.on_failure("h1"), 20.0)
        self.assertEqual(b.on_failure("h1"), 20.0)  # 封顶
        self.assertEqual(b.streak("h1"), 4)

    def test_success_resets_streak(self):
        from gcm.app.adaptive import HostBackoffTable
        t = 100.0
        b = HostBackoffTable(base_seconds=5.0, max_seconds=20.0,
                             clock=lambda: t)
        b.on_failure("h1")
        b.on_failure("h1")
        b.on_success("h1")
        self.assertEqual(b.streak("h1"), 0)
        self.assertEqual(b.delay_for("h1"), 0.0)

    def test_delay_decays_with_time(self):
        from gcm.app.adaptive import HostBackoffTable
        clock = {"t": 100.0}
        b = HostBackoffTable(base_seconds=5.0, max_seconds=300.0,
                             clock=lambda: clock["t"])
        b.on_failure("h1")
        self.assertGreater(b.delay_for("h1"), 0.0)
        clock["t"] += 6.0  # 越过 5s 延迟
        self.assertEqual(b.delay_for("h1"), 0.0)

    def test_hosts_are_independent(self):
        from gcm.app.adaptive import HostBackoffTable
        b = HostBackoffTable(base_seconds=5.0, max_seconds=300.0)
        b.on_failure("a")
        b.on_failure("a")
        self.assertEqual(b.streak("b"), 0)
        self.assertEqual(b.delay_for("b"), 0.0)


class TestEngineWiring(unittest.TestCase):
    """引擎结果回调必须喂两张表（行为断言，真引擎 + 注入结果）。"""

    def test_result_updates_monitor_and_backoff(self):
        import tempfile
        from gcm.app.engine import SyncEngine
        from gcm.models import RepoSpec, SyncResult, SyncStatus
        eng = SyncEngine(Path(tempfile.mkdtemp()) / "clones", concurrency=2)
        spec = RepoSpec(owner="o", repo="r", url_https="https://github.com/o/r")
        res = SyncResult(spec=spec, status=SyncStatus.FAILED,
                         action=__import__("gcm.models", fromlist=["SyncAction"]).SyncAction.FAILED,
                         message="x", duration_ms=5000)
        # 直接调结果回调（worker 线程同一入口）
        eng._on_result(0, res)
        host = eng._host_for(spec)
        self.assertGreaterEqual(eng._host_backoff.streak(host), 1)
        self.assertGreater(eng._slow_monitor.median(), 0.0)
        # 成功后清零
        res2 = SyncResult(spec=spec, status=SyncStatus.SUCCESS,
                          action=__import__("gcm.models", fromlist=["SyncAction"]).SyncAction.CLONED,
                          message="ok", duration_ms=1000)
        eng._on_result(0, res2)
        self.assertEqual(eng._host_backoff.streak(host), 0)


if __name__ == "__main__":
    unittest.main()
