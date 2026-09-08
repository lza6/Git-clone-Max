# -*- coding: utf-8 -*-
"""线程池 worker：并行执行 git 同步任务，实时转发日志到 UI。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal, pyqtSlot

from ..db.repo_db import Database, load_progress, save_progress
from ..git.service import GitService
from ..models import RepoSpec, StreamChunk, SyncResult, SyncAction, SyncStatus

# 每个仓库独立的取消标志
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


class WorkerSignals(QObject):
    line = pyqtSignal(int, str, str)        # index, text, level
    progress = pyqtSignal(int, str)         # index, percent_text
    result = pyqtSignal(int, SyncResult)
    finished = pyqtSignal()


class CloneWorker(QRunnable):
    """并行执行一个仓库的同步任务。"""

    def __init__(self, index: int, payload: TaskPayload, service: GitService,
                 db: Database, progress_path: str, on_line=None):
        super().__init__()
        self.setAutoDelete(True)
        self.index = index
        self.payload = payload
        self.service = service
        self.db = db
        self.progress_path = progress_path
        self.signals = WorkerSignals()
        self._external_line = on_line

    def run(self):
        spec = self.payload.spec
        flag = self.payload.flag or CancelFlag()
        res = SyncResult(spec=spec, status=SyncStatus.RUNNING)
        try:
            res = self.service.sync(spec)
            # 记录数据库（空仓库 head_sha 为空也照常入库，action=empty）
            if self.db is not None:
                repo_id = self.db.upsert_repo(spec, res.path, host="github.com",
                                              default_branch=None, head_sha=res.head_sha)
                self.db.add_sync_history(
                    repo_id, res.status, res.action, res.message, res.detail,
                    commits=res.commits, head_before="", head_after=res.head_sha,
                    remote_head=res.remote_sha, duration_ms=res.duration_ms,
                )
            self.signals.result.emit(self.index, res)
        except Exception as e:
            res.status = SyncStatus.FAILED
            res.action = SyncAction.FAILED
            res.message = "异常"
            res.detail = str(e)
            self.signals.result.emit(self.index, res)
        finally:
            # 标记进度完成（断点续传）
            if self.progress_path:
                data = load_progress(Path(self.progress_path))
                data["finished"] = [x for x in data.get("finished", []) if x.get("key") != f"{spec.owner}/{spec.repo}"]
                data["finished"].append({
                    "key": f"{spec.owner}/{spec.repo}",
                    "status": res.status.value,
                    "message": res.message,
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                save_progress(Path(self.progress_path), data)
            self.signals.finished.emit()
