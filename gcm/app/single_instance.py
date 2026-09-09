# -*- coding: utf-8 -*-
"""G06-2 单实例锁：文件锁 + PID 探活，防多实例并发写数据库/进度文件。

- 锁文件 `<name>.lock` 常驻持有（flock / msvcrt 独占）。
- 哨兵 `<name>.lock.pid` 记录持有 PID；锁不可得时检查哨兵 PID 是否存活，
  已死（上次崩溃残留）→ 清理后重试一次。
- 设计目标：重复启动第二个实例被拦截，首个实例不受影响。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


def _pid_alive(pid: int) -> bool:
    """判断 PID 是否存活（Windows / POSIX）。"""
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            try:
                code = ctypes.c_ulong()
                ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
                return code.value == 259  # STILL_ACTIVE
            finally:
                ctypes.windll.kernel32.CloseHandle(h)
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def _free_lock(lock) -> None:
    """幂等清理：删除 PID 哨兵与锁句柄（供测试/异常路径兜底）。"""
    try:
        lock.release()
    except Exception:
        pass
    try:
        if getattr(lock, "lock_fh", None) is not None:
            lock.lock_fh.close()
    except Exception:
        pass
    try:
        p = getattr(lock, "pid_file", None)
        if p is not None and p.exists():
            p.unlink(missing_ok=True)
    except Exception:
        pass


class SingleInstanceLock:
    """跨进程单实例文件锁。"""

    def __init__(self, lock_path: str | Path, app_name: str = "Git-clone-Max"):
        self.path = Path(lock_path)
        # 哨兵 PID 文件：`app.lock.pid`（固定命名，避免 with_suffix 把 .lock 也替换掉）
        self.pid_file = self.path.with_name(self.path.name + ".pid")
        self.app_name = app_name
        self.lock_fh: Optional[object] = None
        self._owned = False

    # ------------------------------------------------------------------
    def try_acquire(self) -> bool:
        """尝试获取锁；已有存活实例 → False；崩溃残留 → 清理后重试一次。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self._try_lock_file():
            self._write_pid()
            self._owned = True
            return True
        # 锁不可得：检查哨兵 PID 是否存活
        if self._pid_file_stale():
            # 残留死进程 → 清理后重试
            try:
                self.path.unlink(missing_ok=True)
            except Exception:
                pass
            try:
                self.pid_file.unlink(missing_ok=True)
            except Exception:
                pass
            if self._try_lock_file():
                self._write_pid()
                self._owned = True
                return True
        return False

    def _try_lock_file(self) -> bool:
        """获取文件独占锁（flock/msvcrt）；失败返回 False。"""
        try:
            if os.name == "nt":
                import msvcrt
                fh = open(self.path, "a+", encoding="utf-8")
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    fh.close()
                    return False
            else:
                import fcntl
                fh = open(self.path, "a+", encoding="utf-8")
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    fh.close()
                    return False
            self.lock_fh = fh
            return True
        except Exception:
            return False

    def _write_pid(self) -> None:
        try:
            # 用 pathlib 写；不存在则先创建（open with 'w' 自动建父目录已由 mkdir 保证）
            self.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        except Exception:
            pass

    def _pid_file_stale(self) -> bool:
        """哨兵 PID 文件存在且 PID 已死 → True（崩溃残留）。"""
        try:
            if not self.pid_file.exists():
                return True
            pid = int(self.pid_file.read_text(encoding="utf-8").strip() or 0)
            return not _pid_alive(pid)
        except Exception:
            return True

    def release(self) -> None:
        """释放锁并清理哨兵。"""
        if self.lock_fh is not None:
            try:
                if os.name == "nt":
                    import msvcrt
                    self.lock_fh.seek(0)
                    msvcrt.locking(self.lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.lock_fh.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                self.lock_fh.close()
            except Exception:
                pass
            self.lock_fh = None
        try:
            self.pid_file.unlink(missing_ok=True)
        except Exception:
            pass
        self._owned = False
