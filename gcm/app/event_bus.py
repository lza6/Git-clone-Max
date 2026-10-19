"""G71-1 进程内事件总线：任务生命周期事件 → SSE / UI / 诊断包。

**为什么需要它**（对标参考项目 SurgeDM/Surge 的事件模型）：
主项目原先的三条出口各自为政 —— UI 走 Qt 信号、agent 走 `--json` 一次性快照、
HTTP API 只能轮询 `/api/repos`。三者都拿不到「队列里现在发生了什么」：
- UI 看不到任务在*进入线程池之前*的排队态（`_pending_queue` 只在引擎内部）；
- agent 想看实时进度只能反复轮询，且轮询会重复扫 DB；
- 出问题后没有一份「按时间排好序的事件流」可以附在诊断包里。

本模块只做一件事：**把引擎产生的生命周期事实发布成有序、已打码、可回放的事件**。
订阅方（SSE / 诊断包 / 测试）互不感知，也不允许反向影响引擎 —— 发布永不阻塞、
永不抛异常；订阅方队列满时丢弃最旧事件并计数（宁可丢事件，不能拖住克隆）。

设计约束：
- **线程安全**：worker 线程与主线程都会发布；订阅者可能在 HTTP 线程等待。
- **有界内存**：环形缓冲默认 500 条；每个订阅队列默认 1000 条。
- **出站打码**：所有字符串值在入缓冲前过 `redact()`，避免 token / URL 凭据
  经 SSE 泄露给本机其它进程（HTTP API 无鉴权，仅靠回环边界）。
- **可回放**：`subscribe(replay=N)` 先把最近 N 条补发给新订阅者，便于
  「页面打开前已经发生的事件」也能被看到。
"""
from __future__ import annotations

import json
import queue
import threading
import time
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any, Optional

from ..util.redact import redact

# 单条字符串事件字段的最大长度：防止某次 git 报错把整段日志塞进环形缓冲。
_MAX_FIELD_CHARS = 4000
# 嵌套深度上限：事件负载是本项目自己构造的，但保守处理，避免病态结构递归。
_MAX_DEPTH = 4


class EventType:
    """事件类型常量（同时作为 SSE 的 `event:` 名称）。

    命名与参考项目保持一致，便于 agent 侧用同一套映射：
    `queued` / `started` / `progress` / `result` / `error` / `system`。
    `task_*` 前缀用于区分「单任务」与「整批」。
    """

    BATCH_STARTED = "batch_started"
    TASK_QUEUED = "task_queued"
    TASK_STARTED = "task_started"
    TASK_PROGRESS = "task_progress"
    TASK_RESULT = "task_result"
    TASK_ERROR = "task_error"
    RETRY_QUEUED = "retry_queued"
    RETRY_STARTED = "retry_started"
    RETRY_DROPPED = "retry_dropped"
    BATCH_FINISHED = "batch_finished"
    SYSTEM = "system"

    ALL: frozenset[str] = frozenset({
        BATCH_STARTED, TASK_QUEUED, TASK_STARTED, TASK_PROGRESS,
        TASK_RESULT, TASK_ERROR, RETRY_QUEUED, RETRY_STARTED,
        RETRY_DROPPED, BATCH_FINISHED, SYSTEM,
    })


def _redact_value(value: Any, depth: int = 0) -> Any:
    """递归打码事件负载。

    只对**字符串**调用 `redact`（URL 内嵌凭据 / token / SSH key 会被掩码），
    数值与布尔原样保留；未知对象转成打码后的字符串，避免不可序列化对象
    在 SSE 写帧时才炸。
    """
    if depth > _MAX_DEPTH:
        return "<truncated>"
    if isinstance(value, str):
        return redact(value)[:_MAX_FIELD_CHARS]
    if isinstance(value, dict):
        return {str(k): _redact_value(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(v, depth + 1) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return redact(str(value))[:_MAX_FIELD_CHARS]


@dataclass(frozen=True)
class Event:
    """一条不可变事件。

    `seq` 是进程内单调递增序号（不是全局持久序号）：SSE 用它做 `id:`，
    订阅者可用它做「有没有丢事件」的连续性判断。
    """

    type: str
    seq: int
    ts: float
    data: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        # 先铺负载再覆盖元字段：即使调用方误传 seq/ts/type，也不能伪造事件序号。
        return {**self.data, "type": self.type, "seq": self.seq, "ts": self.ts}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))

    def sse_frame(self) -> bytes:
        """SSE 帧：id + event + data（data 必须是单行 JSON，不能换行）。"""
        return (f"id: {self.seq}\n"
                f"event: {self.type}\n"
                f"data: {self.to_json()}\n\n").encode()


class Subscription:
    """事件订阅句柄（上下文管理器）。`close()` 幂等。"""

    __slots__ = ("_bus", "_queue", "_types", "label", "_closed",
                 "dropped", "last_drop_ts", "received")

    def __init__(self, bus: EventBus, q: queue.Queue[Optional[Event]],
                 types: Optional[frozenset[str]], label: str = ""):
        self._bus = bus
        self._queue = q
        self._types = types
        self.label = label
        self._closed = False
        # G72：按订阅者暴露背压指标，便于定位是哪个消费者被拖慢。
        self.dropped = 0
        self.received = 0
        self.last_drop_ts = 0.0

    def matches(self, event_type: str) -> bool:
        return self._types is None or event_type in self._types

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def qsize(self) -> int:
        try:
            return int(self._queue.qsize())
        except Exception:
            return 0

    def wait(self, timeout: float = 1.0) -> list[Event]:
        """等待并取走当前可用事件；超时返回空列表。

        先阻塞等第一条，再非阻塞排空 —— SSE 写帧时自然成批，
        既不会空转，也不会让高频进度事件逐条唤醒。
        """
        if self._closed:
            return []
        out: list[Event] = []
        try:
            first = self._queue.get(timeout=max(0.0, float(timeout)))
        except queue.Empty:
            return []
        if first is None:          # 关闭哨兵
            self._closed = True
            return []
        out.append(first)
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is None:
                self._closed = True
                break
            out.append(item)
        return out

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._bus._unsubscribe(self)
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def __enter__(self) -> Subscription:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class EventBus:
    """有界、线程安全、可回放的事件总线。"""

    def __init__(self, maxlen: int = 500, max_queue: int = 1000,
                 progress_maxlen: int = 200):
        self._maxlen = max(1, int(maxlen))
        self._max_queue = max(1, int(max_queue))
        self._lock = threading.RLock()
        # G71-11（审查 S1）：进度事件单独缓冲 —— 32 并发 × 10Hz 会把 500 条
        # 环形缓冲 1.5 秒冲光，导致诊断包/快照看不到几分钟前的错误。
        # 非进度事件（批次/结果/错误/重试）只进 `_buffer`，进度只进 `_progress`。
        self._buffer: deque[Event] = deque(maxlen=self._maxlen)
        self._progress: deque[Event] = deque(maxlen=max(1, int(progress_maxlen)))
        self._subs: list[Subscription] = []
        self._seq = 0
        self._closed = False
        self._dropped = 0
        self._published = 0

    # ------------------------------------------------------------ 发布
    def publish(self, event_type: str, data: Optional[dict[str, Any]] = None) -> Optional[Event]:
        """发布事件；返回事件对象（总线关闭时返回 None）。

        **保证不抛**：这是引擎热路径（每次结果/进度都会调用），任何异常都不能
        让克隆流程失败。负载在入缓冲前完成打码与序列化安全化。
        """
        if not event_type:
            return None
        try:
            with self._lock:
                if self._closed:
                    return None
                self._seq += 1
                event = Event(
                    type=str(event_type),
                    seq=self._seq,
                    ts=time.time(),
                    data=_redact_value(dict(data or {})),
                )
                if event.type == EventType.TASK_PROGRESS:
                    self._progress.append(event)
                else:
                    self._buffer.append(event)
                self._published += 1
                subs = list(self._subs)
            for sub in subs:
                if not sub.matches(event.type):
                    continue
                self._enqueue(sub, event)
            return event
        except Exception:  # noqa: BLE001 —— 事件总线故障绝不影响主流程
            return None

    def _enqueue(self, sub: Subscription, event: Event) -> None:
        try:
            sub._queue.put_nowait(event)
            sub.received += 1
        except queue.Full:
            # 慢订阅者：丢最旧的，保证流仍然“活着”。计数供诊断查看。
            try:
                sub._queue.get_nowait()
                sub._queue.put_nowait(event)
                sub.received += 1
            except (queue.Empty, queue.Full):
                pass
            sub.dropped += 1
            sub.last_drop_ts = time.time()
            with self._lock:
                self._dropped += 1

    # ------------------------------------------------------------ 订阅
    def _items_locked(self, type_set: Optional[frozenset[str]]) -> list[Event]:
        """取用于回放/快照的事件（按 seq 升序）。

        `type_set=None` → 只返回非进度事件（黑匣子的默认视图）；
        显式请求 `task_progress` 时才合并进度缓冲。
        """
        items = list(self._buffer)
        if type_set is not None and EventType.TASK_PROGRESS in type_set:
            items += list(self._progress)
        if type_set is not None:
            items = [e for e in items if e.type in type_set]
        items.sort(key=lambda e: e.seq)
        return items

    def subscribe(self, replay: int = 0,
                  types: Optional[Iterable[str]] = None,
                  label: str = "") -> Subscription:
        """创建订阅；`replay` 为补发的最近事件条数（0 = 只收新事件）。"""
        type_set = None
        if types is not None:
            normalized = {str(t).strip() for t in types if str(t).strip()}
            type_set = frozenset(normalized) if normalized else None

        q: queue.Queue[Optional[Event]] = queue.Queue(maxsize=self._max_queue)
        sub = Subscription(self, q, type_set, label=label)
        with self._lock:
            if self._closed:
                sub._closed = True
                return sub
            self._subs.append(sub)
            items = self._items_locked(type_set)
            replay_n = max(0, min(int(replay or 0), len(items)))
            if replay_n:
                for ev in items[-replay_n:]:
                    try:
                        q.put_nowait(ev)
                    except queue.Full:
                        break
        return sub

    def _unsubscribe(self, sub: Subscription) -> None:
        with self._lock:
            try:
                self._subs.remove(sub)
            except ValueError:
                pass

    # ------------------------------------------------------------ 查询
    def oldest_seq(self, types: Optional[Iterable[str]] = None) -> int:
        """当前缓冲中最早事件的 seq（空缓冲 → 0）。"""
        type_set = None
        if types is not None:
            normalized = {str(t).strip() for t in types if str(t).strip()}
            type_set = frozenset(normalized) if normalized else None
        with self._lock:
            items = self._items_locked(type_set)
        return items[0].seq if items else 0

    def since(self, seq: int, limit: int = 500,
              types: Optional[Iterable[str]] = None) -> list[Event]:
        """返回 seq **之后**的事件（G72：SSE `Last-Event-ID` 精确续传）。"""
        try:
            after = int(seq)
        except (TypeError, ValueError):
            return []
        type_set = None
        if types is not None:
            normalized = {str(t).strip() for t in types if str(t).strip()}
            type_set = frozenset(normalized) if normalized else None
        with self._lock:
            items = self._items_locked(type_set)
        out = [e for e in items if e.seq > after]
        n = max(0, int(limit or 0))
        return out[-n:] if n else out

    def snapshot(self, limit: int = 100,
                 types: Optional[Iterable[str]] = None) -> list[Event]:
        """取最近事件（新→旧取再反转），供 `/api/events/snapshot` 与诊断包。

        默认（`types=None`）**不含** `task_progress`；需要进度时显式传
        `types=["task_progress"]` 或包含它的集合。
        """
        n = max(0, int(limit or 0))
        type_set = None
        if types is not None:
            normalized = {str(t).strip() for t in types if str(t).strip()}
            type_set = frozenset(normalized) if normalized else None
        with self._lock:
            items = self._items_locked(type_set)
        return items[-n:] if n else []

    def stats(self) -> dict[str, Any]:
        """总线健康度（订阅数 / 发布数 / 丢弃数），用于诊断。"""
        with self._lock:
            return {
                "subscribers": len(self._subs),
                "buffered": len(self._buffer) + len(self._progress),
                "buffered_events": len(self._buffer),
                "buffered_progress": len(self._progress),
                "published": self._published,
                "dropped": self._dropped,
                "closed": self._closed,
                "subscribers_detail": [
                    {"label": s.label, "qsize": s.qsize,
                     "received": s.received, "dropped": s.dropped,
                     "last_drop_ts": s.last_drop_ts, "closed": s.closed}
                    for s in self._subs
                ],
            }

    def close(self) -> None:
        """关闭总线：唤醒所有等待者并拒绝后续发布（进程退出/测试隔离用）。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            subs = list(self._subs)
            self._subs.clear()
        for sub in subs:
            sub._closed = True
            try:
                sub._queue.put_nowait(None)
            except queue.Full:
                pass


# 进程级单例：引擎、HTTP API、诊断包共享同一条事件流。
_GLOBAL_BUS = EventBus()


def get_event_bus() -> EventBus:
    return _GLOBAL_BUS


def reset_event_bus() -> EventBus:
    """替换全局总线（**仅测试**使用；生产代码不得调用）。

    不直接 close 旧总线：可能仍有订阅者在退出中，关闭动作会打乱它们的断言。
    """
    global _GLOBAL_BUS
    _GLOBAL_BUS = EventBus()
    return _GLOBAL_BUS


def iter_sse_frames(events: Iterable[Event]) -> Iterator[bytes]:
    """把事件序列转成 SSE 字节帧（供测试与 HTTP 层复用）。"""
    for ev in events:
        yield ev.sse_frame()
