"""git 命令环形缓冲（Rev10 G-01/G-02）。

**要解决的问题**：`run_git_ui` 的日志只保留最近 200 行且随进程即丢 ——
某条仓库克隆失败后，用户想回看「git 到底说了什么」没有任何入口
（失败面板只有一条 message 摘要）。

**设计**：
- 每条命令一行记录（folder_name、argv 摘要、rc、耗时、输出尾部 50 行）；
- 进程内 deque，容量 500 条（超出丢最旧）—— 与 event_bus 同一取舍：
  观测数据不落盘、不阻塞主流程；
- 线程安全：`run_git_ui` 在 worker 线程调用 `record()`；
- 打码：入缓冲前过 `redact`（它面向 UI 回看，等同日志出站）。
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from ..util.redact import redact

#: 环形容量（条）。32 并发 × 500 仓批次下，500 条足够覆盖最后一整批。
MAX_ENTRIES = 500

#: 每条记录保留的输出行数（尾部）。
TAIL_LINES = 50


@dataclass
class CommandRecord:
    """一条 git 命令的执行记录（只读快照）。"""
    folder_name: str
    argv: str                  # 已打码的命令摘要（不含完整参数）
    returncode: int
    duration_ms: int
    ended_at: str
    tail: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "folder_name": self.folder_name,
            "argv": self.argv,
            "returncode": self.returncode,
            "duration_ms": self.duration_ms,
            "ended_at": self.ended_at,
            "tail": list(self.tail),
        }


_buf: deque[CommandRecord] = deque(maxlen=MAX_ENTRIES)
_lock = threading.Lock()


def record(folder_name: str, cmd: list[str] | tuple[str, ...], returncode: int,
           duration_ms: int, tail_lines: list[str]) -> None:
    """记一条命令（任何失败都不抛 —— 观测绝不影响主流程）。"""
    try:
        argv = redact(" ".join(str(c) for c in cmd[:6]))
        tail = [redact(str(x)) for x in tail_lines[-TAIL_LINES:]]
        rec = CommandRecord(
            folder_name=str(folder_name or "?"),
            argv=argv,
            returncode=int(returncode),
            duration_ms=int(duration_ms),
            ended_at=time.strftime("%Y-%m-%d %H:%M:%S"),
            tail=tail,
        )
        with _lock:
            _buf.append(rec)
    except Exception as e:
        from .applog import warn as _warn
        _warn(f"git 命令记录失败（观测缺一条，不影响主流程）：{e}")


def recent(n: int = 50, folder_name: str | None = None) -> list[CommandRecord]:
    """最近 n 条（可按仓过滤）；新的在前。"""
    with _lock:
        items = list(_buf)
    if folder_name:
        items = [r for r in items if r.folder_name == folder_name]
    return list(reversed(items))[: max(0, int(n))]


def for_repo(folder_name: str, n: int = 20) -> list[CommandRecord]:
    """某仓库的最近命令（G-02 查看入口用）。"""
    return recent(n, folder_name=folder_name)


def clear_for_tests() -> None:
    with _lock:
        _buf.clear()
