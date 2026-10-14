"""跨进程文件锁（G67-10）：Windows `msvcrt.locking` / POSIX `fcntl.flock`。

为什么需要它：本项目多处采用「读 JSON → 改 → 原子替换」的写盘模式
（`progress.json`、`settings.json`）。**线程锁只保护同进程**——多开一个实例
（或看门狗/CLI 与服务进程并存）时，两个进程各自加各自的线程锁，读-改-写照样
交错：后写的整份覆盖前写的，用户表现为「改了设置没保存」「token 偶发丢失」。

锁失败时**降级为仅线程锁并留痕**（不抛异常中断主流程），因为「文件锁不可用」
（只读目录、网络盘不支持锁）比「写失败」更可接受；但绝不像原来那样静默降级。
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# 同进程线程互斥。**模块级共用一把**是有意的：Windows 的 msvcrt.locking 是
# 按文件句柄加锁的，同进程多线程各开各的句柄会互相死等；这里先用线程锁把
# 同进程调用串行化，文件锁才只承担「跨进程」这一件事。
_thread_lock = threading.RLock()
# 本线程**已持有文件锁的路径集合**（G68-2）。
# 这里必须按**路径**判断重入，不能只数深度：
#   `RLock` 可重入，但**文件锁不是** —— 同一路径重入若再开句柄会自锁死等，所以同路径要跳过；
#   而**不同路径**嵌套时若也跳过，那条路径就完全没有跨进程保护了。
# 实测（两进程 + 区间重叠判定）：用深度计数时，P1 持锁 A 后进入锁 B（不同路径）会跳过 B 的文件锁，
#   P1[0, 2.51s] 与 P2[1.43, 3.94s] **重叠 1.08s** → B 上没有互斥。
_depth_paths = threading.local()


def _held_paths() -> set[str]:
    if not getattr(_depth_paths, "paths", None):
        _depth_paths.paths = set()
    return _depth_paths.paths


def _lock_key(target: Path) -> str:
    """锁的规范化标识：同一文件的不同写法（相对/绝对/大小写）必须归一到同一个 key。"""
    try:
        return os.path.normcase(os.path.abspath(str(target)))
    except Exception:
        return str(target)


# 抢锁的总预算（秒）。超时才降级 —— 降级会失去跨进程互斥，必须是真的异常才发生。
_ACQUIRE_TIMEOUT_SEC = 30.0
_ACQUIRE_POLL_SEC = 0.02


def _acquire(lock_fh) -> bool:
    """对已打开的句柄加独占锁；成功返回 True。

    G69-16（压测抓出，重要）：**不能用 `msvcrt.LK_LOCK`**。它内建「重试 10 次、
    每次间隔 1 秒」的上限，之后直接抛 `OSError` —— 而本函数一抛、调用方就
    **静默降级为仅线程锁**，该进程从此**完全没有跨进程保护**。
    实测：4 进程各写 200 次时，有 1 个进程降级；降级期间两个写方同时进入临界区，
    于是 `os.replace` 开始报 `PermissionError [WinError 5]`（这正是压测里那几次
    「写失败」的真因，不是退避不够）。

    改为用 `LK_NBLCK`（非阻塞）**自己轮询**，超时预算由我们掌握；只有超过
    `_ACQUIRE_TIMEOUT_SEC` 仍拿不到才降级（那才是真异常，值得告警）。
    """
    if os.name == "nt":
        import msvcrt
        deadline = time.monotonic() + _ACQUIRE_TIMEOUT_SEC
        while True:
            try:
                lock_fh.seek(0)                       # 锁定从第 0 字节起的 1 字节
                msvcrt.locking(lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
                return True
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(_ACQUIRE_POLL_SEC)
    import fcntl
    fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)      # POSIX 阻塞式，无内建上限
    return True


def _release(lock_fh) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            lock_fh.seek(0)
            msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
    except Exception:
        pass


def _warn(msg: str) -> None:
    try:
        from ..app.applog import warn as _log_warn
        _log_warn(msg)
    except Exception:
        pass


@contextmanager
def cross_process_lock(path: Path | str, label: str = "文件") -> Iterator[None]:
    """线程锁 + 进程锁双层互斥，保护对 `path` 的读-改-写临界区。

    锁文件为 `<path>.lock`（追加打开，不写入内容）。获取失败 → 降级为仅线程锁，
    并写入 app.log 留痕（跨进程互斥失效这件事必须可观测，不能无声发生）。
    """
    target = Path(path)
    key = _lock_key(target)
    with _thread_lock:
        # 只对**同一路径**判定为可重入（见 `_depth_paths` 注释：不同路径必须各自加锁）
        held = _held_paths()
        nested = key in held
        if not nested:
            held.add(key)
        lock_fh = None
        try:
            if not nested:
                lock_fh = open(str(target) + ".lock", "a+")
                _acquire(lock_fh)
        except Exception:
            # 关键：失败分支必须关掉**已打开**的句柄再降级。
            # 否则每次调用泄漏一个 fd（`save_progress` 每个任务完成都调用一次，
            # 长时间大批量会累积到进程 fd 上限）。G67-13 实测过这条路径。
            if lock_fh is not None:
                try:
                    lock_fh.close()
                except Exception:
                    pass
            lock_fh = None
            _warn(f"{label} 进程锁获取失败，已降级为仅线程锁（跨进程互斥失效）")
        try:
            yield
        finally:
            if not nested:
                held.discard(key)
            if lock_fh is not None:
                _release(lock_fh)
                try:
                    lock_fh.close()
                except Exception:
                    pass
