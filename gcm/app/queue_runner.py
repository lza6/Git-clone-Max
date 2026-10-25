"""同步队列的运行编排（G80-B7-3 / S3）：把「到点的队列」变成「引擎批次」。

职责边界（与设计稿一致）：
- 本模块**只做判定与调度**：哪些队列到点、引擎是否空闲、启动后登记；
- **不自己实现同步**——批次复用调用方（MainWindow）提供的 `_launch`（既有的
  批次路径：并发/镜像/凭据/进度表全走同一条链）；
- **不做重试**——失败项照旧进 `retry_queue`（G67-3：两层重投会互相污染）；
- 互斥规则：手动批次 > 队列批次（`engine.busy` 时直接让路，下个 tick 再看）。

给 MainWindow 的接缝只有一个：`QueueRunner(callbacks)`，其中 callbacks 里放
`launch(specs)`（闭包包 `_launch`）与 `is_busy()`。这样本模块不 import 任何 Qt/UI。
"""
from __future__ import annotations

import datetime
from collections.abc import Callable
from typing import Any

from . import background_jobs
from .queue_scheduler import is_due, missed_run_allowed

# 后台任务面板的第 5 行（B7-1 已建面板，S4 只需登记）
JOB_SYNC_QUEUE = "sync_queue"


class QueueRunner:
    """定时检查 sync_queue 并通过回调启动批次。线程安全（QTimer 单线程调用）。"""

    def __init__(self, *, store, get_settings: Callable[[], Any],
                 launch: Callable[[list[dict[str, Any]]], int],
                 is_busy: Callable[[], bool],
                 now_fn: Callable[[], datetime.datetime] | None = None,
                 tick_interval_sec: float = 60.0):
        """store: QueueStore；launch: 包装好 _launch 的回调（传 specs dicts）。"""
        self._store = store
        self._get_settings = get_settings
        self._launch = launch
        self._is_busy = is_busy
        self._now_fn = now_fn or datetime.datetime.now
        self._tick_interval = max(30.0, float(tick_interval_sec))
        self._enabled = False
        self._timer: Any = None  # QTimer 由 UI 侧创建（app 层不 import Qt）

    # ---------------------------------------------------------------- 生命周期
    def attach_timer(self, timer: Any) -> None:
        """UI 侧把 QTimer 交给 runner（app 层不 import Qt，遵守 B6 边界）。"""
        self._timer = timer

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        if self._timer is not None:
            if self._enabled:
                self._timer.start()
            else:
                self._timer.stop()
        background_jobs.registry().register(
            JOB_SYNC_QUEUE, "定时同步队列",
            enabled=self._enabled, interval_sec=self._tick_interval)

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ---------------------------------------------------------------- 核心判定
    def due_queues(self, now: datetime.datetime | None = None) -> list[dict[str, Any]]:
        """返回此刻允许启动的队列行（含 specs），按 id 升序。

        判定三步：
        1. enabled；
        2. `is_due`（周几 + 时间窗，跨零点窗支持）；
        3. 未到窗/今天已跑过的 → 跳过；窗后启动且 on_missed=run_once 且今天没跑 → 补跑。
        """
        now = now or self._now_fn()
        out: list[dict[str, Any]] = []
        for row in self._store.list_queues():
            if not row.get("enabled"):
                continue
            days = set(row.get("days_set") or [])
            start = str(row.get("start_time") or "")
            end = str(row.get("end_time") or "")
            on_missed = str(row.get("on_missed") or "skip")
            last_run = str(row.get("last_run_at") or "")
            # 到点（时间窗内）或「错过但允许补跑一次」都算 due —— 两个判定共享 append
            due_now = is_due(days, start, end, now)
            catch_up = missed_run_allowed(days, start, on_missed, last_run, now)
            if due_now or catch_up:
                out.append(row)
        return out

    def tick(self) -> int:
        """一次检查：到点且引擎空闲 → 逐个启动批次并 mark_run。返回启动的队列数。

        引擎忙 → 整轮让路（手动批次优先），下个 tick 再看 —— 不排队不堆积。
        """
        if not self._enabled:
            return 0
        try:
            if self._is_busy():
                return 0
        except Exception as e:  # noqa: BLE001 - 忙闲查询失败按"忙"处理更安全
            from .applog import warn as _warn
            _warn(f"队列调度查询引擎状态失败（按忙处理）：{e}")
            return 0
        due = self.due_queues()
        launched = 0
        for row in due:
            specs = row.get("specs") or []
            if not specs:
                continue
            try:
                self._launch(specs)
                self._store.mark_run(int(row["id"]), "dispatched")
                launched += 1
                from .applog import info as _info
                _info(f"定时队列「{row.get('name')}」已启动（{len(specs)} 个仓库）")
            except Exception as e:  # noqa: BLE001 - 单个队列失败不拖垮其它队列
                self._store.mark_run(int(row["id"]), f"error: {e}")
                from .applog import warn as _warn
                _warn(f"定时队列「{row.get('name')}」启动失败：{e}")
        if due:
            background_jobs.notify_run(JOB_SYNC_QUEUE,
                                       result=f"到点 {len(due)} 队列，启动 {launched}")
        return launched
