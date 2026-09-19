"""统一同步调度引擎：开始克隆 / 一键更新 / 并发调整 / 取消 / 断点续传。

设计目标（对应 计划书/下一步改进指南.md 第四章）：
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

import threading
import time
from collections import OrderedDict
from collections.abc import Iterable
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, QThreadPool, QTimer, pyqtSignal

from ..db.repo_db import Database, load_progress, save_progress
from ..git.service import GitService
from ..models import RepoSpec, SyncAction, SyncResult, SyncStatus
from ..util.redact import redact
from .worker import CancelFlag, CloneWorker, TaskPayload

# 周期落盘间隔（秒）：期间完成的任务先聚在内存，到时统一写一次
_FLUSH_INTERVAL = 2.0

# G43-1②：git 输出行合并窗口（毫秒）与立即冲刷阈值（行数）
_LINE_WINDOW_MS = 250
_LINE_FLUSH_THRESHOLD = 200
# G43-5：progress / progress_detail 心跳批量 emit 间隔（毫秒）
_PROGRESS_HEARTBEAT_MS = 100
# G43-2：批次流控首批入队上限（>64 放入待发队列，完成一个惰性补投一个）
_BATCH_START_LIMIT = 64


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
                 retries: int = 2, proxy: str = "", token: str = "",
                 submodule: bool = False, rate_limit_kbps: int = 0,
                 host_tokens: Optional[dict[str, str]] = None,
                 mirror_prefix: Optional[dict[str, str]] = None,
                 precheck_remote: bool = False, single_branch: bool = False,
                 force_ipv4: bool = False,
                 lfs_enabled: bool = False,
                 post_clone_hook: str = "",
                 ssh_key: str = "",
                 mirror: bool = False):
        super().__init__()
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = db
        self.progress_path = progress_path
        self.pool = QThreadPool(self)
        self.set_concurrency(concurrency)

        self.fetch_timeout = fetch_timeout
        self.clone_timeout = clone_timeout
        self.retries = max(0, retries)
        self.proxy = (proxy or "").strip()
        self.token = (token or "").strip()
        self.host_tokens: dict[str, str] = dict(host_tokens or {})  # G38-1
        self.mirror_prefix: dict[str, str] = dict(mirror_prefix or {})  # G38-2
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
        # G48-1 并发自适应状态
        self._adapt_max = max(1, min(32, int(concurrency)))
        self._adapt_current = self._adapt_max
        self._net_fail_streak = 0
        self._net_ok_streak = 0

        self.tasks: list[CloneWorker] = []
        self.flags: dict[int, CancelFlag] = {}
        self.specs: dict[int, RepoSpec] = {}
        self._host_by_key: dict[str, str] = {}
        self._depth = 0
        self._unshallow = False
        self._pending = 0
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
    def set_concurrency(self, n: int) -> int:
        """设置线程池并发上限，返回实际生效值（1..32）。"""
        n = int(n)
        n = max(1, min(32, n))
        self.pool.setMaxThreadCount(n)
        return n

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

    def flush_progress(self):
        """把内存进度态原子落盘（周期调用；engine 单线程写文件）。"""
        if not self.progress_path or not self._dirty:
            return
        try:
            save_progress(self.progress_path, self._progress)
            self._dirty = False
        except Exception:
            pass  # 落盘失败不致命，下次周期再试

    # ------------------------------------------------------------ G48-1 并发自适应
    def _adapt_result(self, status: SyncStatus, message: str) -> None:
        """G48-1：连续网络类失败 → 并发减半(下限 1)；连续成功 K 次 → 回升至设置上限。

        状态经 adapt_changed 信号通知 UI（状态栏提示当前并发）。
        """
        try:
            msg = (message or "").lower()
            nw_kw = ("timeout", "refused", "resolve", "peer", "connection",
                     "network", "unable to access", "could not resolve",
                     "timed out", "502", "503", "early eof", "remote end")
            if status == SyncStatus.FAILED and any(k in msg for k in nw_kw):
                self._net_fail_streak += 1
                self._net_ok_streak = 0
                if self._net_fail_streak >= 3:
                    nxt = max(1, self._adapt_current // 2)
                    if nxt < self._adapt_current:
                        self._adapt_current = nxt
                        self.set_concurrency(nxt)
                        self.adapt_changed.emit(
                            nxt, f"网络抖动，并发已自动降至 {nxt}")
            else:
                self._net_ok_streak += 1
                self._net_fail_streak = 0
                if self._net_ok_streak >= 3 and self._adapt_current < self._adapt_max:
                    self._adapt_current = self._adapt_max
                    self.set_concurrency(self._adapt_max)
                    self.adapt_changed.emit(
                        self._adapt_max, f"网络恢复，并发已回升至 {self._adapt_max}")
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
            self._on_worker_done()

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
            precheck_remote=getattr(self, "precheck_remote", False),  # G38-3
            single_branch=getattr(self, "single_branch", False),  # G38-4
            force_ipv4=getattr(self, "force_ipv4", False),  # G38-6
        )
        # 进度附加文本（速率/对象数）注入点：解析 git 行并统一经引擎信号转发到 UI
        svc.send_progress_detail = self._emit_progress_detail
        return svc

    def _worker_service(self, flag: CancelFlag) -> GitService:
        """为单个 worker 构造带「单任务取消」回调的 service（G02-3）。

        每个 worker 独占一个 service 实例，cancelled 同时反映「全局取消」与
        该 worker 的 flag——避免共享同一 service 时并发改 cancelled 的竞态。
        """
        svc = self._service()
        global_cancelled = self.is_cancelled
        svc.cancelled = lambda: global_cancelled() or flag()
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
               mirror: bool = False) -> int:
        """启动一组仓库的并行同步。返回实际调度数量（去重后）。

        - specs: 待同步仓库列表（可含重复，内部去重）
        - host_by_key: {'owner/repo': host}，用于落库保留来源 host
        - fetch_depth/unshallow: 浅克隆语义
        - clear_input: 全部完成后是否需要 UI 清空输入框（engine 只透传标志，由 UI 处理）
        - check_existing: 断点续传预检（已完成且目录在 → 跳过）
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
                    self._mark_done(pre[0] and first.display or key, pre[0], pre[1])
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
        self.tasks = []
        self.flags = {}
        self.specs = {}
        self._host_by_key = host_by_key or {}
        self._depth = fetch_depth
        self._unshallow = unshallow
        self._pending = len(unique_specs)
        # G22-2：登记本轮待完成清单并立即落盘（进程被杀后可据此恢复）
        self._mark_in_progress([f"{s.owner}/{s.repo}" for s in unique_specs])
        self.flush_progress()
        self._emit_line(0, f"开始并行同步 {len(unique_specs)} 个仓库"
                           f"（{'浅克隆 depth=' + str(fetch_depth) if fetch_depth else '满量'}）",
                         "system")

        # G43-2 批次流控：>64 只入队前 64，其余进待发队列，完成一个惰性补投一个。
        # flags/specs 仍登记全部（取消缓存与落库 host 查询语义不变）；进度表全部行
        # 仍由 UI 在 launch 后按 specs 预填，PENDING 态等待。
        for i, spec in enumerate(unique_specs):
            flag = CancelFlag()
            self.flags[i] = flag
            self.specs[i] = spec
            if i < _BATCH_START_LIMIT:
                self._start_worker(i, spec, flag)
            else:
                self._pending_queue.append((i, spec, flag))
        # G43-5：进度心跳定时器（主线程批量 emit）
        self._progress_timer.start()
        return len(unique_specs)

    def _start_worker(self, index: int, spec: RepoSpec, flag: CancelFlag):
        """构造并启动单个 worker（launch 首批与 _replenish 补投共用）。"""
        key = f"{spec.owner}/{spec.repo}"
        host = self._host_by_key.get(key) or "github.com"
        payload = TaskPayload(spec=spec, flag=flag, host=host)
        # G02-3: 每个 worker 独占一个 service（带自身 flag 的取消回调），
        # 支持单任务暂停，其余 worker 互不影响
        service = self._worker_service(flag)
        # worker 只负责 git 同步与结果回传；DB 写入与 progress 落盘统一由引擎
        # 在 _on_result / flush 处理，避免多 worker 并发写同一 SQLite 连接与进度文件
        worker = CloneWorker(index, payload, service, None, None,
                             on_line=lambda c, i=index: self._emit_line(i, c.text, c.level),
                             fetch_depth=self._depth if self._depth else 0,
                             unshallow=self._unshallow)
        worker.signals.line.connect(self.line.emit)
        # G43-5：progress/progress_detail 聚合进缓冲，由 100ms 心跳批量 emit
        worker.signals.progress.connect(self._on_worker_progress)
        worker.signals.progress_detail.connect(self._on_worker_progress_detail)
        worker.signals.result.connect(self._on_result)
        worker.signals.finished.connect(self._on_worker_done)
        self.tasks.append(worker)
        self.pool.start(worker)

    def _replenish(self):
        """G43-2：完成一个任务 → 从待发队列惰性补投一个（取消/无排队时不做）。"""
        if self._cancelled or not self._pending_queue:
            return
        index, spec, flag = self._pending_queue.pop(0)
        self._start_worker(index, spec, flag)

    # ------------------------------------------------------------ 回调
    def _on_result(self, index: int, res: SyncResult):
        # 引擎统一落库（worker 不再写 DB，避免并发写同一 SQLite 连接）
        if self.db is not None:
            try:
                repo_id = self.db.upsert_repo(
                    res.spec, res.path,
                    host=(self.specs.get(index, res.spec) and
                          self._host_by_key.get(f"{res.spec.owner}/{res.spec.repo}",
                                                "github.com")),
                    default_branch=None, head_sha=res.head_sha or None)
                self.db.add_sync_history(
                    repo_id, res.status, res.action, res.message, res.detail,
                    commits=res.commits, head_before="", head_after=res.head_sha,
                    remote_head=res.remote_sha, duration_ms=res.duration_ms)
            except Exception:
                pass  # 落库失败不影响 UI 展示（历史可追溯性损失通过日志暴露）
        # 进度内存态登记
        self._mark_done(f"{res.spec.owner}/{res.spec.repo}", res.status, res.message)
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

    def _on_worker_done(self):
        # 计数含取消：取消也是终态，若不计入会导致 finished 信号永不收敛
        self._pending = max(0, self._pending - 1)
        # G22-2：每任务结束都周期落盘一次（进程被杀时 in_progress 尽量最新）
        self.flush_progress()
        if self._pending == 0:
            # 全部完成：先冲刷日志/进度缓冲（保证 finished 前送达），再强制落盘一次
            self.flush_all_buffers()
            self.flush_progress()
            self.busy = False
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
        while self._pending > 0 and time.time() < deadline:
            try:
                from PyQt6.QtCore import QCoreApplication
                app = QCoreApplication.instance()
                if app is not None:
                    app.processEvents()
            except Exception:
                pass
            time.sleep(0.02)
        try:
            self.pool.waitForDone(200)
        except Exception:
            pass
        self.flush_all_buffers()  # G43-1②：drain 后冲刷残留缓冲
        return self._pending == 0

    def shutdown(self):
        """取消剩余任务并落盘，供窗口关闭时调用。"""
        self.cancel_all()
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
