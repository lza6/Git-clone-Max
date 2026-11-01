"""线程池 worker：并行执行 git 同步任务，实时转发日志到 UI。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from ..db.repo_db import Database, mark_finished
from ..git.service import GitService
from ..models import (  # 每个仓库独立的取消标志
    RepoSpec,
    SyncAction,
    SyncResult,
    SyncStatus,
)


class CancelFlag:
    def __init__(self):
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def __call__(self) -> bool:
        return self._cancelled


@dataclass
class TaskPayload:
    spec: RepoSpec
    flag: CancelFlag = None
    host: str = "github.com"   # 仓库来源 host（导入仓库为 local/gitlab 等，落库原样保留）


class WorkerSignals(QObject):
    line = pyqtSignal(int, str, str)        # index, text, level
    progress = pyqtSignal(int, str)         # index, percent_text
    progress_detail = pyqtSignal(int, str)  # index, "rate files" 附加文本（速率/对象数）
    result = pyqtSignal(int, SyncResult)
    finished = pyqtSignal()


class CloneWorker(QRunnable):
    """并行执行一个仓库的同步任务。"""

    def __init__(self, index: int, payload: TaskPayload, service: GitService,
                 db: Database, progress_path: str, on_line=None,
                 fetch_depth: int = 0, unshallow: bool = False,
                 engine=None, is_update: bool = False,
                 host_gate=None):
        super().__init__()
        self.setAutoDelete(True)
        self.index = index
        self.payload = payload
        self.service = service
        self.db = db
        # A5-5/R3：per-host 闸门（None=不限流）。run() 内 acquire/finally release。
        self._host_gate = host_gate
        self.progress_path = progress_path
        self.signals = WorkerSignals()
        self._external_line = on_line
        self._fetch_depth = fetch_depth
        self._unshallow = unshallow
        self._engine = engine           # 可选：引擎引用（供 line/progress 转发，避免 index=0）
        self._is_update = is_update     # 更新模式：进程开始即“更新中”，不先显示失败

    def _emit_progress_detail(self, text):
        """把进度附加文本（速率/对象数）经 signals.progress_detail 发出。"""
        try:
            self.signals.progress_detail.emit(self.index, text)
        except Exception:
            pass

    def run(self):
        spec = self.payload.spec
        res = SyncResult(spec=spec, status=SyncStatus.RUNNING)
        # A5-5/R3：per-host 闸门 —— 在线程池线程内取票（不占 GUI 线程），
        # 直到同 host 有空位才真正开始 git 操作。
        # 审查 R-3：acquire 带超时循环内查取消标志 —— 无限阻塞会让
        # cancel_all 对已入池 worker 失效（线程池槽位被占死）。
        if self._host_gate is not None:
            acquired = False
            try:
                while not acquired:
                    acquired = self._host_gate.acquire(timeout=0.5)
                    if not acquired and self.payload.flag is not None \
                            and self.payload.flag():
                        break  # 已取消 → 不再等闸门，走正常取消路径
            except Exception:
                acquired = True  # 闸门失效降级为不限流（绝不卡死任务）
            if not acquired:
                self.signals.result.emit(self.index, SyncResult(
                    spec=spec, status=SyncStatus.CANCELLED,
                    action=SyncAction.FAILED, message="已取消（排队中）"))
                self.signals.finished.emit()
                return
        try:
            # 更新模式下：状态先置“更新中”，避免启动瞬间显示“失败”
            if self._is_update:
                self.signals.result.emit(self.index, SyncResult(
                    spec=spec, status=SyncStatus.RUNNING,
                    action=SyncAction.FETCHED, message="更新中…"))
            # 复用传入的 service；若外部未配置浅克隆语义则按 payload 深度覆盖
            svc = self.service
            # G69-20：把**本 worker** 的行回调接到 service 上。
            # 此前 `on_line` 参数只被保存、从未使用：所有输出都走引擎
            # `_service()` 里固定 `index=0` 的 lambda —— 多仓库并发时
            # 每个仓库的 git 输出全部记到第 0 行（日志归属错误）。
            if self._external_line is not None:
                try:
                    svc.on_line = self._external_line
                except Exception:
                    pass
            # 进度附加文本（速率/对象数）注入点：把解析结果透传给本 worker 的进度详情信号
            svc.send_progress_detail = self._emit_progress_detail
            if svc.fetch_depth != self._fetch_depth or svc.unshallow != self._unshallow:
                svc = GitService(
                    svc.root, on_line=self._external_line or svc.on_line,
                    cancelled=svc.cancelled,
                    fetch_timeout=svc.fetch_timeout, clone_timeout=svc.clone_timeout,
                    retries=svc.retries, backoff=svc._backoff, proxy=svc.proxy,
                    fetch_depth=self._fetch_depth, unshallow=self._unshallow,
                    # G69-21：重建时必须带上**全部**非深度参数，否则这条降级路径
                    # 会静默丢掉 token/凭据/LFS/子模块/钩子/限速等配置。
                    token=getattr(svc, "token", ""),
                    host_tokens=getattr(svc, "host_tokens", None) or {},
                    host_auth_types=getattr(svc, "host_auth_types", None) or {},
                    ssh_key=getattr(svc, "_ssh_key", "") or "",
                    lfs_enabled=getattr(svc, "_lfs_enabled", False),
                    submodule=getattr(svc, "_submodule", False),
                    post_clone_hook=getattr(svc, "_post_clone_hook", "") or "",
                    rate_limit_kbps=getattr(svc, "rate_limit_kbps", 0) or 0,
                    force_ipv4=getattr(svc, "force_ipv4", False),
                    precheck_remote=getattr(svc, "precheck_remote", False),
                    single_branch=getattr(svc, "single_branch", False),
                    mirror=getattr(svc, "_mirror", False),
                    # G58-2：镜像备援配置
                    mirror_prefix=getattr(svc, "mirror_prefix", None) or {},
                    mirror_use_presets=getattr(svc, "mirror_use_presets", True),
                    mirror_fallback=getattr(svc, "mirror_fallback", True),
                    mirror_probe_timeout=getattr(svc, "mirror_probe_timeout", 8.0),
                )
                svc.send_progress_detail = self._emit_progress_detail
            res = svc.sync(spec)
            # G81（N-6）：同步成功后顺手做确定性画像（纯本地读、零网络、零 LLM）。
            # 实测 ≈1.1ms/仓（1,726 目录 1.95s），因此放在 worker 线程里同步做，
            # 不需要后台任务。失败/取消/裸仓库不探测（裸仓没有工作树可画像）。
            if (res.status == SyncStatus.SUCCESS and res.path
                    and getattr(res, "checkout_state", "ok") != "skip"):
                try:
                    from .knowledge import probe_repo_profile
                    res.profile = probe_repo_profile(res.path)
                except Exception as e:  # noqa: BLE001
                    # 画像失败绝不能把一次成功的同步变成失败 —— 放弃画像，但必须留痕
                    # （走 applog 而不是 _emit：不想让每个仓库都往 UI 日志刷一行）。
                    res.profile = None
                    from . import applog
                    applog.warn(f"仓库画像探测失败（{spec.folder_name}）：{e}")
            # DB 记录由引擎统一处理（engine.py 传入 db=None 时此处跳过，
            # 避免多 worker 并发写同一 SQLite 连接）；独立使用时仍写库
            if self.db is not None:
                repo_id = self.db.upsert_repo(spec, res.path, host=self.payload.host,
                                              default_branch=None, head_sha=res.head_sha)
                self.db.add_sync_history(
                    repo_id, res.status, res.action, res.message, res.detail,
                    commits=res.commits, head_before="", head_after=res.head_sha,
                    remote_head=res.remote_sha, duration_ms=res.duration_ms,
                )
                # G81：独立使用路径也要落画像（引擎路径在 engine._on_result 落）
                if res.profile is not None:
                    try:
                        self.db.set_repo_profile(repo_id, res.profile)
                    except Exception as e:  # noqa: BLE001
                        # 画像是展示用派生数据：写失败不影响同步结果，但必须留痕
                        from . import applog
                        applog.warn(
                            f"仓库画像落库失败（{spec.folder_name}）：{e}")
            self.signals.result.emit(self.index, res)
        except Exception as e:
            res.status = SyncStatus.FAILED
            res.action = SyncAction.FAILED
            res.message = "异常"
            res.detail = str(e)
            self.signals.result.emit(self.index, res)
        finally:
            # A5-5/R3：释放 host 闸门（只在真正 acquire 过才 release —— 取消早退未持票）
            if self._host_gate is not None and getattr(self, "_gate_held", False):
                try:
                    self._host_gate.release()
                except Exception:
                    pass
            # 进度落盘由引擎统一处理（engine.py 传 path=None 跳过）；
            # 独立使用时仍写 progress.json（G21-2：统一走 mark_finished，不再手工拼结构）
            if self.progress_path:
                try:
                    mark_finished(Path(self.progress_path), spec, {
                        "status": res.status.value,
                        "message": res.message,
                        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    })
                except Exception:
                    pass
            self.signals.finished.emit()
