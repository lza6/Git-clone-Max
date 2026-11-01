# -*- coding: utf-8 -*-
"""G43-1②/G43-2/G43-5 专项测试：批次流控、日志合并窗口、进度心跳。"""
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

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gcm.app.engine import SyncEngine, _BATCH_START_LIMIT
from gcm.models import RepoSpec, SyncStatus


def _spec(i: int) -> RepoSpec:
    return RepoSpec(f"o{i}", f"r{i}", f"https://github.com/o{i}/r{i}.git",
                    folder_name=f"o{i}__r{i}")


class _FakeWorker:
    """最小 worker 替身：仅记录启动并立即模拟完成。"""

    def __init__(self, index, payload, service, db, pp, on_line=None,
                 fetch_depth=0, unshallow=False):
        self.index = index
        self.payload = payload
        self.on_line = on_line

    def start_later(self):
        pass


class TestBatchFlowControl(unittest.TestCase):
    def _engine(self, n_specs: int, concurrency: int = 8):
        d = Path(tempfile.mkdtemp())
        eng = SyncEngine(d / "clones", concurrency=concurrency)
        eng._pending_queue = []
        return eng

    def test_batch_limits_simultaneous_start(self):
        """G43-2 + C1-8：200 个仓库只入队前 `batch_start_limit()` 个（=线程池上限），
        其余进待发队列。

        语义变更（v9.12.0 / Rev7 C1-8）：原实现恒用模块常量 `_BATCH_START_LIMIT=64`，
        而线程池上限最多 32 —— 「流控」名义成立、实际无效（超出的 worker 已构造完，
        各持一个 Service 在池里干等）。现按池上限派生，断言精度不变（仍是精确相等），
        只是基准换成派生值。
        """
        eng = self._engine(200)
        started = []
        eng._start_worker = mock.Mock(side_effect=lambda i, s, f: started.append(i))
        specs = [_spec(i) for i in range(200)]
        limit = eng.batch_start_limit()
        self.assertEqual(limit, 8, "concurrency=8 时首批上限应正好等于 8")
        # 用 mock 避免真实 git：替换 pool.start 语义 —— launch 内部调用 _start_worker
        launched = eng.launch(specs)
        self.assertEqual(launched, 200, "launch 返回去重后总数")
        self.assertEqual(len(started), limit, f"首批只应启动 {limit} 个")
        self.assertEqual(len(eng._pending_queue), 200 - limit)
        # 补投：`_replenish` 现在补满到上限，而 mock 掉 `_start_worker` 不会递增在跑计数
        # → 一次调用会补到上限为止。这里改为直接断言「补投后待发队列变短」，
        # 并单独用 `test_replenish_fills_to_limit` 覆盖「补满」语义。
        before_pending = len(eng._pending_queue)
        eng._replenish()
        self.assertLess(len(eng._pending_queue), before_pending,
                        "补投后待发队列必须变短")
        self.assertGreaterEqual(len(started), limit)
        # 清理
        eng._cancelled = True
        eng._pending_queue = []
        eng._stop_buffers()

    def test_replenish_fills_up_to_limit(self):
        """C1-8：`_replenish` 必须补满到上限（否则用户调高并发后旧节奏不变）。"""
        eng = self._engine(20, concurrency=3)
        started = []

        def _fake_start(i, s, f):
            started.append(i)
            eng._running = eng._running_count() + 1   # 与真实 _start_worker 同语义

        eng._start_worker = mock.Mock(side_effect=_fake_start)
        eng.launch([_spec(i) for i in range(20)])
        self.assertEqual(len(started), 3)
        # 3 → 6：模拟用户运行中把并发调高，随后一个任务完成触发补投
        eng.set_concurrency(6)
        self.assertEqual(len(started), 6, "调高并发后应立即补投到新上限（不等下一个任务完成）")
        self.assertEqual(len(eng._pending_queue), 20 - 6)
        # 一个任务完成 → 降到 5 → 补投回 6
        eng._running = 5
        eng._replenish()
        self.assertEqual(len(started), 7)
        eng._cancelled = True
        eng._pending_queue = []
        eng._stop_buffers()

    def test_dedupe_under_limit(self):
        """≤上限时全部立即入队、无待发。"""
        eng = self._engine(10, concurrency=16)
        started = []
        eng._start_worker = mock.Mock(side_effect=lambda i, s, f: started.append(i))
        eng.launch([_spec(i) for i in range(10)])
        self.assertEqual(len(started), 10)
        self.assertEqual(eng._pending_queue, [])
        eng._stop_buffers()

    def test_cancel_pending_queue_converges(self):
        """G43-2：取消时待发队列清空且不补投；_pending 归零 → finished 恰好一次。"""
        eng = self._engine(0)
        started = []
        # mock 掉真实 worker 启动（避免后台线程在测试结束时仍在跑导致 Qt 退出崩溃）
        eng._start_worker = mock.Mock(side_effect=lambda i, s, f: started.append(i))
        specs = [_spec(i) for i in range(70)]
        limit = eng.batch_start_limit()      # concurrency=8 → 8
        ended = []
        eng.finished.connect(lambda: ended.append(True))
        launched = eng.launch(specs)
        self.assertEqual(launched, 70)
        self.assertEqual(len(started), limit, f"首批应 mock 启动 {limit} 个")
        self.assertEqual(len(eng._pending_queue), 70 - limit)
        # 模拟取消：先清空首批（_pending 到待发数），待发队列由 cancel_all 收敛
        eng._pending = 70 - limit
        eng.cancel_all()
        self.assertEqual(eng._pending, 0, "取消后 _pending 应归零")
        self.assertEqual(eng._pending_queue, [], "待发队列应清空")
        self.assertEqual(len(ended), 1, "finished 应恰好触发一次")
        eng._stop_buffers()


class TestLineBatching(unittest.TestCase):
    def test_line_batch_flush_threshold(self):
        """G43-1②：超过阈值行数立即冲刷；信号最终全部送达。"""
        from gcm.app.engine import _LINE_FLUSH_THRESHOLD
        d = Path(tempfile.mkdtemp())
        eng = SyncEngine(d / "clones")
        received = []
        eng.line.connect(lambda i, t, l: received.append((i, t, l)))
        # 逐条 emit（worker 线程同语义）
        for i in range(_LINE_FLUSH_THRESHOLD + 5):
            eng._emit_line(3, f"line-{i}", "info")
        # 阈值触发 → 全部送达（含最后 5 条缓冲，需手动冲刷模拟 finished 前兜底）
        eng.flush_all_buffers()
        self.assertEqual(len(received), _LINE_FLUSH_THRESHOLD + 5)
        self.assertTrue(any(t == "line-0" for _, t, _ in received))
        self.assertTrue(any(t == f"line-{_LINE_FLUSH_THRESHOLD + 4}" for _, t, _ in received))
        eng._stop_buffers()

    def test_line_batch_small_volume_flush(self):
        """G43-1②：少量行由 flush_all_buffers 兜底送达（finished 前语义）。"""
        d = Path(tempfile.mkdtemp())
        eng = SyncEngine(d / "clones")
        received = []
        eng.line.connect(lambda i, t, l: received.append(t))
        eng._emit_line(0, "开始", "system")
        eng._emit_line(1, "克隆中", "info")
        self.assertEqual(received, [])
        eng.flush_all_buffers()
        self.assertEqual(sorted(received), ["克隆中", "开始"])
        eng._stop_buffers()
        eng._stop_buffers()


class TestProgressHeartbeat(unittest.TestCase):
    def test_progress_buffered_and_flushed(self):
        """G43-5：progress/progress_detail 进入缓冲，flush 后逐条送达。"""
        d = Path(tempfile.mkdtemp())
        eng = SyncEngine(d / "clones")
        prog = []
        detail = []
        eng.progress.connect(lambda i, p: prog.append((i, p)))
        eng.progress_detail.connect(lambda i, t: detail.append((i, t)))
        eng._on_worker_progress(0, "42")
        eng._on_worker_progress(1, "100")
        eng._on_worker_progress_detail(0, "1.2 MiB/s")
        self.assertEqual(prog, [])
        eng.flush_all_buffers()
        self.assertEqual(sorted(prog), [(0, "42"), (1, "100")])
        self.assertEqual(detail, [(0, "1.2 MiB/s")])
        eng._stop_buffers()


if __name__ == "__main__":
    unittest.main(verbosity=2)
