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

from ..i18n import tr
from . import background_jobs
from .queue_scheduler import already_ran_in_window_today, is_due, missed_run_allowed, window_slot

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
            # S-06：到点分支必须排除「今天同窗已跑过」，否则 tick 每 60s 重复启动
            due_now = (is_due(days, start, end, now)
                       and not already_ran_in_window_today(days, start, end, last_run, now))
            # B1：补跑带宽限（窗口结束后 CATCHUP_GRACE_HOURS 内才允许）
            catch_up = missed_run_allowed(days, start, on_missed, last_run, now,
                                          end=end)
            if due_now or catch_up:
                row = dict(row)
                row["_due_path"] = "window" if due_now else "catchup"
                out.append(row)
        return out

    def tick(self) -> int:
        """一次检查：到点且引擎空闲 → 抢占计划槽位并启动批次。返回启动的队列数。

        引擎忙 → 整轮让路（手动批次优先），下个 tick 再看 —— 不排队不堆积。
        B1：启动前先 `claim_slot`（INSERT OR IGNORE），同一窗口实例只可能成功一次；
        启动抛异常时**不释放槽位**、本窗口不再重试（见下方定案注释）。
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
        now = self._now_fn()
        due = self.due_queues(now)
        launched = 0
        for row in due:
            specs = row.get("specs") or []
            if not specs:
                continue
            qid = int(row["id"])
            start = str(row.get("start_time") or "")
            end = str(row.get("end_time") or "")
            trigger = str(row.get("_due_path") or "window")
            slot_key, scheduled_for = window_slot(qid, start, end, now)
            run_id = f"{slot_key}:{trigger}"
            if not self._store.claim_slot(qid, slot_key, scheduled_for,
                                          trigger, run_id):
                continue  # 本窗口已被（别的 tick/补跑）抢占
            try:
                self._launch(specs)
            except Exception as e:  # noqa: BLE001 - 单个队列失败不拖垮其它队列
                # 失败**不释放槽位、不重试**：`mark_run("error: …")` 已把本窗口
                # 计为「今天跑过」（S-06 防 60s 重试风暴的既有语义）。B1 审查
                # （MAJOR-3）证实「release_slot 允许下个 tick 重试」与 mark_run
                # 互斥、且会造成每分钟一次失败风暴 —— 明确定案：本窗口不再重试，
                # 失败原因可从 last_status 与日志读取，槽位记录保留可审计。
                self._store.mark_run(qid, f"error: {e}")
                from .applog import warn as _warn
                _warn(f"定时队列「{row.get('name')}」启动失败：{e}")
                continue
            launched += 1
            from .applog import info as _info
            _info(f"定时队列「{row.get('name')}」已启动（{len(specs)} 个仓库）")
            try:
                # 启动后的登记独立于失败路径（R2 NIT-3）：登记失败不得把一次
                # 已发生的启动记成 error —— 槽位已在，本窗口也不会因此重跑。
                self._store.mark_run(qid, "dispatched")
                self._store.set_slot_state(qid, slot_key, "done")
            except Exception as e:  # noqa: BLE001 - 登记失败只降级可观测性
                from .applog import warn as _warn
                _warn(tr("定时队列「{}」已启动但登记失败：{}").format(
                    row.get("name"), e))
        if due:
            background_jobs.notify_run(JOB_SYNC_QUEUE,
                                       result=f"到点 {len(due)} 队列，启动 {launched}")
        return launched
