"""统一同步调度引擎：开始克隆 / 一键更新 / 并发调整 / 取消 / 断点续传。

设计目标：
- 把 MainWindow 里分散的池/任务/flags/进度逻辑收敛到一处，UI 只依赖本引擎。
- 去重：相同 folder_name 只同步一次，避免两个 worker 抢同一目录（日志实证的
  `Ephemeral-AI-Lab/layerfs` 并发竞态）。
- 进度安全：progress.json 由引擎内存聚合 + 周期落盘（单线程写），worker 不再
  并发写同一临时文件，杜绝 `PermissionError → finished 信号丢失 → UI 永久 busy`。
- 取消收敛：is_cancelled(key) 缓存替代 UI 每行遍历 flags 的 CPU 热点。
- finished 收敛：引擎内部计数 worker 完成数，全部完成才发 finished（不依赖
  “finished 信号逐个递减”，二次防线）。
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterable
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import QObject, QThreadPool, QTimer, pyqtSignal

from ..db.repo_db import Database, load_progress, save_progress
from ..git.service import GitService
from ..models import RepoSpec, SyncAction, SyncResult, SyncStatus
from ..util.redact import redact
from .batch_state import BatchState
from .errors import classify_error
from .event_bus import EventBus, EventType
from .mirrors import normalize_mirror_prefix
from .worker import CancelFlag, CloneWorker, TaskPayload

# G43-1②：git 输出行合并窗口（毫秒）与立即冲刷阈值（行数）
_LINE_WINDOW_MS = 250
_LINE_FLUSH_THRESHOLD = 200
# G43-5：progress / progress_detail 心跳批量 emit 间隔（毫秒）
_PROGRESS_HEARTBEAT_MS = 100
# G43-2：批次流控首批入队上限的**硬上限**。实际生效值由 `batch_start_limit()`
# 按线程池上限派生（C1-8/Rev7 §4 C-12）—— 原实现恒用 64，而线程池上限最多 32，
# 于是最多 63 个 worker 各持一个 GitService 在池里干等，内存与"取消后多久真停"都被放大。
_BATCH_START_LIMIT = 64



def _redact_result(res: SyncResult) -> SyncResult:
    """G80-B1：结果落库/上屏前的**唯一**打码点。

    为什么在引擎而不是在 worker：worker 里 `res.message/detail` 由 git 输出拼成，
    其中可能含 `https://user:pass@host`、`Authorization: ...`、`ghp_xxx`；
    这些文本随后会进 DB（→ /api/report、CSV/xlsx、MCP 导出）与 UI 重试面板。
    在唯一结果汇聚点打码一次，比在每个出口各打一次可靠（新增出口不会漏）。

    非字符串值原样返回（redact 对非 str 是恒等函数）。
    """
    res.message = redact(res.message)
    res.detail = redact(res.detail)
    return res


class SyncEngine(QObject):
    """统一的并行同步调度器。"""

    line = pyqtSignal(int, str, str)        # index, text, level
    progress = pyqtSignal(int, str)         # index, percent
    progress_detail = pyqtSignal(int, str)  # index, "rate files" 附加文本（速率/对象数）
    result = pyqtSignal(int, SyncResult)    # index, result
    adapt_changed = pyqtSignal(int, str)     # G48-1 并发自适应：当前并发, 说明
    finished = pyqtSignal()                 # 全部 worker 完成（成功/失败/取消都算）
    _arm_flush = pyqtSignal()               # G43-1②：worker 线程请求主线程武装合并窗口定时器

    def __init__(self, root: Path, db: Optional[Database] = None,
                 progress_path: Optional[Path] = None,
                 concurrency: int = 8,
                 fetch_timeout: int = 300, clone_timeout: int = 600,
                 time_budget: float = 0.0, host_concurrency: int = 4,
                 retries: int = 2, proxy: str = "", token: str = "",
                 submodule: bool = False, rate_limit_kbps: int = 0,
                 host_tokens: Optional[dict[str, str]] = None,
                 mirror_prefix: Optional[dict[str, object]] = None,
                 mirror_use_presets: bool = False,
                 mirror_fallback: bool = True,
                 mirror_probe_timeout: float = 8.0,
                 host_auth_types: Optional[dict[str, str]] = None,
                 precheck_remote: bool = False, single_branch: bool = False,
                 force_ipv4: bool = False,
                 lfs_enabled: bool = False,
                 post_clone_hook: str = "",
                 ssh_key: str = "",
                 mirror: bool = False,
                 retry_queue_path: Optional[str | Path] = None,
                 retry_enabled: bool = False,
                 retry_max: int = 2,
                 retry_backoff_base_sec: int = 30,
                 session_store: Optional[Any] = None,
                 watchdog_enabled: bool = False,
                 watchdog_interval_min: int = 5,
                 progress_gc: Optional[Any] = None,
                 concurrency_state_path: Optional[str | Path] = None,
                 event_bus: Optional[EventBus] = None):
        super().__init__()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = db
        self.progress_path = progress_path
        self.pool = QThreadPool(self)
        # C1-7（Rev7 §4 C-25）：并发自适应状态必须在 `set_concurrency` **之前**就位 ——
        # `set_concurrency` 现在会同步这三个字段（否则用户改并发后被自适应顶回旧值）。
        self._adapt_max = max(1, min(32, int(concurrency)))
        self._adapt_current = self._adapt_max
        self._net_fail_streak = 0
        self._net_ok_streak = 0
        self.set_concurrency(concurrency)
        # G71-1：进程内事件总线（None = 不发布事件，保持旧测试/旧调用零变化）
        self._event_bus = event_bus
        self._batch_id = ""
        self._batch_counts: dict[str, int] = {}
        # G71-8：批次状态机（转换校验 + 计数唯一来源）
        self._batch_state = BatchState()

        self.fetch_timeout = fetch_timeout
        self.clone_timeout = clone_timeout
        self.time_budget = max(0.0, float(time_budget))
        self._host_concurrency = max(0, int(host_concurrency))
        self.retries = max(0, retries)
        self.proxy = (proxy or "").strip()
        self.token = (token or "").strip()
        self.host_tokens: dict[str, str] = dict(host_tokens or {})  # G38-1
        self.mirror_prefix: dict[str, tuple[str, ...]] = normalize_mirror_prefix(
            mirror_prefix)  # G38-2/G58-2
        self.mirror_use_presets = bool(mirror_use_presets)   # G58-2
        self.mirror_fallback = bool(mirror_fallback)         # G58-2
        self.mirror_probe_timeout = max(1.0, float(mirror_probe_timeout or 8.0))
        self.host_auth_types: dict[str, str] = dict(host_auth_types or {})  # G58-5 认证头类型
        self.precheck_remote = bool(precheck_remote)  # G38-3
        self.single_branch = bool(single_branch)      # G38-4
        self.force_ipv4 = bool(force_ipv4)            # G38-6
        self._submodule = bool(submodule)   # G08-1 透传给 GitService
        self._rate_limit_kbps = max(0, int(rate_limit_kbps or 0))  # G04-4 限速
        # G48 深化透传
        self._lfs = bool(lfs_enabled)
        self._post_clone_hook = (post_clone_hook or "").strip()
        self._ssh_key_global = (ssh_key or "").strip()
        self._mirror_launch = bool(mirror)
        # G48-1 并发自适应状态（`_adapt_max`/`_adapt_current`/streak 已在 pool 之后初始化）
        # G52-1 任务级自动重试队列（仅网络类错误；None 时行为与旧版一致）
        self._retry_queue = None
        if retry_queue_path and retry_enabled:
            from .retry_queue import RetryQueue
            self._retry_queue = RetryQueue(
                retry_queue_path, enabled=True,
                max_retries=int(retry_max), backoff_base_sec=int(retry_backoff_base_sec))
        # G52-2 跨会话续跑清单（None 时不启用）
        self.session_store = session_store if session_store is not None else None
        # G52-3 看门狗参数（由 MainWindow 创建 Watchdog；引擎只存参）
        self.watchdog_enabled = bool(watchdog_enabled)
        self.watchdog_interval_min = int(watchdog_interval_min)
        # G52-4 进度文件治理（None 时不启用；传入 progress_gc 模块才在批次末滚动/裁剪）
        self._progress_gc = progress_gc if progress_gc is not None else None
        # G52-6 并发自适应记忆（跨会话恢复上次并发；None 时不持久化）
        self._concurrency_state_path = (
            Path(concurrency_state_path) if concurrency_state_path else None)
        self._recent_batch_rates: list[float] = []
        self._batch_total = 0
        self._batch_net_fail = 0
        # G80-B7-2：在跑任务的开始时间（folder_name → ts）。用于识别"卡了远超超时"的任务。
        # 为什么需要：QThreadPool 的槽位只在 runnable 返回后才释放 —— 若 worker 卡在
        # 某个不会返回的调用里（历史上真出现过：`_pump` 读线程泄漏），槽位会被永久占住，
        # 表现为"并发数越跑越小、新任务一直排队"，但界面上看不出任何异常。
        self._inflight_at: dict[str, float] = {}
        self._stuck_reported: set[str] = set()
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(15000)
        self._retry_timer.timeout.connect(self._retry_tick)
        # 卡死巡检：30s 一次，只做检测与上报，不改并发
        self._stuck_timer = QTimer(self)
        self._stuck_timer.setInterval(30_000)
        self._stuck_timer.timeout.connect(self._check_stuck_tasks)
        # C1-9（Rev7 §4 C-13）：并发记忆的**恢复**与重试队列无关 —— 原实现把它关在
        # `if self._retry_queue is not None:` 里，而默认 `retry_enabled=False`，
        # 于是 `_persist_concurrency_state()`（批次结束**无条件**写）永远只写不读：
        # 上次被降到 4 并发，重启又回 32，再被限流一次。写入侧一直是对的，读侧被短路。
        if self._concurrency_state_path is not None:
            self._restore_concurrency_state()
        if self._retry_queue is not None:
            self._retry_timer.start()

        self.tasks: list[CloneWorker] = []
        self.flags: dict[int, CancelFlag] = {}
        self.specs: dict[int, RepoSpec] = {}
        self._host_by_key: dict[str, str] = {}
        # A5-5/R3：per-host 并发闸门。每 host 一个 Semaphore(host_concurrency)。
        # <=0 视为不限流。C1-1（Rev7 X-1）：此处原有 `self._host_concurrency = 4`
        # 覆盖构造参数，详见 `set_host_concurrency` docstring。
        self._host_gates: dict[str, threading.Semaphore] = {}
        # G67-3：上一批的启动上下文快照（host_by_key/fetch_depth/unshallow/mirror）。
        # 与 `_host_by_key` **分开保存** —— 后者会在 `_release_tasks()` 里被清空，
        # 而重投定时器可能在该批结束之后才到期，那时正是需要这份快照的时候。
        self._last_batch_ctx: dict[str, object] = {}
        self._depth = 0
        self._unshallow = False
        self._pending = 0
        # C1-8：在跑 worker 计数（流控补投判定）。必须在此初始化 —— 原实现只在
        # `launch` 里赋值，`set_concurrency`→`_top_up_pending` 若早于 launch 被调用
        # 就只能靠 getattr 兜底（审查意见）。
        self._running = 0
        # C2-6：批次级暂停（暂停=不再补投新任务，在途跑完即止；与「取消」不同）
        self._paused = False
        self.busy = False

        # 进度内存态 + 周期落盘（G22-2：in_progress/failed 全生命周期跟踪）
        self._progress: dict[str, dict] = self._normalize_progress(
            load_progress(self.progress_path) if self.progress_path
            else {"finished": [], "in_progress": {}})
        self._dirty = False
        self._skip_done = True          # 已完成且目录存在 → 直接跳过
        self._launch_clear_input = False

        # 取消缓存：key -> 是否已取消（避免 UI 每行输出遍历全部 flags）
        self._cancelled = False

        # 内存护栏：完成时释放对 worker 的强引用，避免 QRunnable 队列积压导致 OOM
        self._retired_tasks: list[CloneWorker] = []

        # G43-1②/G43-5：信号批聚合缓冲（worker 线程写入、主线程定时冲刷，锁保护）
        self._emit_lock = threading.Lock()
        self._line_buf: dict[int, list[tuple[str, str]]] = {}   # index -> [(text, level)]
        self._line_count = 0
        self._progress_buf: dict[int, str] = {}                 # index -> 最新 percent
        self._progress_detail_buf: dict[int, str] = {}          # index -> 最新附加文本
        self._line_flush_timer = QTimer(self)
        self._line_flush_timer.setSingleShot(True)
        self._line_flush_timer.setInterval(_LINE_WINDOW_MS)
        self._line_flush_timer.timeout.connect(self._flush_line_buf)
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(_PROGRESS_HEARTBEAT_MS)
        self._progress_timer.timeout.connect(self._flush_progress_buf)

        # G43-2：待发队列（>64 的超额 spec 缓存于此，完成一个补投一个）
        self._pending_queue: list[tuple[int, RepoSpec, CancelFlag]] = []

        self._arm_flush.connect(self._arm_line_flush)

    # ------------------------------------------------------------ 并发
    @property
    def retry_queue(self):
        """重试队列（供 UI「重试队列」面板读取；未启用时为 None）。"""
        return self._retry_queue

    def _publish(self, event_type: str, **data: Any) -> None:
        """发布事件（G71-1）。**永不抛**：事件总线故障不得影响同步主流程。"""
        bus = self._event_bus
        if bus is None:
            return
        try:
            payload = dict(data)
            if self._batch_id:
                payload.setdefault("batch_id", self._batch_id)
            bus.publish(event_type, payload)
        except Exception:
            pass

    def set_concurrency(self, n: int, by_user: bool = True) -> int:
        """设置线程池并发上限，返回实际生效值（1..32）。

        `by_user=True`（默认）：这是**用户显式选择**，同时抬高/压低自适应天花板
        `_adapt_max`，并把待发队列补到新上限 —— 否则用户把并发从 8 改到 3 之后，
        连续 3 次成功又被自适应顶回 8（C-25）；把 3 改回 8 时，待发队列也不会
        立刻补投（用户体感"改了没用"）。

        `by_user=False`：**引擎内部**的自适应调整（`_adapt_result`）与跨会话恢复
        （`_restore_concurrency_state`），只动线程池、不动天花板 —— 否则一次网络抖动
        把并发降到 1 就会永久压低天花板，"网络恢复后回升"永远不可能发生。
        """
        n = int(n)
        n = max(1, min(32, n))
        self.pool.setMaxThreadCount(n)
        if by_user:
            self._adapt_max = n
            self._adapt_current = n
            self._net_fail_streak = 0
            self._net_ok_streak = 0
            # 运行中调高并发 → 待发队列立刻补投（不需要等下一个任务完成）
            self._top_up_pending()
        return n

    def batch_start_limit(self) -> int:
        """首批入队上限 = min(硬上限, 线程池上限)。

        C1-8（Rev7 §4 C-12）：原实现恒用模块常量 64，而线程池上限最多 32 ——
        「流控」名义成立、实际无效：超出的 worker 已经构造完（各持一个 GitService
        与一份 env 字典）在 Qt 队列里干等，既占内存又拉长"取消后多久真停"。
        按池上限派生后，同时存活的 worker 数不会超过真正能跑的数量。
        """
        try:
            pool_max = int(self.pool.maxThreadCount())
        except Exception as e:  # noqa: BLE001 - 池异常退化为硬上限，但要留痕
            from . import applog
            applog.warn(f"读取线程池上限失败，流控退化为硬上限 "
                        f"{_BATCH_START_LIMIT}：{e}")
            pool_max = _BATCH_START_LIMIT
        return max(1, min(_BATCH_START_LIMIT, pool_max))

    @property
    def paused(self) -> bool:
        """当前是否处于「批次暂停」态（C2-6）。"""
        return bool(getattr(self, "_paused", False))

    def pause(self) -> bool:
        """暂停补投新任务（在途任务继续跑完）。返回是否真的进入暂停态。

        与 `cancel_all()` 的区别：暂停**不终止**任何在跑任务，只是不再从待发队列
        取新的；恢复后接着补投。用户挂机刷几百个仓库时最常要的就是这个中间档
        （原来只有"全部取消"）。
        """
        if self._cancelled or self._pending <= 0:
            return False
        if self._paused:
            return True
        self._paused = True
        self._publish(EventType.BATCH_PAUSED,
                      pending=len(getattr(self, "_pending_queue", []) or []),
                      running=int(getattr(self, "_running", 0)))
        return True

    def resume(self) -> bool:
        """恢复补投（立刻把待发队列补满到当前上限）。"""
        if not self._paused:
            return False
        self._paused = False
        self._publish(EventType.BATCH_RESUMED,
                      pending=len(getattr(self, "_pending_queue", []) or []))
        self._top_up_pending()
        return True

    def _top_up_pending(self) -> None:
        """把待发队列补投到当前上限（并发达上限/已取消/无排队时不做）。

        `_replenish()` 是它的别名（每完成一个任务调用一次 → 补满到当前上限）。
        `cancel_all()` 为待发项合成终态时**不会**走 `_start_worker`，因此那里的
        `_on_worker_done(count_running=False)` 不递减在跑计数。
        """
        if getattr(self, "_cancelled", False) or getattr(self, "_paused", False):
            return
        queue = getattr(self, "_pending_queue", None)
        if not queue:
            return
        limit = self.batch_start_limit()
        while queue and self._running_count() < limit:
            index, spec, flag = queue.pop(0)
            self._start_worker(index, spec, flag)

    def _running_count(self) -> int:
        """当前真正在跑的 worker 数（用于流控补投判定）。"""
        return max(0, int(getattr(self, "_running", 0)))

    # ------------------------------------------------------------ G52-6 并发记忆
    def _restore_concurrency_state(self) -> None:
        """启动时从 concurrency_state.json 恢复上次因**网络问题**降下来的并发。

        三条守卫（审查 C-1，都是真机可触发的坑）：

        ① **只在 `fail_rate > 0` 时恢复** —— 写入侧一直是无条件写的，若不加门控，
           一份陈旧的 `{"concurrency": 1, "fail_rate": 0}` 会把用户设置悄悄覆盖掉；
        ② **不低于天花板的一半** —— 否则一次抖动降到 1 会被永久记住；而 `_launch`
           每批都新建引擎、`_net_ok_streak` 随批清零，用户若按「一次 1~2 个仓库」
           的节奏用，永远攒不满 3 次成功 → 并发永久钉在 1，界面还显示得很正常；
        ③ **必须可观测**（applog + `adapt_changed`）—— 静默降并发等于用户看着
           「并行线程 32」而实际单线程跑。

        `by_user=False`：恢复的是「上次的当前值」，不动天花板 `_adapt_max`
        （天花板由用户本次设置决定）。
        """
        if self._concurrency_state_path is None or not self._concurrency_state_path.exists():
            return
        try:
            data = json.loads(self._concurrency_state_path.read_text(encoding="utf-8"))
            n = int(data.get("concurrency", 0))
            fail_rate = float(data.get("fail_rate", 0) or 0.0)
            if fail_rate <= 0 or not (1 <= n < self._adapt_max):
                return
            floor = max(1, (self._adapt_max + 1) // 2)
            n = max(n, floor)
            self.set_concurrency(n, by_user=False)
            self._adapt_current = n
            from . import applog
            applog.warn(f"已按上次网络状况临时降至 {n} 并发"
                        f"（设置上限 {self._adapt_max}；连续成功后自动回升）")
            self.adapt_changed.emit(n, f"已按上次网络状况临时降至 {n} 并发")
        except Exception as e:  # noqa: BLE001 - 状态文件损坏不该阻止启动
            from . import applog
            applog.warn(f"并发记忆恢复失败（按设置值启动）：{e}")

    def _persist_concurrency_state(self) -> None:
        """批次结束时持久化当前并发与近批失败率。"""
        if self._concurrency_state_path is None:
            return
        try:
            rate = self.batch_fail_rate
            data = {
                "concurrency": self._adapt_current,
                "fail_rate": float(rate) if rate is not None else 0.0,
                "window": len(self._recent_batch_rates),
                "total": self._batch_total,
                "failed": self._batch_net_fail,
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            # G71-2：统一原子写（唯一 tmp + fsync + 替换退避）。
            from ..util.atomic_io import write_json_atomic
            write_json_atomic(self._concurrency_state_path, data,
                              label="concurrency_state.json")
        except Exception:
            pass

    @property
    def batch_fail_rate(self):
        """近 N 批网络失败率（百分比）；无数据返回 None。"""
        if not self._recent_batch_rates:
            return None
        return sum(self._recent_batch_rates) / len(self._recent_batch_rates) * 100.0

    # ------------------------------------------------------------ G52-1 重试
    def _record_retry_failure(self, res) -> None:
        """失败结果（可重试类）入自动重试队列；成功由 _clear_retry_success 清理。"""
        if self._retry_queue is None:
            return
        try:
            key = f"{res.spec.owner}/{res.spec.repo}"
            outcome = self._retry_queue.record_failure_ex(
                res.spec, res.message or "", res.detail or "")
            if outcome.get("queued"):
                self._emit_line(
                    0,
                    f"已加入失败重试队列（{outcome.get('category')}，"
                    f"第 {outcome.get('attempts')} 次）：{res.spec.folder_name}",
                    "warn")
                self._publish(
                    EventType.RETRY_QUEUED, key=key,
                    folder_name=res.spec.folder_name,
                    display=res.spec.display,
                    attempts=int(outcome.get("attempts") or 0),
                    next_retry_at=float(outcome.get("next_retry_at") or 0),
                    category=outcome.get("category"),
                    code=outcome.get("code"),
                    title=outcome.get("title"),
                )
            elif outcome.get("reason") in ("exhausted", "non_retryable"):
                self._publish(
                    EventType.RETRY_DROPPED, key=key,
                    folder_name=res.spec.folder_name,
                    attempts=int(outcome.get("attempts") or 0),
                    category=outcome.get("category"),
                    code=outcome.get("code"),
                    title=outcome.get("title"),
                    reason=outcome.get("reason"),
                )
        except Exception:
            pass

    def _clear_retry_success(self, res) -> None:
        """成功/跳过/冲突 → 从重试队列移除（已解决）。

        A5-2/C-17：key 需与入队时一致（含 @ref），否则带 ref 的失败条目成功后永不清除。
        兼容旧条目：无 ref 形态也一并清理。
        """
        if self._retry_queue is None:
            return
        try:
            base = f"{res.spec.owner}/{res.spec.repo}"
            self._retry_queue.mark_success(
                base + (f"@{res.spec.ref}" if res.spec.ref else ""))
            if res.spec.ref:
                self._retry_queue.mark_success(base)  # 旧格式条目兜底清理
        except Exception:
            pass

    def run_due_retries(self) -> None:
        """公开入口：立即检查一次到期重试（UI「重试队列」面板用）。

        与 15s 轮询走同一条 `_retry_tick`，保留 busy/取消保护。
        """
        self._retry_tick()

    def configure_retry_queue(self, path: Optional[str | Path], enabled: bool,
                              max_retries: int = 2,
                              backoff_base_sec: int = 30) -> None:
        """G71-11（审查 R5）：运行中切换「失败自动重试」开关。

        引擎创建时若开关是关的，`_retry_queue` 为 None；用户中途打开后必须
        **就地创建队列并启动轮询**，否则面板会显示成功但永远不会重投。
        """
        if not enabled:
            if self._retry_queue is not None:
                self._retry_queue.set_enabled(False)
            return
        if self._retry_queue is None and path:
            from .retry_queue import RetryQueue
            self._retry_queue = RetryQueue(
                path, enabled=True, max_retries=int(max_retries),
                backoff_base_sec=int(backoff_base_sec))
            try:
                self._retry_timer.start()
            except Exception:
                pass
        elif self._retry_queue is not None:
            self._retry_queue.set_enabled(True)

    def _retry_tick(self) -> None:
        """引擎空闲时按退避到期自动重投（幂等：busy/取消/无到期 → 跳过）。"""
        if self._retry_queue is None or not self._retry_queue.enabled:
            return
        if self.busy or self._cancelled:
            return
        # 审查 C-2 的处理方式（重要，别退回硬拦截）：
        # 起初我在这里加过 `if not self._last_batch_ctx: return`，想挡住「空上下文重投
        # → host 列被写成 github.com」。但这会**关掉一个既有契约** ——
        # `tests/test_g67_closure.test_retry_tick_without_prior_batch_still_works`
        # 明确要求「快照为空时不得崩、仍要重投」（首次启动就有待重投队列是真实场景）。
        # 真正的根因不在重投，而在 host 的取值方式：原来盲 `or "github.com"`。
        # 现在改为 `_host_for()`（登记表 → 从 URL 解析 → 才回落），污染源已消除，
        # 因此这里保持原行为（允许无上下文重投）。
        try:
            # G72：单 tick 最多重投 50 条，避免几百个同时到期造成瞬时尖峰
            due = self._retry_queue.due_tasks(limit=50)
        except Exception:
            return
        if not due:
            return
        specs = []
        for rec in due:
            try:
                specs.append(self._retry_queue.to_spec(rec))
            except Exception:
                continue
        if specs:
            self._publish(EventType.RETRY_STARTED,
                          count=len(specs),
                          keys=[f"{s.owner}/{s.repo}" for s in specs])
            # G67-3 修复：重投必须复用**上一批的上下文**，否则：
            #   ① 不带 host_by_key → 非 GitHub 仓库落库成 host="github.com"，
            #      污染 UNIQUE(owner,repo,host)（同一仓库被当成两个）；
            #   ② 丢掉 fetch_depth/mirror → 重投出来的仓库克隆语义与用户所选不符
            #      （选「浅克隆」却按满量拉，或反之）。
            ctx = self._last_batch_ctx or {}
            try:
                self.launch(specs, check_existing=False, clear_input=False,
                            host_by_key=dict(ctx.get("host_by_key") or {}),
                            fetch_depth=int(ctx.get("fetch_depth") or 0),
                            unshallow=bool(ctx.get("unshallow")),
                            mirror=bool(ctx.get("mirror")),
                            session_merge=True)  # A5-2/C-18：不抹掉未投的失败项
            except Exception:
                pass

    # ------------------------------------------------------------ G52-4 进度 GC
    def _run_progress_gc(self) -> None:
        """批次完成（非取消）时滚动/裁剪 progress.json。"""
        if self._progress_gc is None or not self.progress_path:
            return
        try:
            stats = self._progress_gc.rotate_progress(self.progress_path)
            if stats.get("trimmed", 0):
                # 同步 engine 内存态：磁盘已裁剪，内存 finished 也截断到保留量，
                # 避免后续任何 flush_progress 把裁剪前的旧数据覆盖回磁盘。
                kept = int(stats.get("rows_kept", 0))
                if kept > 0 and len(self._progress.get("finished", [])) > kept:
                    self._progress["finished"] = self._progress["finished"][-kept:]
                    self._dirty = True
                msg = (f"进度文件已治理：归档 {stats.get('trimmed', 0)} 条，"
                       f"保留 {kept} 条")
                self._emit_line(0, msg, "info")
        except Exception:
            pass

    def _rebuild_search_index_if_due(self) -> None:
        """批次收尾：与 repos 行数不一致时重建 FTS 索引（C4-2）。失败只 warn。"""
        db = self.db
        fn = getattr(db, "ensure_fts", None)
        if not callable(fn):
            return
        try:
            n = int(fn() or 0)
        except Exception as e:      # noqa: BLE001 —— 索引重建失败不影响批次结果
            from . import applog
            applog.warn(f"全文索引重建失败（搜索降级为 LIKE）：{e}")
            return
        if n:
            from . import applog
            applog.info(f"全文索引已重建：{n} 条")

    def _prune_history_if_due(self) -> None:
        """批次收尾：让 DB 做一次同步历史容量治理（C3-2）。失败只 warn。"""
        db = self.db
        fn = getattr(db, "prune_history_if_due", None)
        if not callable(fn):
            return
        try:
            n = int(fn() or 0)
        except Exception as e:      # noqa: BLE001 —— 治理失败不该影响批次收尾
            from . import applog
            applog.warn(f"同步历史容量治理失败（不影响本批结果）：{e}")
            return
        if n:
            self._emit_line(0, f"同步历史已治理：删除 {n} 条过期记录", "info")

    def _maybe_checkpoint_wal(self) -> None:
        """批次收尾时做一次被动 WAL 收缩（C1-11 / Rev7 §4 C-24）。

        `PRAGMA wal_checkpoint(PASSIVE)` 只把已提交的 WAL 帧尽量写回主库，
        **不阻塞读者、不截断正在被读的帧、不丢数据**，所以放在收尾是安全的；
        但它有 IO 成本，不该每任务一次。

        失败绝不能影响批次收尾（数据库已被关、被其它进程独占都是真实场景），
        故整体吞掉并只记日志 —— 与 `_run_progress_gc` 同一取舍。
        """
        db = self.db
        if db is None or not hasattr(db, "wal_checkpoint"):
            return
        try:
            db.wal_checkpoint()
        except Exception as e:  # noqa: BLE001 - WAL 收缩失败不得拖垮批次收尾
            from . import applog
            applog.warn(f"WAL 收缩失败（不影响本批结果）：{e}")

    # ------------------------------------------------------------ G52-2 会话
    def _session_record_pending(self, specs, merge: bool = False) -> None:
        if self.session_store is None or not specs:
            return
        try:
            self.session_store.replace(specs, merge=merge)
        except Exception:
            pass

    def _session_update_result(self, res) -> None:
        if self.session_store is None:
            return
        try:
            key = f"{res.spec.owner}/{res.spec.repo}"
            self.session_store.update_status(key, res.status.value, res.message or "")
        except Exception:
            pass

    def _session_mark_completed(self) -> None:
        if self.session_store is None:
            return
        try:
            self.session_store.mark_completed()
        except Exception:
            pass

    # ------------------------------------------------------------ 内存护栏
    def _retire_task(self, worker: CloneWorker):
        """worker 完成时把它的强引用转移出活跃列表并尽快释放。

        QThreadPool 只会在任务结束后自动 delete（autoDelete=True），但我们仍持有
        self.tasks 的强引用——若不及时清空，32 并发跑完仍会累积整个列表的
        CloneWorker/SyncResult/日志尾部，是长时 OOM 的来源之一。
        """
        try:
            if worker in self.tasks:
                self.tasks.remove(worker)
            self._retired_tasks.append(worker)
            # 只保留最近 32 个退役对象引用，其余交给 GC
            if len(self._retired_tasks) > 32:
                del self._retired_tasks[: len(self._retired_tasks) - 32]
        except Exception:
            pass

    @property
    def concurrency(self) -> int:
        return self.pool.maxThreadCount()

    # ------------------------------------------------------------ 进度
    @staticmethod
    def _normalize_progress(data: dict[str, dict]) -> dict[str, dict]:
        """G22-2：兼容旧 progress.json（可能缺 in_progress/failed 键）。"""
        data.setdefault("finished", [])
        data.setdefault("in_progress", {})
        data.setdefault("failed", [])
        return data

    def _mark_in_progress(self, keys: list[str]):
        """launch 时登记本轮待完成清单（崩溃后可恢复）。"""
        ip = self._progress.setdefault("in_progress", {})
        for k in keys:
            ip[k] = time.strftime("%Y-%m-%d %H:%M:%S")
        self._dirty = True

    def _emit_line(self, index: int, text: str, level: str = "info"):
        # G28-1：出口统一打码（git 报错行可能回显含凭据的 remote URL）
        text = redact(text)
        # G43-1②：写入合并窗口缓冲（250ms 或阈值 200 行冲刷），降低信号风暴
        with self._emit_lock:
            first = not self._line_buf
            self._line_buf.setdefault(index, []).append((text, level))
            self._line_count += 1
            over = self._line_count >= _LINE_FLUSH_THRESHOLD
        if over:
            self._flush_line_buf()          # 超阈值立即冲刷（可被任意线程调用）
        elif first:
            self._arm_flush.emit()          # 首批后请求主线程武装 250ms 单次定时器

    def _arm_line_flush(self):
        """主线程：武装 250ms 合并窗口定时器（缓冲非空且未运行才启动）。"""
        with self._emit_lock:
            if not self._line_buf or self._line_flush_timer.isActive():
                return
        self._line_flush_timer.start()

    def _flush_line_buf(self):
        """G43-1②：把缓冲的 git 输出行按 index 聚合并一次 emit（多行一行信号）。"""
        with self._emit_lock:
            if not self._line_buf:
                return
            buf, self._line_buf = self._line_buf, {}
            self._line_count = 0
        for idx, entries in buf.items():
            for text, level in entries:
                self.line.emit(idx, text, level)

    def flush_all_buffers(self):
        """G43-1②/G43-5：finished 前兜底冲刷，保证日志/进度全部送达后再发 finished。"""
        self._flush_line_buf()
        self._flush_progress_buf()

    def _stop_buffers(self):
        """G43-5：停止心跳/合并窗口定时器（幂等，收敛/关闭时调用）。"""
        try:
            self._progress_timer.stop()
        except Exception:
            pass
        try:
            self._line_flush_timer.stop()
        except Exception:
            pass

    def _emit_progress_detail(self, index: int, text: str):
        """G43-5：进度附加文本进入缓冲，由 100ms 心跳批量 emit。"""
        with self._emit_lock:
            self._progress_detail_buf[index] = text

    def _on_worker_progress(self, index: int, percent: str):
        """G43-5：worker 进度进入缓冲（保留最新值），心跳批量 emit。"""
        with self._emit_lock:
            self._progress_buf[index] = str(percent)

    def _on_worker_progress_detail(self, index: int, text: str):
        """G43-5：worker 进度详情进入缓冲（保留最新值），心跳批量 emit。"""
        with self._emit_lock:
            self._progress_detail_buf[index] = str(text)

    def _flush_progress_buf(self):
        """G43-5：心跳批量 emit 最新 progress / progress_detail（主线程）。"""
        with self._emit_lock:
            if not self._progress_buf and not self._progress_detail_buf:
                return
            prog, self._progress_buf = self._progress_buf, {}
            detail, self._progress_detail_buf = self._progress_detail_buf, {}
        for idx, pct in prog.items():
            self.progress.emit(idx, pct)
        for idx, text in detail.items():
            self.progress_detail.emit(idx, text)
        # G71-1：同一心跳把进度发布到事件总线（SSE 订阅者直接消费，无需轮询 DB）
        for idx in set(prog) | set(detail):
            spec = self.specs.get(idx)
            self._publish(
                EventType.TASK_PROGRESS,
                index=int(idx),
                key=(f"{spec.owner}/{spec.repo}" if spec is not None else ""),
                folder_name=(spec.folder_name if spec is not None else ""),
                percent=prog.get(idx),
                detail=detail.get(idx),
            )
        # A5-6/C-15（审查 R-1）：心跳同时兜底节流落盘 —— 任务级落盘被 2s 节流后，
        # 「缓冲已空但 dirty 未落」的窗口由这里补齐（否则进程被杀时 progress 陈旧）
        self._progress_flush_maybe()

    def flush_progress(self):
        """把内存进度态原子落盘（周期调用；engine 单线程写文件）。"""
        if not self.progress_path or not self._dirty:
            return
        try:
            save_progress(self.progress_path, self._progress)
            self._dirty = False
            self._last_flush_ts = time.time()
        except Exception:
            pass  # 落盘失败不致命，下次周期再试

    _PROGRESS_FLUSH_MIN_INTERVAL = 2.0  # A5-6/C-15：任务级落盘节流间隔（秒）

    def _progress_flush_maybe(self):
        """任务结束时的节流落盘：距上次落盘不足 2s 只标记 dirty，交给心跳兜底。

        心跳（_flush_progress_buf 每 100ms）末尾会调用本方法兜底；
        到点直接落盘，未到点保持 dirty 等下一跳。
        """
        if not self.progress_path or not self._dirty:
            return
        if time.time() - getattr(self, "_last_flush_ts", 0.0) >= \
                self._PROGRESS_FLUSH_MIN_INTERVAL:
            self.flush_progress()

    # ------------------------------------------------------------ G48-1 并发自适应
    def _adapt_result(self, status: SyncStatus, message: str) -> None:
        """G48-1：连续网络类失败 → 并发减半(下限 1)；连续成功 K 次 → 回升至设置上限。

        状态经 adapt_changed 信号通知 UI（状态栏提示当前并发）。
        """
        try:
            if status == SyncStatus.FAILED and \
                    classify_error(message or "").retryable:
                self._net_fail_streak += 1
                self._net_ok_streak = 0
                if self._net_fail_streak >= 3:
                    nxt = max(1, self._adapt_current // 2)
                    if nxt < self._adapt_current:
                        self._adapt_current = nxt
                        # by_user=False：自适应只动线程池，不动天花板（否则一次抖动
                        # 就把 `_adapt_max` 永久压低，"网络恢复后回升"不可能发生）
                        self.set_concurrency(nxt, by_user=False)
                        self.adapt_changed.emit(
                            nxt, f"网络抖动，并发已自动降至 {nxt}")
            elif status in (SyncStatus.SUCCESS, SyncStatus.SKIPPED):
                # 审查意见：只有真正成功/跳过才算「网络恢复」的证据。
                # 原实现的 else 分支把 CANCELLED（cancel_all 合成的终态）也算进来，
                # 会冒出一次假的「网络恢复，并发已回升」。
                self._net_ok_streak += 1
                self._net_fail_streak = 0
                if self._net_ok_streak >= 3 and self._adapt_current < self._adapt_max:
                    # 回升目标是**用户设置的**天花板 `_adapt_max`（C-25：用户把并发
                    # 从 8 改成 3 之后，这里必须回到 3 而不是 8）
                    target = self._adapt_max
                    self.set_concurrency(target, by_user=False)
                    self._adapt_current = target
                    self.adapt_changed.emit(
                        target, f"网络恢复，并发已回升至 {target}")
        except Exception:
            pass

    def _mark_done(self, key: str, status: SyncStatus, message: str):
        finished = self._progress.setdefault("finished", [])
        finished = [x for x in finished if x.get("key") != key]
        finished.append({
            "key": key,
            "status": status.value,
            "message": message,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        })
        self._progress["finished"] = finished
        self._dirty = True
        self._adapt_result(status, message)

    # ------------------------------------------------------------ 取消
    def cancel_all(self):
        self._cancelled = True
        for flag in self.flags.values():
            flag.cancel()
        # G43-2：待发队列清空且不再补投（逐个合成 CANCELLED 终态，in_progress 收敛）
        pending, self._pending_queue = self._pending_queue, []
        for index, spec, flag in pending:
            try:
                flag.cancel()
            except Exception:
                pass
            res = SyncResult(spec=spec, status=SyncStatus.CANCELLED,
                             action=SyncAction.CANCELLED, message="已取消")
            self._on_result(index, res)
            # count_running=False：这些项从未 _start_worker（没递增过在跑计数）
            self._on_worker_done(count_running=False)

    def pause_task(self, index: int) -> bool:
        """暂停单个任务：只取消目标 worker 的 flag，其余任务继续（G02-3）。

        返回是否成功定位到该任务。
        """
        flag = self.flags.get(index)
        if flag is not None:
            try:
                flag.cancel()
                # 同步：若该任务对应 worker 的取消回调绑定的是全局状态，
                # 这里直接把目标 worker 的 payload.flag 置为已取消即可被 worker 感知
                for w in self.tasks:
                    if w.index == index:
                        w.payload.flag.cancel()
                return True
            except Exception:
                pass
        # 兼容：index 可能对应 tasks 中的位置
        try:
            if 0 <= index < len(self.tasks):
                self.tasks[index].payload.flag.cancel()
                return True
        except Exception:
            pass
        return False

    def is_cancelled(self, key: str = "") -> bool:
        """全局取消状态。key 未用（当前为全量取消语义），保留参数便于后续逐仓库取消。"""
        return self._cancelled

    # ------------------------------------------------------------ 调度
    @staticmethod
    def _dedupe_specs(specs: Iterable[RepoSpec]) -> OrderedDict[str, list[RepoSpec]]:
        """按目标目录（folder_name 或 local_path）去重，返回 key -> [spec]。

        F5：folder_name 已把 @tag 编码进去（owner__repo@v1 vs @v2 目录不同），
        直接用目标目录键即可天然区分不同 tag。
        """
        groups: OrderedDict[str, list[RepoSpec]] = OrderedDict()
        for s in specs:
            key = s.local_path or s.folder_name  # 目标目录键（含 @tag 后缀）
            groups.setdefault(key, []).append(s)
        return groups

    def _service(self) -> GitService:
        # fetch_depth/unshallow 同步给 service：让 worker（worker.py 不受允许改动）不因
        # 深度差异重建 GitService，从而避免重建时丢掉 token（token 仅在本注入点透传）。
        svc = GitService(
            self.root,
            on_line=lambda c: self._emit_line(0, c.text, c.level),
            cancelled=lambda: self.is_cancelled(),
            fetch_timeout=self.fetch_timeout,
            clone_timeout=self.clone_timeout,
            time_budget=getattr(self, "time_budget", 0.0),
            retries=self.retries,
            proxy=self.proxy,
            token=self.token,
            fetch_depth=self._depth if self._depth else 0,
            unshallow=self._unshallow,
            submodule=getattr(self, "_submodule", False),
            rate_limit_kbps=getattr(self, "_rate_limit_kbps", 0),
            lfs_enabled=getattr(self, "_lfs", False),
            mirror=getattr(self, "_mirror_launch", False),
            post_clone_hook=getattr(self, "_post_clone_hook", ""),
            ssh_key=getattr(self, "_ssh_key_global", ""),
            host_tokens=getattr(self, "host_tokens", None) or {},  # G38-1
            mirror_prefix=getattr(self, "mirror_prefix", None) or {},  # G38-2
            mirror_use_presets=getattr(self, "mirror_use_presets", False),  # G58-2
            mirror_fallback=getattr(self, "mirror_fallback", True),        # G58-2
            mirror_probe_timeout=getattr(self, "mirror_probe_timeout", 8.0),  # G58-2
            host_auth_types=getattr(self, "host_auth_types", None) or {},  # G58-5
            precheck_remote=getattr(self, "precheck_remote", False),  # G38-3
            single_branch=getattr(self, "single_branch", False),  # G38-4
            force_ipv4=getattr(self, "force_ipv4", False),  # G38-6
        )
        # 进度附加文本（速率/对象数）注入点：解析 git 行并统一经引擎信号转发到 UI
        svc.send_progress_detail = self._emit_progress_detail
        return svc

    def _repo_ssh_key(self, spec: RepoSpec, host: str) -> str:
        """查该仓库的 SSH 私钥**路径**（G80-B8）；无 db / 查不到 / 出错 → 空串（回落全局）。

        单独抽出来是为了可测：`_start_worker` 会真的起线程池，测试里不该为了查一个字段
        就把整个 worker 拉起来。
        """
        if self.db is None:
            return ""
        try:
            return self.db.get_repo_ssh_key(spec.owner, spec.repo, host) or ""
        except Exception as e:  # noqa: BLE001 - 读失败不能阻断整批，降级全局 key
            from . import applog
            applog.warn(f"读取仓库级 SSH key 失败（降级全局）：{e}")
            return ""

    def _worker_service(self, flag: CancelFlag, ssh_key: str = "") -> GitService:
        """为单个 worker 构造带「单任务取消」回调的 service（G02-3）。

        每个 worker 独占一个 service 实例，cancelled 同时反映「全局取消」与
        该 worker 的 flag——避免共享同一 service 时并发改 cancelled 的竞态。
        """
        svc = self._service()
        global_cancelled = self.is_cancelled
        svc.cancelled = lambda: global_cancelled() or flag()
        if ssh_key:
            # G80-B8：仓库级 SSH 私钥**按任务**覆盖全局 `settings.ssh_key_path`。
            # 优先在任务粒度覆盖，而不是共享 service —— 同批里不同仓库可以用不同 key。
            svc._ssh_key = ssh_key
        return svc

    def _precheck_skip(self, spec: RepoSpec) -> Optional[tuple]:
        """断点续传预检：progress 中已完成且目标目录存在 → 返回 (status, message)。

        返回 None 表示需要真正同步。
        """
        if not self._skip_done or self.progress_path is None:
            return None
        key = f"{spec.owner}/{spec.repo}"
        for item in self._progress.get("finished", []):
            if item.get("key") == key:
                target = Path(spec.local_path or self.root / spec.folder_name)
                if target.exists() and (target / ".git").exists():
                    return (SyncStatus.SUCCESS, f"已完成，跳过（{item.get('message','')}）")
                break  # 同 key 上有新记录但目录不在 → 需重新同步
        return None

    def launch(self, specs: Iterable[RepoSpec], host_by_key: Optional[dict[str, str]] = None,
               fetch_depth: int = 0, unshallow: bool = False,
               clear_input: bool = False, check_existing: bool = True,
               mirror: bool = False, session_merge: bool = False) -> int:
        """启动一组仓库的并行同步。返回实际调度数量（去重后）。

        - specs: 待同步仓库列表（可含重复，内部去重）
        - host_by_key: {'owner/repo': host}，用于落库保留来源 host
        - fetch_depth/unshallow: 浅克隆语义
        - clear_input: 全部完成后是否需要 UI 清空输入框（engine 只透传标志，由 UI 处理）
        - check_existing: 断点续传预检（已完成且目录在 → 跳过）
        - session_merge: A5-2/C-18 —— 重投路径传 True，会话清单只合入新 key
          不整体替换（否则 due_tasks(limit) 截断的失败项从续跑清单被抹掉）
        """
        if self.busy:
            return 0
        self._cancelled = False
        self._mirror_launch = bool(mirror)  # G48-6 本次批次镜像模式
        self._clear_input = clear_input
        self._skip_done = check_existing

        # G22-1：先释放上轮可能滞留的旗标/列表引用，避免累积
        try:
            self.tasks.clear()
            self.flags.clear()
            self.specs.clear()
        except Exception:
            pass

        groups = self._dedupe_specs(specs)
        # 展开去重后的唯一 spec 列表
        unique_specs: list[RepoSpec] = []
        dup_keys: list[str] = []
        for key, grp in groups.items():
            if not grp:
                continue
            first = grp[0]
            if len(grp) > 1:
                dup_keys.append(key)
            # 断点续传预检
            if not first.is_local:
                pre = self._precheck_skip(first)
                if pre is not None:
                    self._emit_line(len(unique_specs), f"{first.display}：{pre[1]}", "info")
                    # A5-2/C-16：key 统一 owner/repo —— 原实现 `pre[0] and first.display
                    # or key` 恒传 display（pre[0] 恒真），与 _precheck_skip 的
                    # f"{owner}/{repo}" 匹配键不一致 → 续跑预检永不命中
                    self._mark_done(key, pre[0], pre[1])
                    continue
            unique_specs.append(first)
        for k in dup_keys:
            self._emit_line(0, f"重复仓库已去重，仅同步一次：{k}", "warn")

        if not unique_specs:
            # G43-1②：提前完成也要先冲刷缓冲（去重/跳过提示可能挂在缓冲里）
            self.flush_all_buffers()
            self.busy = False
            self.finished.emit()
            return 0

        self.busy = True
        # G80-B7-2：批次开始 → 启动卡死巡检（30s 一次，只上报不回收槽位）
        self._inflight_at.clear()
        self._stuck_reported.clear()
        try:
            self._stuck_timer.start()
        except Exception as e:  # noqa: BLE001 - 定时器不可用不该阻断克隆
            from . import applog
            applog.warn(f"卡死巡检定时器启动失败：{e}")
        self.tasks = []
        self.flags = {}
        self.specs = {}
        self._host_by_key = host_by_key or {}
        self._depth = fetch_depth
        self._unshallow = unshallow
        # G67-3：快照本批上下文供 `_retry_tick` 重投复用（见 `_last_batch_ctx` 注释）
        self._last_batch_ctx = {
            "host_by_key": dict(host_by_key or {}),
            "fetch_depth": fetch_depth,
            "unshallow": unshallow,
            "mirror": bool(mirror),
        }
        self._pending = len(unique_specs)
        # G52-6：重置批次计数（近批失败率窗口）
        self._batch_total = len(unique_specs)
        self._batch_net_fail = 0
        # G71-1：新批次 ID + 事件计数（SSE/诊断包按批次聚合）
        self._batch_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        self._batch_counts = {
            s.value: 0 for s in SyncStatus
        }
        # G71-8：状态机登记全部任务（queued），后续 started/result 只做转换。
        # G71-11（审查 R2）：key 必须用 **folder_name**（含 @tag），不能用
        # owner/repo —— 同一仓库的 @v1 与 @v2 同批存在时会互相覆盖计数，
        # 导致 terminal < total、invalid_transitions 增加。
        self._batch_state = BatchState(total=len(unique_specs))
        for spec in unique_specs:
            self._batch_state.register(spec.folder_name)
        self._batch_counts = dict(self._batch_state.counts)
        self._publish(
            EventType.BATCH_STARTED,
            total=len(unique_specs),
            root=str(self.root),
            fetch_depth=int(fetch_depth or 0),
            unshallow=bool(unshallow),
            mirror=bool(mirror),
            concurrency=int(self.concurrency),
        )
        for i, spec in enumerate(unique_specs):
            self._publish(
                EventType.TASK_QUEUED,
                index=i, key=f"{spec.owner}/{spec.repo}",
                folder_name=spec.folder_name, display=spec.display,
                url=spec.url_https, host=self._host_by_key.get(
                    f"{spec.owner}/{spec.repo}", "github.com"),
            )
        # G52-2：launch 时会话清单整体替换为 pending（供续跑/看门狗）；
        # A5-2/C-18：重投路径 session_merge=True → 只合入新 key（见 replace 文档）
        self._session_record_pending(unique_specs, merge=bool(session_merge))
        # G22-2：登记本轮待完成清单并立即落盘（进程被杀后可据此恢复）
        self._mark_in_progress([f"{s.owner}/{s.repo}" for s in unique_specs])
        self.flush_progress()
        self._emit_line(0, f"开始并行同步 {len(unique_specs)} 个仓库"
                           f"（{'浅克隆 depth=' + str(fetch_depth) if fetch_depth else '满量'}）",
                         "system")

        # G43-2 批次流控：只入队前 `batch_start_limit()` 个（= 线程池上限，C1-8），
        # 其余进待发队列，完成一个惰性补投一个。
        # flags/specs 仍登记全部（取消缓存与落库 host 查询语义不变）；进度表全部行
        # 仍由 UI 在 launch 后按 specs 预填，PENDING 态等待。
        start_limit = self.batch_start_limit()
        self._running = 0
        self._paused = False       # 新批次从「未暂停」开始（暂停是批次内状态）
        # 新批次取代旧批次的待发队列（旧批次若被取消，残留项不应混进本批）
        self._pending_queue = []
        for i, spec in enumerate(unique_specs):
            flag = CancelFlag()
            self.flags[i] = flag
            self.specs[i] = spec
            if i < start_limit:
                self._start_worker(i, spec, flag)
            else:
                self._pending_queue.append((i, spec, flag))
        # G43-5：进度心跳定时器（主线程批量 emit）
        self._progress_timer.start()
        return len(unique_specs)

    def _host_for(self, spec: RepoSpec) -> str:
        """该仓库的 host（落库 / 事件 / per-host 闸门共用）。

        取值顺序：**本批登记表 → 从 `url_https` 解析 → 最后才回落到 github.com**。

        为什么不能直接 `self._host_by_key.get(key) or "github.com"`（审查 C-2 根因）：
        重投路径与「启动期自动重投」下本批登记表是空的，盲回落会把 gitlab / gitee /
        自建主机的仓库**落库成 `host="github.com"`**，污染 `UNIQUE(owner,repo,host)`
        —— 同一仓库被当成两个，之后「一键更新」会指向错误来源。
        """
        key = f"{spec.owner}/{spec.repo}"
        host = (self._host_by_key.get(key) or "").strip()
        if host:
            return host
        raw = getattr(spec, "url_https", "") or getattr(spec, "url", "") or ""
        try:
            from .url_lib import host_of
            got = (host_of(str(raw)) or "").strip()
        except Exception as e:  # noqa: BLE001 - 解析失败按默认 host，但要留痕
            from . import applog
            applog.warn(f"解析仓库 host 失败（{raw}），按 github.com 处理：{e}")
            got = ""
        return got or "github.com"

    def _host_gate(self, host: str) -> Optional[threading.Semaphore]:
        """取（惰性建）某 host 的并发闸门。host_concurrency<=0 → None（不限流）。"""
        if self._host_concurrency <= 0:
            return None
        gate = self._host_gates.get(host)
        if gate is None:
            gate = threading.Semaphore(self._host_concurrency)
            self._host_gates[host] = gate
        return gate

    def set_host_concurrency(self, n: int) -> None:
        """运行中调整 per-host 并发上限（0 = 不限流）。

        **Rev7 X-1 修复记录**：`__init__` 里曾有一行 `self._host_concurrency = 4`
        无条件覆盖构造参数，导致 ①设置页/配置改值永不生效；②`0=不限流` 语义不可达；
        ③本方法只被 tests 调用（零生产调用点）—— 三者叠加即「可配置性伪装」。
        现在 C1-1 删掉了那行硬编码，C1-2 补了设置页控件（`ui/settings_network.py`）。

        注意：清空闸门后，已在跑的 worker 仍持旧闸门票（release 到旧实例），
        新 worker 拿新闸门 —— 切换瞬间两代闸门并存，实际并发可能短暂超过
        新上限（审查 R-2 已知取舍；设置只在启动前改最稳妥）。
        """
        self._host_concurrency = max(0, int(n))
        self._host_gates.clear()

    def _start_worker(self, index: int, spec: RepoSpec, flag: CancelFlag):
        """构造并启动单个 worker（launch 首批与 _replenish 补投共用）。"""
        key = f"{spec.owner}/{spec.repo}"
        host = self._host_for(spec)
        # C1-8：在跑计数（流控补投判定用）。与真实 worker 的 finished 严格配对
        # （`_on_worker_done(count_running=True)`）；取消时为待发项合成的终态走
        # `count_running=False` —— 那些项从未在此递增过，不能递减。
        self._running = self._running_count() + 1
        # A5-5/R3：per-host 并发闸门 —— 同一 host 的在跑任务数受 host_concurrency
        # 限制（默认 4）。300 仓全指 github.com × 全局并发 32 会成片触发
        # secondary rate limit；限流后成片失败变整体减速。acquire 在 worker
        # 线程内完成（不占 GUI 线程），release 在 worker 结束时（finally）。
        self._batch_state.transition(spec.folder_name, "running")
        # G71-8：queued → running（状态机对重复/迟到转换是幂等的）
        # G71-11：与登记 key 一致，使用 folder_name（含 @tag）
        self._publish(EventType.TASK_STARTED,
                      index=index, key=key,
                      folder_name=spec.folder_name, display=spec.display,
                      url=spec.url_https, host=host)
        payload = TaskPayload(spec=spec, flag=flag, host=host)
        # G80-B7-2：记开始时间（key 用 folder_name，与 batch_state 一致）
        self._inflight_at[spec.folder_name] = time.time()
        # G80-B8：仓库级 SSH 私钥（只存路径）。查不到 → 空串 → 走全局设置。
        repo_ssh_key = self._repo_ssh_key(spec, host)
        # G02-3: 每个 worker 独占一个 service（带自身 flag 的取消回调），
        # 支持单任务暂停，其余 worker 互不影响
        service = self._worker_service(flag, ssh_key=repo_ssh_key)
        # worker 只负责 git 同步与结果回传；DB 写入与 progress 落盘统一由引擎
        # 在 _on_result / flush 处理，避免多 worker 并发写同一 SQLite 连接与进度文件
        worker = CloneWorker(index, payload, service, None, None,
                             on_line=lambda c, i=index: self._emit_line(i, c.text, c.level),
                             fetch_depth=self._depth if self._depth else 0,
                             unshallow=self._unshallow,
                             host_gate=self._host_gate(host))
        worker.signals.line.connect(self.line.emit)
        # G43-5：progress/progress_detail 聚合进缓冲，由 100ms 心跳批量 emit
        worker.signals.progress.connect(self._on_worker_progress)
        worker.signals.progress_detail.connect(self._on_worker_progress_detail)
        worker.signals.result.connect(self._on_result)
        worker.signals.finished.connect(self._on_worker_done)
        self.tasks.append(worker)
        self.pool.start(worker)

    def stuck_threshold_sec(self) -> float:
        """判定"疑似卡死"的阈值：clone 超时 + 120s 余量（可被测试覆盖）。"""
        return float(self.clone_timeout) + 120.0

    def _check_stuck_tasks(self) -> None:
        """巡检在跑任务：超过阈值仍未结束 → 发一次 task_stuck 事件 + 记日志。

        **只报告，不回收槽位**：回收会让实际并发临时超过 `maxThreadCount`，
        而"是否接受这个风险"应当由用户显式决定（参考 river 的 `AddWorkerSlot`，
        它同样把这一点标注为 dangerous）。本版不做回收，避免用静默超发换"看起来没卡"。
        """
        now = time.time()
        threshold = self.stuck_threshold_sec()
        for key, started in list(self._inflight_at.items()):
            if key in self._stuck_reported:
                continue
            age = now - float(started or now)
            if age < threshold:
                continue
            self._stuck_reported.add(key)
            try:
                self._publish(EventType.TASK_STUCK, key=key,
                              elapsed_ms=int(age * 1000),
                              threshold_ms=int(threshold * 1000),
                              message=(f"任务超过 {int(threshold)}s 未结束，疑似卡死"
                                       "（槽位仍被占用；可取消整批后重试）"))
            except Exception as e:  # noqa: BLE001 - 巡检不能把引擎带崩
                from . import applog
                applog.warn(f"task_stuck 事件发布失败：{e}")
            from . import applog
            applog.warn(f"疑似卡死任务：{key}（已 {int(age)}s，阈值 {int(threshold)}s）"
                        "—— 可在「失败汇总」面板一键取消（C1-3）")

    def cancel_stuck_tasks(self) -> int:
        """用户显式动作：取消所有**已上报的**疑似卡死任务（G80-B7-2 补全）。

        与回收槽位的区别：不绕过 `maxThreadCount` —— 槽位要等 worker 真正退出
        （QThreadPool 语义）才释放；这里只是把 CancelFlag 置位 + 发事件，
        让 worker 在下一个检查点尽快收敛。**不静默超发**。
        返回：实际标记取消的任务数。
        """
        if not self._stuck_reported:
            return 0
        cancelled = 0
        for idx, spec in list(self.specs.items()):
            key = spec.folder_name
            if key not in self._stuck_reported:
                continue
            flag = self.flags.get(idx)
            if flag is not None:
                try:
                    flag.cancel()
                    cancelled += 1
                except Exception as e:  # noqa: BLE001 - 单个取消失败不拖垮其它
                    from . import applog
                    applog.warn(f"取消卡死任务失败（{key}）：{e}")
        if cancelled:
            from . import applog
            applog.warn(f"用户确认取消 {cancelled} 个疑似卡死任务（等待 worker 收敛后释放槽位）")
            try:
                self._publish(EventType.SYSTEM, message=f"已请求取消 {cancelled} 个卡死任务")
            except Exception:
                pass
        return cancelled

    def forget_inflight(self, key: str) -> None:
        """任务结束（任何终态）时清理记账。"""
        self._inflight_at.pop(key, None)
        self._stuck_reported.discard(key)

    def stuck_task_keys(self) -> list[str]:
        """当前已上报的疑似卡死任务（UI/诊断可用；测试也用它断言）。"""
        return sorted(self._stuck_reported)

    def _replenish(self):
        """G43-2：完成一个任务 → 从待发队列惰性补投。

        C1-8：改为「补满到当前上限」而不是「每次恰好补一个」—— 原实现下，
        用户运行中把并发从 3 调到 8，也只会按 3 的节奏一个一个补，永远到不了 8。
        """
        self._top_up_pending()

    # ------------------------------------------------------------ 回调
    def _on_result(self, index: int, res: SyncResult):
        # G80-B1：先打码再落库/上屏（唯一打码点）
        res = _redact_result(res)
        # G80-B7-2：任务到终态 → 清掉 in-flight 记账（不再参与卡死判定）
        self.forget_inflight(res.spec.folder_name)
        # 引擎统一落库（worker 不再写 DB，避免并发写同一 SQLite 连接）
        if self.db is not None:
            try:
                repo_id = self.db.upsert_repo(
                    res.spec, res.path,
                    # 审查 C-2 根因：原来盲回落 "github.com"，会把 gitlab/gitee 仓库
                    # 落库成 github.com（污染 UNIQUE(owner,repo,host)）
                    host=self._host_for(res.spec),
                    default_branch=None, head_sha=res.head_sha or None)
                self.db.add_sync_history(
                    repo_id, res.status, res.action, res.message, res.detail,
                    commits=res.commits, head_before="", head_after=res.head_sha,
                    remote_head=res.remote_sha, duration_ms=res.duration_ms,
                    # C3-4：分阶段耗时（precheck/transfer/checkout/hook）一起落库，
                    # 供"这批为什么慢"直接查，不必翻 git 原始日志
                    stages=getattr(res, "stages", None))
                # G81（N-6）：仓库画像落库。失败只记日志，不影响这条结果
                # （画像是展示用派生数据，绝不能让它把成功的同步变成失败）。
                if getattr(res, "profile", None) is not None:
                    try:
                        self.db.set_repo_profile(repo_id, res.profile)
                    except Exception as e:  # noqa: BLE001 - 画像写失败不该打断批次
                        from . import applog
                        applog.warn(
                            f"仓库画像落库失败（{res.spec.folder_name}）：{e}")
            except Exception as e:  # noqa: BLE001 - 落库失败不能打断整批，但必须留痕
                # G80-B2：原注释称「通过日志暴露」，实际什么都没记（app.log 里查不到）。
                # 落库失败 = 这条结果不会进历史/报表/统计，事后无法追溯，必须记下来。
                from . import applog
                applog.error(f"结果落库失败（{res.spec.folder_name}）：{e}")
        # 进度内存态登记
        self._mark_done(f"{res.spec.owner}/{res.spec.repo}", res.status, res.message)
        # G52-1：网络类失败入自动重试队列；成功/跳过/冲突移除
        if res.status == SyncStatus.FAILED:
            self._record_retry_failure(res)
        else:
            self._clear_retry_success(res)
        # G52-2：会话清单状态更新（成功/取消/跳过/冲突 → 移除；失败保留）
        self._session_update_result(res)
        # G71-1：结构化结果事件（agent/SSE 用；错误另发 task_error 携带三段式信息）
        try:
            _key = f"{res.spec.owner}/{res.spec.repo}"
            # G71-11（审查 R2）：状态机用 folder_name（含 @tag），事件 key 仍用 owner/repo
            self._batch_state.mark(res.spec.folder_name, res.status.value)
            # 兼容字段：旧调用方/测试仍读 `_batch_counts`
            self._batch_counts = dict(self._batch_state.counts)
        except Exception as e:  # noqa: BLE001 - 计数失败不影响单条结果展示，但计数会偏
            # G80-B2：批次计数错乱会让顶部「成功/失败」相互矛盾，必须可追溯
            from . import applog
            applog.warn(f"批次计数更新失败（{res.spec.folder_name}）：{e}")
        self._publish(
            EventType.TASK_RESULT,
            index=int(index),
            key=f"{res.spec.owner}/{res.spec.repo}",
            folder_name=res.spec.folder_name,
            display=res.spec.display,
            status=res.status.value,
            action=res.action.value,
            message=res.message or "",
            detail=res.detail or "",
            commits=int(res.commits or 0),
            duration_ms=int(getattr(res, "duration_ms", 0) or max(
                0.0, (res.ended or 0.0) - (res.started or 0.0)) * 1000),
            path=res.path or "",
            head_sha=res.head_sha or "",
            remote_sha=res.remote_sha or "",
            # G80-B4：检出状态（ok / missing / skip）。加性字段，老消费者忽略即可；
            # SSE / CLI --json / 诊断包都用同一份事件，因此只需在这里暴露一次。
            checkout_state=getattr(res, "checkout_state", "ok") or "ok",
            # G81（N-5）：取数通道与降级链（"" = 直连源站 / 全程常规克隆）。
            # 加性字段，只加不减；agent 可用它回答"为什么这次慢 / 历史为什么不完整"。
            mirror_used=getattr(res, "mirror_used", "") or "",
            degrade_path=getattr(res, "degrade_path", "") or "",
        )
        if res.status == SyncStatus.FAILED:
            try:
                info = classify_error(res.message or "", res.detail or "")
                self._publish(
                    EventType.TASK_ERROR,
                    index=int(index),
                    key=f"{res.spec.owner}/{res.spec.repo}",
                    folder_name=res.spec.folder_name,
                    category=info.category,
                    code=info.code,
                    title=info.title,
                    cause=info.cause,
                    action=info.action,
                    retryable=info.retryable,
                    http_status=info.http_status,
                    raw=info.raw,
                )
            except Exception as e:  # noqa: BLE001 - 事件发布永不阻塞/抛（EventBus 契约）
                from . import applog
                applog.warn(f"发布 task_error 事件失败（{res.spec.folder_name}）：{e}")
        # G52-6：批次失败计数（网络类失败计入近批失败率）
        if res.status == SyncStatus.FAILED:
            # G80-B2：整数自增不可能失败，原来的 try/except: pass 是纯噪声（去掉一个裸吞点）
            self._batch_net_fail += 1
        # G22-2：从待完成清单移除；失败项单独留档供「重试失败项」
        key = f"{res.spec.owner}/{res.spec.repo}"
        ip = self._progress.setdefault("in_progress", {})
        ip.pop(key, None)
        if res.status == SyncStatus.FAILED:
            failed = [x for x in self._progress.get("failed", []) if x.get("key") != key]
            failed.append({"key": key, "message": res.message,
                           "time": time.strftime("%Y-%m-%d %H:%M:%S")})
            self._progress["failed"] = failed
        self._dirty = True
        # 转发给 UI
        self.result.emit(index, res)

    def _on_worker_done(self, count_running: bool = True):
        # 计数含取消：取消也是终态，若不计入会导致 finished 信号永不收敛
        self._pending = max(0, self._pending - 1)
        # C1-8：在跑计数递减，与 `_start_worker` 的递增配对。
        # `count_running=False` 给「从未启动过的待发项」的合成终态（cancel_all）用。
        if count_running:
            self._running = max(0, self._running_count() - 1)
        # A5-6/C-15：每任务结束都全量 fsync 落盘 → 32 并发同帧完成时每帧 2 次
        # fsync（5-20ms）阻塞 GUI。改为节流：距上次落盘 <2s 只标记 dirty，
        # 由既有 100ms 心跳兜底落盘；批次收尾/launch 仍强制 flush。
        self._progress_flush_maybe()
        if self._pending == 0:
            # 全部完成：先冲刷日志/进度缓冲（保证 finished 前送达），再强制落盘一次
            self.flush_all_buffers()
            self.flush_progress()
            self.busy = False
            # G80-B7-2：批次结束 → 停巡检（不清 _stuck_reported：留给人看"这批有卡过"）
            try:
                self._stuck_timer.stop()
            except Exception as e:  # noqa: BLE001 - 停表失败不影响收尾
                from . import applog
                applog.warn(f"卡死巡检定时器停止失败：{e}")
            self._inflight_at.clear()
            # G52-2：非取消的整批完成 → 会话标记正常完成（不再续跑）
            if not self._cancelled:
                self._session_mark_completed()
            # G52-4：批次完成（非取消）→ 进度文件滚动/裁剪
            if not self._cancelled:
                self._run_progress_gc()
                # C3-2（Rev7 C-9）：同步历史容量治理挪到批次收尾（原来每 100 条历史触发
                # 一次全表窗口函数，10 万行实测 164ms，且发生在 GUI 线程）
                self._prune_history_if_due()
                # C4-2：全文索引是**派生**的，批次收尾重建一次（搜索是低频操作，
                # 不必给每次 upsert 加触发器）
                self._rebuild_search_index_if_due()
            # C1-11（Rev7 §4 C-24）：批次正常收尾 → 触发一次被动 WAL 收缩。
            # 为什么放这里而不是每任务：`wal_checkpoint(PASSIVE)` 不阻塞读者、不丢未提交
            # 数据，是安全操作；但每个任务一次会造成无谓 IO。此前该方法有实现、**零生产
            # 调用点**（只有测试），于是长挂机 + 未结束事务会让 WAL 涨到几十 MB 不收缩。
            self._maybe_checkpoint_wal()
            # G52-6：批次完成（非取消）→ 记录近批失败率并持久化并发记忆
            if not self._cancelled:
                try:
                    if self._batch_total > 0:
                        rate = self._batch_net_fail / self._batch_total
                        self._recent_batch_rates.append(rate)
                        if len(self._recent_batch_rates) > 8:
                            del self._recent_batch_rates[:-8]
                except Exception:
                    pass
                self._persist_concurrency_state()
            # G71-1：批次终态事件（含各状态计数与是否取消），供 SSE/诊断包收尾
            self._publish(
                EventType.BATCH_FINISHED,
                total=int(self._batch_total),
                cancelled=bool(self._cancelled),
                counts=dict(self._batch_counts),
                state=self._batch_state.summary(),
                net_fail=int(self._batch_net_fail),
            )
            self._batch_id = ""
            self.finished.emit()
            self._stop_buffers()
            self._release_tasks()  # 全部结束后集中释放 worker 引用防 OOM
        else:
            # G43-2：未全部完成 → 惰性补投一个待发任务
            self._replenish()

    def _release_tasks(self):
        """全部完成时清空活跃任务列表与 flags/specs 引用，降低长时运行内存占用。"""
        try:
            for w in self.tasks:
                self._retire_task(w)
            self.tasks.clear()
            self.flags.clear()
            self.specs.clear()
            self._host_by_key = {}
            self._pending_queue = []
            # C1-8：批次结束把在跑计数归零（防止极端情况下漏配对导致下一批补投停摆）
            self._running = 0
        except Exception:
            pass

    # ------------------------------------------------------------ 收尾
    def drain(self, timeout_ms: int = 8000) -> bool:
        """G22-1：取消全部任务并等待在跑 worker 收敛（优雅关闭）。

        返回是否在 timeout 内全部结束。等待期间泵 Qt 事件，
        保证 QRunnable 的跨线程 finished 信号能送达主线程计数。
        """
        self.cancel_all()
        deadline = time.time() + max(0.0, timeout_ms / 1000.0)
        pumped_warned = False
        while self._pending > 0 and time.time() < deadline:
            try:
                from PyQt6.QtCore import QCoreApplication
                app = QCoreApplication.instance()
                if app is not None:
                    app.processEvents()
            except Exception as e:  # noqa: BLE001 - 关闭路径不能抛
                # G80-B2：不再裸吞；只记一次，避免 20ms 轮询刷爆日志。
                if not pumped_warned:
                    from . import applog
                    applog.warn(f"drain 期间泵 Qt 事件失败（可能已无 QApplication）：{e}")
                    pumped_warned = True
            time.sleep(0.02)
        try:
            self.pool.waitForDone(200)
        except Exception:
            pass
        self.flush_all_buffers()  # G43-1②：drain 后冲刷残留缓冲
        try:
            self._retry_timer.stop()
        except Exception:
            pass
        return self._pending == 0

    def _disconnect_worker_signals(self) -> None:
        """断开 worker→engine 的全部信号（关闭/替换引擎前调用）。

        已排队的 QueuedConnection 事件无法撤回，但断开后新的跨线程投递不会再
        落到即将销毁的 QObject 上。
        """
        for w in list(getattr(self, "tasks", []) or []):
            for sig_name in ("result", "finished", "line", "progress",
                             "progress_detail"):
                try:
                    getattr(getattr(w, "signals", None), sig_name).disconnect()
                except Exception:
                    pass

    def discard(self) -> None:
        """**不泵事件循环**地让本引擎收敛，供「即将被替换掉」的场景使用（G69-13）。

        与 `shutdown()` 的区别在于**不调 `drain()`** —— `drain()` 为了接收跨线程
        `finished` 信号会 `processEvents()`，而 `discard()` 的调用点是 UI 槽
        （`MainWindow._launch` 里替换 `self.engine`），在里面泵事件会**重入** UI 槽
        （例如刚排队的 finished → `_on_engine_finished` → 触碰已被替换的控件），
        属于典型的 Qt 重入隐患。

        这里只做「让 C++ 层安静下来」这一件事：取消 → 等线程池排空 → 停定时器/缓冲。
        `pool.waitForDone(ms)` 会阻塞到 worker 真正结束，**这正是避免
        「Python 持 GIL 析构 QThreadPool ⟷ worker 需重取 GIL」死锁的关键**。
        """
        try:
            self.cancel_all()
        except Exception:
            pass
        try:
            self._retry_timer.stop()
        except Exception:
            pass
        # C1-6（Rev7 §4 C-6）：此前只停 `_retry_timer`，`_progress_timer`（100ms 心跳）
        # 与 `_stuck_timer`（30s 巡检）仍在跑 —— 每次 `_launch` 替换引擎都留下一对活
        # 定时器：旧引擎的心跳会把过期进度 emit 给已换过引擎的 UI，巡检还会持续
        # 往 app.log 写「疑似卡死」。`_stop_buffers()` 停心跳与行合并窗口，这里再把
        # 巡检停掉，三者才算真的安静。
        self._stop_buffers()
        try:
            self._stuck_timer.stop()
        except Exception as e:  # noqa: BLE001 - 停表失败不影响收敛（但要留痕）
            from . import applog
            applog.warn(f"discard 时卡死巡检定时器停止失败：{e}")
        self._disconnect_worker_signals()
        try:
            self.pool.waitForDone(5000)   # 阻塞式收敛：不泵事件、不死锁
        except Exception:
            pass
        try:
            self.pool.clear()
        except Exception:
            pass
        try:
            self.flush_progress()
        except Exception:
            pass

    def shutdown(self, drain_ms: int = 4000, wait_ms: int = 8000):
        """取消剩余任务并落盘，供窗口关闭时调用。

        G84-22：预算改为**可传参**。原来写死 `drain(4000)` + `waitForDone(8000)`
        —— 关窗时 GUI 线程最多阻塞 12s，用户看到的是"窗口卡死"，而不是"正在停止"。
        调用方（`MainWindow.closeEvent`）现在会先给出可见反馈再把预算收紧到
        `drain 1500 + wait 2500`（合计 ≤4s）；**下限不能太低**：取消后的 worker
        若不收敛就析构 QThreadPool，会以 `0xC0000409` 崩溃（G69-21 实测）。

        G67-22 修复：原实现 `cancel_all()` 之后**不等 worker 收敛**就清空
        `in_progress` 并落盘 —— 但在跑的 worker 稍后仍会回调 `_on_result`
        （取消也是终态），把 `in_progress[key]` 又写回去。于是
        「shutdown 后 in_progress 已清空」只在**快**机器上偶然成立；
        实测同一测试套件里，`_on_result` 晚到一步就会把上一个引擎的 key 留在
        `progress.json` 里，下次启动误报「上次未完成」。改为先等收敛
        （复用 `drain` 的等待语义）再清空 + 落盘。
        """
        self.cancel_all()
        # G52-1：窗口关闭停止重试轮询，避免残留定时器
        try:
            self._retry_timer.stop()
        except Exception:
            pass
        # G67-22：先等在跑 worker 到达终态（drain 内部会泵事件、等 pool），
        # 否则清空后会被晚到的 `_on_result` 重新写回。
        try:
            self.drain(drain_ms)
        except Exception:
            pass
        # G69-21：drain 超时也必须把线程池**真正等干净**再返回。
        # 原实现 drain 后的 `pool.waitForDone(200)` 只有 200ms：慢任务（>4s）仍在跑时，
        # 子进程/主窗口退出会在解释器析构 QThreadPool 的阶段崩溃 ——
        # 实测 `TestShutdownClearsInProgress` 子进程约 2/5 概率以
        # Windows `0xC0000409`（fail-fast）退出，且 stdout 全丢。
        # 先断开 worker→engine 的信号，避免退出期仍有跨线程投递到将销毁的 QObject；
        # 再阻塞等线程池排空（取消后通常毫秒级，这里给足 8s 上限）。
        self._disconnect_worker_signals()
        try:
            self.pool.waitForDone(wait_ms)
        except Exception:
            pass
        # G22-2：用户主动优雅退出时清空待完成清单，避免下次启动误报「上次未完成」
        try:
            self._progress["in_progress"] = {}
            self._dirty = True
        except Exception:
            pass
        self.flush_progress()
        self.flush_all_buffers()  # G43-1②/G43-5：关闭前送达残留缓冲
        self._stop_buffers()
        try:
            self.pool.clear()
        except Exception:
            pass

