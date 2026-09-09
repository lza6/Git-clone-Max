# -*- coding: utf-8 -*-
"""G06-2 single-instance 锁测试：进程/线程双保险幂等（对应指南 G06-2）。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.single_instance import SingleInstanceLock, _free_lock


class TestSingleInstance(unittest.TestCase):
    def test_acquire_and_release_roundtrip(self):
        d = Path(tempfile.mkdtemp())
        lock = SingleInstanceLock(d / "app.lock")
        self.assertTrue(lock.try_acquire(), "首次应能获得锁")
        # 同进程再次获取（进程内同锁）→ 应失败（视为已有实例）
        lock2 = SingleInstanceLock(d / "app.lock")
        self.assertFalse(lock2.try_acquire(), "第二次同进程获取应失败")
        lock.release()
        # 释放后可再获锁
        lock3 = SingleInstanceLock(d / "app.lock")
        self.assertTrue(lock3.try_acquire(), "释放后应能再获锁")
        lock3.release()

    def test_pid_file_content(self):
        d = Path(tempfile.mkdtemp())
        lock = SingleInstanceLock(d / "app.lock")
        self.assertTrue(lock.try_acquire())
        # pid 哨兵在 try_acquire 成功后才写入
        content = (d / "app.lock.pid").read_text(encoding="utf-8").strip()
        self.assertEqual(int(content), os.getpid())
        lock.release()

    def test_cleanup(self):
        d = Path(tempfile.mkdtemp())
        lock = SingleInstanceLock(d / "app.lock")
        _free_lock(lock)  # 不应抛异常（幂等清理）
        _free_lock(lock)


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestSingleInstanceCrossProcess(unittest.TestCase):
    """跨进程争锁 E2E：真实两个 Python 进程。"""

    def test_second_process_blocked(self):
        import subprocess
        import sys as _sys
        import tempfile
        import time
        d = Path(tempfile.mkdtemp())
        lockpath = d / "app.lock"
        script = (
            "import sys, time\n"
            "sys.path.insert(0, r'%s')\n"
            "from gcm.app.single_instance import SingleInstanceLock\n"
            "lock = SingleInstanceLock(r'%s')\n"
            "ok = lock.try_acquire()\n"
            "print('acquired:', ok, flush=True)\n"
            "if ok:\n"
            "    time.sleep(4)\n"
            "    lock.release()\n"
        ) % (str(ROOT), str(lockpath))
        p1 = subprocess.Popen([_sys.executable, "-c", script],
                              stdout=subprocess.PIPE, text=True)
        try:
            time.sleep(1.2)  # p1 已持锁
            r2 = subprocess.run([_sys.executable, "-c", script],
                                capture_output=True, text=True, timeout=10)
            self.assertIn("acquired: False", r2.stdout)
            p1.wait(timeout=10)
        finally:
            # 兜底回收 p1 输出流，避免 ResourceWarning
            try:
                p1.communicate(timeout=5)
            except Exception:
                try:
                    p1.kill()
                except Exception:
                    pass
