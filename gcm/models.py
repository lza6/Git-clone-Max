"""共享数据模型与常量。"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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
    local_path: str = ""        # 实际本地路径（导入仓库用；空则按 root/folder_name 推导）
    ref: str = ""               # G08-2 指定分支/标签（owner/repo@v1.2.0 → clone -b）

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
    # G81：由 `git/service` 在收尾时赋值；此前是"动态挂上去"的隐式属性，
    # 但 `engine._on_result` 落库时又直接 `res.duration_ms` 访问 —— 任何没走
    # service 的构造路径（测试、重放、外部集成）都会在落库处抛 AttributeError。
    # 显式声明后两种写法都安全。
    duration_ms: int = 0
    # G80-B4：检出状态。"" / "ok" = 正常；"missing" = 已取回但工作树没检出；
    # "skip" = 裸仓库（本就没有工作树，不判失败）。**不改 SyncStatus 枚举**，
    # 因此对 agent 契约（status 取值集合）零影响。
    checkout_state: str = "ok"
    # G81（N-5）：本仓库「为什么这次慢 / 走了哪条路」的可解释性字段。
    # 加性字段，老消费者忽略即可（SSE/CLI --json 用同一份事件，只在这里暴露一次）。
    # mirror_used: 实际取数用的镜像前缀（"" = 直连源站）。镜像克隆成功后
    # origin 会恢复为源站（G69-21），所以不能从目录状态反推 —— 必须在取数时记录。
    # degrade_path: 弱网降级链（"" = 全程常规克隆），取值 "treeless" / "depth1"，
    # 对应 service.py 既有两级降级；用于把"为什么历史不完整"从日志搬到结果行。
    mirror_used: str = ""
    degrade_path: str = ""
    #: C3-4（Rev7 C-29）：分阶段耗时（毫秒），键如 precheck / transfer / checkout / hook。
    #: 只用于解释「这次为什么慢」与统计页的瓶颈排序，**不参与任何判定**。
    #: 加性字段：老消费者忽略即可；CLI/SSE 都从同一份对象出。
    stages: dict = field(default_factory=dict)
    # G81（N-6）：确定性仓库画像（`app.knowledge.RepoProfile`）。由 worker 在
    # 同步成功后探测、由引擎落库；**不参与任何判定**，只用于展示/导出。
    # 类型标 Any 是刻意的：`models` 是底层，不能反向 import `app.knowledge`
    # （那会让 app→models→app 成环）。空 None = 未探测。
    profile: Any = None


@dataclass
class StreamChunk:
    """流式输出块。"""
    index: int
    text: str
    level: str = "info"       # info / warn / error / system
