"""S-08 后台执行器：把引擎结果回调里的 SQLite 写迁出 GUI 线程。

**为什么**：`engine._on_result` 由 worker 线程 emit、经 Qt 排队到 **GUI 线程**
执行（AutoConnection），其中的 repos.db 写（upsert_repo/add_sync_history/
set_repo_profile）虽实测仅 ~3ms/仓，但几百仓批次仍会累计占用 GUI 线程；
known-issues S-08 后半段登记的就是这个。

**方案**：单线程 `ThreadPoolExecutor` 串行消费写任务——
- 保序：同一仓库的 upsert/history/profile 必须按结果顺序落库；
- 关停：`shutdown(wait=True)` 在引擎 discard/shutdown 时把队尾任务写完，
  不丢任何一条结果（崩溃丢的只是 OS 缓冲，与旧行为一致）；
- 失败：任务异常在 worker 线程记 applog（GUI 线程零负担），不影响后续任务。

**降级**：executor 不可用（极端情况下 submit 抛）时回退为**原地执行**，
行为与旧版完全一致。
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from .applog import warn as _warn


class DbWriteExecutor:
    """单线程保序 DB 写执行器。"""

    def __init__(self) -> None:
        self._pool: ThreadPoolExecutor | None = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="gcm-dbwrite")
        self._lock = threading.Lock()
        self._closed = False

    def submit(self, fn: Callable[[], Any]) -> Future | None:
        """投递一个写任务；executor 已关停时**原地执行**（保正确性降级）。"""
        with self._lock:
            pool, closed = self._pool, self._closed
        if pool is None or closed:
            try:
                fn()
            except Exception as e:  # noqa: BLE001 —— 降级路径也要留痕
                _warn(f"DB 写（降级原地执行）失败：{e}")
            return None
        try:
            return pool.submit(fn)
        except RuntimeError as e:  # 解释器关停等极端场景
            _warn(f"DB 写投递失败，转原地执行：{e}")
            try:
                fn()
            except Exception as e2:  # noqa: BLE001
                _warn(f"DB 写（降级原地执行）失败：{e2}")
            return None

    def shutdown(self, wait: bool = True) -> None:
        """关停执行器；wait=True 时等队尾任务写完（discard/shutdown 用）。"""
        with self._lock:
            pool, self._closed = self._pool, True
        if pool is not None:
            pool.shutdown(wait=wait)
        with self._lock:
            self._pool = None
