# -*- coding: utf-8 -*-
"""共享数据模型与常量。"""
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class SyncStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    CONFLICT = "conflict"  # 本地已有改动冲突，未覆盖


class SyncAction(str, Enum):
    CLONED = "cloned"          # 新建克隆
    UPDATED = "updated"        # 增量 fetch + merge/fast-forward
    FETCHED = "fetched"        # 远端无新提交，已 fetch 检查
    EMPTY = "empty"            # 空仓库（远端无提交）
    SKIPPED = "skipped"        # 已存在且配置为跳过
    CONFLICT = "conflict"      # 检测到冲突，未覆盖本地
    CANCELLED = "cancelled"    # 用户取消
    FAILED = "failed"


@dataclass
class RepoSpec:
    """解析后的仓库规格。"""
    owner: str
    repo: str
    url_https: str
    display: str = ""
    folder_name: str = ""
    is_local: bool = False      # 仅本地仓库（无远端），同步时跳过 fetch 依赖 clone

    def __post_init__(self):
        if not self.display:
            self.display = f"{self.owner}/{self.repo}"
        if not self.folder_name:
            self.folder_name = f"{self.owner}__{self.repo}"


@dataclass
class SyncResult:
    """单仓库同步结果。"""
    spec: RepoSpec
    status: SyncStatus
    action: SyncAction = SyncAction.SKIPPED
    message: str = ""
    detail: str = ""
    path: str = ""
    commits: int = 0          # 本次拉取新提交数（增量）
    head_sha: str = ""        # 同步后 HEAD
    remote_sha: str = ""      # 远端 HEAD（fetch 后 origin/HEAD）
    started: float = 0.0
    ended: float = 0.0


@dataclass
class StreamChunk:
    """流式输出块。"""
    index: int
    text: str
    level: str = "info"       # info / warn / error / system
