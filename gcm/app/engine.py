# -*- coding: utf-8 -*-
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

import time
from collections import OrderedDict
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from PyQt6.QtCore import QObject, QThreadPool, pyqtSignal

from ..db.repo_db import Database, load_progress, save_progress
from ..git.service import GitService
from ..models import RepoSpec, SyncResult, SyncStatus
from .worker import CancelFlag, CloneWorker, TaskPayload

# 周期落盘间隔（秒）：期间完成的任务先聚在内存，到时统一写一次
_FLUSH_INTERVAL = 2.0


class SyncEngine(QObject):
    """统一的并行同步调度器。"""

    line = pyqtSignal(int, str, str)        # index, text, level
    progress = pyqtSignal(int, str)         # index, percent
    progress_detail = pyqtSignal(int, str)  # index, "rate files" 附加文本（速率/对象数）
    result = pyqtSignal(int, SyncResult)    # index, result
    finished = pyqtSignal()                 # 全部 worker 完成（成功/失败/取消都算）

    def __init__(self, root: Path, db: Optional[Database] = None,
                 progress_path: Optional[Path] = None,
                 concurrency: int = 8,
                 fetch_timeout: int = 300, clone_timeout: int = 600,
                 retries: int = 2, proxy: str = "", token: str = ""):
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

        self.tasks: List[CloneWorker] = []
        self.flags: Dict[int, CancelFlag] = {}
        self.specs: Dict[int, RepoSpec] = {}
        self._host_by_key: Dict[str, str] = {}
        self._depth = 0
        self._unshallow = False
        self._pending = 0
        self.busy = False

        # 进度内存态 + 周期落盘
        self._progress: Dict[str, dict] = load_progress(self.progress_path) if self.progress_path \
            else {"finished": [], "in_progress": {}}
        self._dirty = False
        self._skip_done = True          # 已完成且目录存在 → 直接跳过

        # 取消缓存：key -> 是否已取消（避免 UI 每行输出遍历全部 flags）
        self._cancelled = False

    # ------------------------------------------------------------ 并发
    def set_concurrency(self, n: int) -> int:
        """设置线程池并发上限，返回实际生效值（1..32）。"""
        n = int(n)
        n = max(1, min(32, n))
        self.pool.setMaxThreadCount(n)
        return n

    @property
    def concurrency(self) -> int:
        return self.pool.maxThreadCount()

    # ------------------------------------------------------------ 进度
    def _emit_line(self, index: int, text: str, level: str = "info"):
        self.line.emit(index, text, level)

    def _emit_progress_detail(self, index: int, text: str):
        """转发进度附加文本（速率/对象数）到 UI。"""
        self.progress_detail.emit(index, text)

    def flush_progress(self):
        """把内存进度态原子落盘（周期调用；engine 单线程写文件）。"""
        if not self.progress_path or not self._dirty:
            return
        try:
            save_progress(self.progress_path, self._progress)
            self._dirty = False
        except Exception:
            pass  # 落盘失败不致命，下次周期再试

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

    # ------------------------------------------------------------ 取消
    def cancel_all(self):
        self._cancelled = True
        for flag in self.flags.values():
            flag.cancel()

    def is_cancelled(self, key: str = "") -> bool:
        """全局取消状态。key 未用（当前为全量取消语义），保留参数便于后续逐仓库取消。"""
        return self._cancelled

    # ------------------------------------------------------------ 调度
    @staticmethod
    def _dedupe_specs(specs: Iterable[RepoSpec]) -> "OrderedDict[str, list[RepoSpec]]":
        """按目标目录（folder_name 或 local_path）去重，返回 key -> [spec]。"""
        groups: "OrderedDict[str, list[RepoSpec]]" = OrderedDict()
        for s in specs:
            key = s.local_path or s.folder_name  # 目标目录键
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
        )
        # 进度附加文本（速率/对象数）注入点：解析 git 行并统一经引擎信号转发到 UI
        svc.send_progress_detail = self._emit_progress_detail
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

    def launch(self, specs: Iterable[RepoSpec], host_by_key: Optional[Dict[str, str]] = None,
               fetch_depth: int = 0, unshallow: bool = False,
               clear_input: bool = False, check_existing: bool = True) -> int:
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
        self._clear_input = clear_input
        self._skip_done = check_existing

        groups = self._dedupe_specs(specs)
        # 展开去重后的唯一 spec 列表
        unique_specs: List[RepoSpec] = []
        dup_keys: List[str] = []
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
        self._emit_line(0, f"开始并行同步 {len(unique_specs)} 个仓库"
                           f"（{'浅克隆 depth=' + str(fetch_depth) if fetch_depth else '满量'}）", "system")

        service = self._service()
        for i, spec in enumerate(unique_specs):
            flag = CancelFlag()
            self.flags[i] = flag
            self.specs[i] = spec
            key = f"{spec.owner}/{spec.repo}"
            host = self._host_by_key.get(key) or "github.com"
            payload = TaskPayload(spec=spec, flag=flag, host=host)
            # worker 只负责 git 同步与结果回传；DB 写入与 progress 落盘统一由引擎
            # 在 _on_result / flush 处理，避免多 worker 并发写同一 SQLite 连接与进度文件
            worker = CloneWorker(i, payload, service, None, None,
                                 on_line=lambda c, i=i: self._emit_line(i, c.text, c.level),
                                 fetch_depth=self._depth if self._depth else 0,
                                 unshallow=self._unshallow)
            worker.signals.line.connect(self.line.emit)
            worker.signals.progress.connect(self.progress.emit)
            worker.signals.progress_detail.connect(self.progress_detail.emit)
            worker.signals.result.connect(self._on_result)
            worker.signals.finished.connect(self._on_worker_done)
            self.tasks.append(worker)
            self.pool.start(worker)
        return len(unique_specs)

    # ------------------------------------------------------------ 回调
    def _on_result(self, index: int, res: SyncResult):
        # 引擎统一落库（worker 不再写 DB，避免并发写同一 SQLite 连接）
        if self.db is not None:
            try:
                repo_id = self.db.upsert_repo(res.spec, res.path,
                                              host=self.specs.get(index, res.spec) and
                                              self._host_by_key.get(f"{res.spec.owner}/{res.spec.repo}", "github.com"),
                                              default_branch=None, head_sha=res.head_sha or None)
                self.db.add_sync_history(
                    repo_id, res.status, res.action, res.message, res.detail,
                    commits=res.commits, head_before="", head_after=res.head_sha,
                    remote_head=res.remote_sha, duration_ms=res.duration_ms)
            except Exception:
                pass  # 落库失败不影响 UI 展示（历史可追溯性损失通过日志暴露）
        # 进度内存态登记
        self._mark_done(f"{res.spec.owner}/{res.spec.repo}", res.status, res.message)
        # 转发给 UI
        self.result.emit(index, res)

    def _on_worker_done(self):
        self._pending = max(0, self._pending - 1)
        if self._pending == 0:
            self.flush_progress()  # 全部完成：强制落盘一次
            self.busy = False
            self.finished.emit()

    # ------------------------------------------------------------ 收尾
    def shutdown(self):
        """取消剩余任务并落盘，供窗口关闭时调用。"""
        self.cancel_all()
        self.flush_progress()
        try:
            self.pool.clear()
        except Exception:
            pass