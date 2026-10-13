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
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

# 同进程线程互斥。**模块级共用一把**是有意的：Windows 的 msvcrt.locking 是
# 按文件句柄加锁的，同进程多线程各开各的句柄会互相死等；这里先用线程锁把
# 同进程调用串行化，文件锁才只承担「跨进程」这一件事。
_thread_lock = threading.RLock()
# 重入深度（仅本线程）：`RLock` 是可重入的，但**文件锁不是** —— 同线程嵌套时若
# 再开一个句柄加同一把 msvcrt 锁会自锁死等。所以只由最外层真正加文件锁。
_depth = threading.local()


def _acquire(lock_fh) -> bool:
    """对已打开的句柄加独占锁；成功返回 True。"""
    if os.name == "nt":
        import msvcrt
        msvcrt.locking(lock_fh.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
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
    with _thread_lock:
        # 同线程重入 → 只复用外层的文件锁，不再开句柄（开了会自锁死等）
        nested = getattr(_depth, "n", 0) > 0
        _depth.n = getattr(_depth, "n", 0) + 1
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
            _depth.n -= 1
            if lock_fh is not None:
                _release(lock_fh)
                try:
                    lock_fh.close()
                except Exception:
                    pass
