"""G80-B7-2 守卫：卡死任务识别（只报告，不回收槽位）。

背景：`QThreadPool` 的槽位只在 runnable 返回后才释放。worker 若卡在一个永不返回的
调用里（历史上真出现过：`_pump` 读线程泄漏），槽位被永久占住 —— 表现为「并发数越跑
越小、新任务一直排队」，但界面上什么都看不出来。

四条不变量：
1. 未超过阈值 → **不上报**（不能一有慢任务就喊狼来了）；
2. 超过阈值 → 发一次 `task_stuck` 事件，且**同一任务只报一次**（不会每 30s 刷屏）；
3. 任务到终态 → 记账被清掉（不再参与判定）；
4. **不回收槽位**：本版只报告（回收会让实际并发临时超上限，需用户显式选择）。
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402


def _engine(tmp: Path):
    from gcm.app.engine import SyncEngine

    return SyncEngine(root=tmp, db=None, progress_path=None)


class TestStuckDetection(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.root = Path(self._td.name)
        self.engine = _engine(self.root)

    def tearDown(self):
        self.engine.shutdown()
        self._td.cleanup()

    def test_not_reported_below_threshold(self):
        self.engine._inflight_at["a__b"] = time.time() - 5
        self.engine._check_stuck_tasks()
        self.assertEqual(self.engine.stuck_task_keys(), [],
                         "5 秒的任务不该被当成卡死")

    def test_reported_once_after_threshold(self):
        self.engine._inflight_at["a__b"] = time.time() - (self.engine.stuck_threshold_sec() + 5)
        published = []
        with mock.patch.object(self.engine, "_publish",
                               side_effect=lambda etype, **kw: published.append((etype, kw))):
            self.engine._check_stuck_tasks()
            self.engine._check_stuck_tasks()   # 第二轮不应该再报
        stuck = [p for p in published if p[0] == "task_stuck"]
        self.assertEqual(len(stuck), 1, "同一任务只允许报一次 task_stuck")
        self.assertIn("卡死", stuck[0][1].get("message", ""))
        self.assertEqual(self.engine.stuck_task_keys(), ["a__b"])

    def test_forget_clears_bookkeeping(self):
        key = "a__b"
        self.engine._inflight_at[key] = time.time() - (self.engine.stuck_threshold_sec() + 5)
        with mock.patch.object(self.engine, "_publish"):
            self.engine._check_stuck_tasks()
        self.assertEqual(self.engine.stuck_task_keys(), [key])
        self.engine.forget_inflight(key)
        self.assertEqual(self.engine.stuck_task_keys(), [])
        self.assertNotIn(key, self.engine._inflight_at)

    def test_threshold_uses_clone_timeout(self):
        self.engine.clone_timeout = 60
        self.assertEqual(self.engine.stuck_threshold_sec(), 180.0,
                         "阈值应为 clone_timeout + 120s 余量")

    def test_event_type_registered(self):
        from gcm.app.event_bus import EventType

        self.assertIn("task_stuck", EventType.ALL, "新事件类型必须登记进 ALL（否则 SSE 过滤会漏）")

    def test_cancel_stuck_tasks_marks_flags_and_counts(self):
        """用户显式动作：取消已上报的卡死任务 → CancelFlag 置位 + 返回数量。

        这是"只报告"与"能处理"之间的桥：不回收槽位（不超发并发），
        只是把取消标记交给 worker，让它在下一个检查点尽快收敛。
        """
        from gcm.app.worker import CancelFlag

        self.engine.clone_timeout = 0  # 阈值 = 120s，方便构造"已上报"
        flag_a, flag_b = CancelFlag(), CancelFlag()
        spec_a = mock.Mock()
        spec_a.folder_name = "a__x"
        spec_b = mock.Mock()
        spec_b.folder_name = "b__y"
        self.engine.specs = {0: spec_a, 1: spec_b}
        self.engine.flags = {0: flag_a, 1: flag_b}
        with mock.patch.object(self.engine, "_publish"):
            self.engine._inflight_at["a__x"] = time.time() - 200
            self.engine._check_stuck_tasks()
            self.assertEqual(self.engine.stuck_task_keys(), ["a__x"])
            n = self.engine.cancel_stuck_tasks()
        self.assertEqual(n, 1)
        self.assertTrue(flag_a._cancelled, "a__x 的 CancelFlag 应被置位")
        self.assertFalse(flag_b._cancelled, "未上报的 b__y 不该被波及")

    def test_cancel_without_reported_returns_zero(self):
        self.assertEqual(self.engine.cancel_stuck_tasks(), 0)

    def test_no_slot_reclaim_in_this_version(self):
        """明确守住「本版不回收槽位」：源码里不得出现改 MaxThreadCount 的回收逻辑。"""
        src = Path("gcm/app/engine.py").read_text(encoding="utf-8")
        start = src.index("def _check_stuck_tasks")
        end = src.index("def forget_inflight", start)
        body = src[start:end]
        self.assertNotIn("setMaxThreadCount", body, "卡死巡检不得悄悄改并发（回收需用户显式选择）")
        self.assertNotIn("_replenish(", body, "卡死巡检不得自行补投任务")

    def test_launch_starts_and_finish_stops_timer(self):
        src = Path("gcm/app/engine.py").read_text(encoding="utf-8")
        self.assertIn("self._stuck_timer.start()", src)
        self.assertIn("self._stuck_timer.stop()", src)


if __name__ == "__main__":
    unittest.main()
