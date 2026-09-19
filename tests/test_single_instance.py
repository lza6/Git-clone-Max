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

class TestSingleInstanceEdgeBranches(unittest.TestCase):
    """G45-3 补充：PID 探活边界 / 哨兵损坏 / 残留清理分支。"""

    def test_pid_alive_invalid_and_dead(self):
        from gcm.app.single_instance import _pid_alive
        self.assertFalse(_pid_alive(0))
        self.assertFalse(_pid_alive(-1))
        self.assertFalse(_pid_alive(99999999))  # 不存在的 PID

    def test_pid_alive_current_process(self):
        from gcm.app.single_instance import _pid_alive
        self.assertTrue(_pid_alive(os.getpid()))

    def test_pid_file_stale_corrupted(self):
        from gcm.app.single_instance import SingleInstanceLock
        d = Path(tempfile.mkdtemp())
        lock = SingleInstanceLock(d / "app.lock")
        # 无 pid 文件 → stale True（可清理）
        self.assertTrue(lock._pid_file_stale())
        # 非数字内容 → stale True
        lock.pid_file.write_text("not-a-pid", encoding="utf-8")
        self.assertTrue(lock._pid_file_stale())
        # 空内容 → stale True
        lock.pid_file.write_text("", encoding="utf-8")
        self.assertTrue(lock._pid_file_stale())
        # 超大数值 PID（不可信/已死）→ stale True
        lock.pid_file.write_text("99999999", encoding="utf-8")
        self.assertTrue(lock._pid_file_stale())

    def test_pid_file_live_pid_not_stale(self):
        from gcm.app.single_instance import SingleInstanceLock
        d = Path(tempfile.mkdtemp())
        lock = SingleInstanceLock(d / "app.lock")
        lock.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        self.assertFalse(lock._pid_file_stale())

    def test_stale_cleanup_retries(self):
        """锁不可得 + 哨兵已死 → 清理后重试一次（跨平台确定性：mock 锁底层）。"""
        from unittest import mock as _mock
        from gcm.app.single_instance import SingleInstanceLock
        d = Path(tempfile.mkdtemp())
        lock_path = d / "app.lock"
        lock_path.write_text("", encoding="utf-8")
        lock_path.with_name(lock_path.name + ".pid").write_text(
            "99999999", encoding="utf-8")
        lock = SingleInstanceLock(lock_path)
        with _mock.patch.object(lock, "_try_lock_file",
                                 side_effect=[False, True]) as m_lock:
            ok = lock.try_acquire()
        self.assertTrue(ok, "残留哨兵清理后应重试成功")
        self.assertEqual(m_lock.call_count, 2, "首次锁失败后应恰好重试一次")
        self.assertEqual(int(lock.pid_file.read_text(encoding="utf-8")),
                         os.getpid(), "成功获取后应重写当前 PID 哨兵")
        lock.release()
        self.assertFalse(lock.pid_file.exists(), "release 后应删除哨兵")

class TestSingleInstancePosixAndEdge(unittest.TestCase):
    """G45-3 补充：POSIX 分支（fcntl/os.kill 模拟）与重试失败分支。

    本机 os.name == nt；POSIX 分支通过 patch os.name + sys.modules 注入 mock fcntl
    确定性覆盖，patch 作用域内自动还原，不影响其余平台测试。
    """

    def test_posix_pid_alive_and_lock_flow(self):
        from unittest import mock as _mock
        import types as _types
        from gcm.app.single_instance import SingleInstanceLock, _pid_alive
        fcntl_mod = _types.ModuleType("fcntl")
        fcntl_mod.LOCK_EX = 2
        fcntl_mod.LOCK_UN = 8
        fcntl_mod.LOCK_NB = 4
        fcntl_mod.flock = _mock.Mock()
        os_kill = _mock.Mock(return_value=True)
        with _mock.patch.dict(sys.modules, {"fcntl": fcntl_mod}), \
             _mock.patch.object(os, "name", "posix"), \
             _mock.patch.object(os, "kill", os_kill):
            self.assertTrue(_pid_alive(123), "posix 存活 PID 应返回 True")
            os_kill.assert_called_once_with(123, 0)
            self.assertFalse(_pid_alive(0), "pid<=0 应返回 False")
        # Path 需在 os.name patch 作用域外创建（pathlib 按 os.name 选类型）
        d = Path(tempfile.mkdtemp())
        lock = SingleInstanceLock(d / "app.lock")  # 构造需在 os.name patch 外
        with _mock.patch.dict(sys.modules, {"fcntl": fcntl_mod}), \
             _mock.patch.object(os, "name", "posix"), \
             _mock.patch.object(os, "kill", os_kill):
            self.assertTrue(lock.try_acquire(), "posix flock 分支应能获锁")
            self.assertTrue(fcntl_mod.flock.called, "应调用 flock")
            lock.release()
            self.assertEqual(fcntl_mod.flock.call_count, 2,
                             "获锁+释放各一次 flock")

    def test_stale_cleanup_retry_fails(self):
        from unittest import mock as _mock
        from gcm.app.single_instance import SingleInstanceLock
        d = Path(tempfile.mkdtemp())
        lock_path = d / "app.lock"
        lock_path.write_text("", encoding="utf-8")
        lock_path.with_name(lock_path.name + ".pid").write_text(
            "99999999", encoding="utf-8")
        lock = SingleInstanceLock(lock_path)
        with _mock.patch.object(lock, "_try_lock_file", side_effect=[False, False]):
            ok = lock.try_acquire()
        self.assertFalse(ok, "清理后重试仍失败应返回 False")
