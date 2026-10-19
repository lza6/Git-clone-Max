"""G71-8 批次状态机（纯逻辑，无 Qt / 无 IO）。

为什么单独抽出来：引擎里「任务从排队 → 运行 → 终态」的转换散落在
`launch` / `_start_worker` / `_on_result` / `cancel_all` 多处，计数靠几个
独立字段（`_batch_total` / `_batch_net_fail` / `_batch_counts`）手工维护，
任何一条提前返回路径漏记一次，UI 的「完成 x/y」就会永久对不上。

本模块只做两件事：
1. 定义**允许的转换**（queued → running → 终态；queued 可直接跳过/取消）；
2. 按 key 幂等记录终态，并给出计数摘要。

引擎用 `BatchState.mark()` 做防御性记账：非法转换不抛（生产路径不能被
状态机卡住），但会被计数到 `invalid_transitions`，供诊断/测试断言。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class TaskState:
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    CONFLICT = "conflict"

    TERMINAL = frozenset({SUCCESS, FAILED, CANCELLED, SKIPPED, CONFLICT})
    ALL = frozenset({QUEUED, RUNNING}) | TERMINAL


# 允许的转换：终态不可再变；queued 可直接到任意终态（预检跳过/取消/立即失败）。
_ALLOWED: dict[str, frozenset[str]] = {
    TaskState.QUEUED: frozenset({
        TaskState.RUNNING, TaskState.SUCCESS, TaskState.FAILED,
        TaskState.CANCELLED, TaskState.SKIPPED, TaskState.CONFLICT,
    }),
    TaskState.RUNNING: frozenset(TaskState.TERMINAL),
    TaskState.SUCCESS: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
    TaskState.SKIPPED: frozenset(),
    TaskState.CONFLICT: frozenset(),
}


@dataclass
class BatchState:
    """一个批次的纯状态账本。线程内使用（引擎主线程回调）。"""

    total: int = 0
    states: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    invalid_transitions: int = 0

    def register(self, key: str, state: str = TaskState.QUEUED) -> None:
        """登记一个任务（重复登记不覆盖已进入终态的记录）。"""
        if not key:
            return
        current = self.states.get(key)
        if current is None:
            self.states[key] = state
            self.counts[state] = self.counts.get(state, 0) + 1

    def transition(self, key: str, new_state: str) -> bool:
        """把任务推进到新状态；返回是否被接受。

        - 未登记的任务按 `queued` 补登记（引擎可能先收到结果、后补行）；
        - 同一终态重复上报 → 幂等接受（返回 True，不重复计数）；
        - 终态 → 其他状态 → 拒绝并计入 `invalid_transitions`。
        """
        if not key:
            return False
        if new_state not in TaskState.ALL:
            self.invalid_transitions += 1
            return False
        current = self.states.get(key)
        if current is None:
            self.register(key, TaskState.QUEUED)
            current = TaskState.QUEUED
        if current == new_state:
            return True
        if new_state not in _ALLOWED.get(current, frozenset()):
            self.invalid_transitions += 1
            return False
        self.counts[current] = max(0, self.counts.get(current, 0) - 1)
        self.states[key] = new_state
        self.counts[new_state] = self.counts.get(new_state, 0) + 1
        return True

    def mark(self, key: str, status: str) -> bool:
        """便捷入口：把 `SyncStatus.value` 映射到任务状态并推进。"""
        mapping = {
            "pending": TaskState.QUEUED,
            "running": TaskState.RUNNING,
            "success": TaskState.SUCCESS,
            "failed": TaskState.FAILED,
            "cancelled": TaskState.CANCELLED,
            "skipped": TaskState.SKIPPED,
            "conflict": TaskState.CONFLICT,
        }
        return self.transition(key, mapping.get(str(status), str(status)))

    @property
    def terminal(self) -> int:
        return sum(self.counts.get(s, 0) for s in TaskState.TERMINAL)

    @property
    def active(self) -> int:
        return self.counts.get(TaskState.QUEUED, 0) + \
            self.counts.get(TaskState.RUNNING, 0)

    def summary(self) -> dict[str, Any]:
        """给 UI / SSE / 诊断包的摘要（总数、终态数、各状态计数）。"""
        return {
            "total": int(self.total),
            "terminal": int(self.terminal),
            "active": int(self.active),
            "counts": dict(self.counts),
            "invalid_transitions": int(self.invalid_transitions),
        }
