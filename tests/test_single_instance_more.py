# -*- coding: utf-8 -*-
"""G50-7 single-instance 补充覆盖：损坏锁文件、PID 探活、异常回退与 POSIX 分支。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gcm.app.single_instance as si
from gcm.app.single_instance import SingleInstanceLock, _free_lock


def _lockdir() -> Path:
    return Path(tempfile.mkdtemp())


class TestPidAlive(unittest.TestCase):
    def test_pid_alive_current_and_dead(self):
        self.assertTrue(si._pid_alive(os.getpid()))
        self.assertFalse(si._pid_alive(0))
        self.assertFalse(si._pid_alive(-1))
        self.assertFalse(si._pid_alive(999_999_999))
        self.assertFalse(si._pid_alive(2_147_483_647))

    def test_pid_alive_exception_fallback(self):
        with mock.patch("os.name", "posix"), \
             mock.patch("os.kill", side_effect=OSError("no such pid")):
            self.assertFalse(si._pid_alive(os.getpid()))

    def test_pid_alive_posix_kill_ok(self):
        with mock.patch("os.name", "posix"), \
             mock.patch("os.kill", return_value=None):
            self.assertTrue(si._pid_alive(os.getpid()))


class TestSingleInstanceLockMore(unittest.TestCase):
    def test_corrupted_pid_file_stale_and_takeover(self):
        d = _lockdir()
        lock = SingleInstanceLock(d / "app.lock")
        lock.pid_file.write_text("not-a-pid", encoding="utf-8")
        self.assertTrue(lock._pid_file_stale())
        with mock.patch.object(lock, "_try_lock_file", side_effect=[False, True]) as tlf:
            self.assertTrue(lock.try_acquire())
        tlf.assert_has_calls([mock.call(), mock.call()])
        self.assertTrue(lock._owned)
        self.assertEqual(lock.pid_file.read_text(encoding="utf-8").strip(), str(os.getpid()))
        lock.release()

    def test_dead_pid_sentinel_takeover(self):
        d = _lockdir()
        lock = SingleInstanceLock(d / "app.lock")
        lock.pid_file.write_text("123456789", encoding="utf-8")
        with mock.patch.object(lock, "_try_lock_file", side_effect=[False, True]) as tlf, \
             mock.patch.object(si, "_pid_alive", return_value=False):
            self.assertTrue(lock.try_acquire())
        tlf.assert_has_calls([mock.call(), mock.call()])
        self.assertTrue(lock._owned)
        lock.release()

    def test_alive_pid_sentinel_rejects(self):
        d = _lockdir()
        lock = SingleInstanceLock(d / "app.lock")
        lock.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        with mock.patch.object(lock, "_try_lock_file", return_value=False) as tlf, \
             mock.patch.object(si, "_pid_alive", return_value=True):
            self.assertFalse(lock.try_acquire())
        tlf.assert_called_once()
        self.assertFalse(lock._owned)

    def test_stale_but_retry_fails(self):
        d = _lockdir()
        lock = SingleInstanceLock(d / "app.lock")
        lock.pid_file.write_text("123456789", encoding="utf-8")
        with mock.patch.object(lock, "_try_lock_file", return_value=False) as tlf, \
             mock.patch.object(si, "_pid_alive", return_value=False):
            self.assertFalse(lock.try_acquire())
        self.assertEqual(tlf.call_count, 2)
        self.assertFalse(lock._owned)

    def test_unlink_failures_still_recover(self):
        d = _lockdir()
        lock = SingleInstanceLock(d / "app.lock")
        lock.pid_file.write_text("123456789", encoding="utf-8")
        with mock.patch.object(lock, "_try_lock_file", side_effect=[False, True]), \
             mock.patch.object(si, "_pid_alive", return_value=False), \
             mock.patch.object(Path, "unlink", side_effect=OSError("locked")):
            self.assertTrue(lock.try_acquire())
        self.assertTrue(lock._owned)
        lock.release()

    def test_write_pid_failure_swallowed(self):
        d = _lockdir()
        lock = SingleInstanceLock(d / "app.lock")
        lock.pid_file = mock.Mock()
        lock.pid_file.write_text.side_effect = OSError("disk full")
        self.assertTrue(lock.try_acquire())
        self.assertTrue(lock._owned)
        lock.release()

    def test_release_and_reacquire(self):
        d = _lockdir()
        a = SingleInstanceLock(d / "app.lock")
        self.assertTrue(a.try_acquire())
        a.release()
        b = SingleInstanceLock(d / "app.lock")
        self.assertTrue(b.try_acquire())
        b.release()

    def test_pid_file_stale_missing_or_unreadable(self):
        d = _lockdir()
        lock = SingleInstanceLock(d / "app.lock")
        self.assertTrue(lock._pid_file_stale())
        lock.pid_file.write_text("abc", encoding="utf-8")
        self.assertTrue(lock._pid_file_stale())
        lock.pid_file = mock.Mock()
        lock.pid_file.exists.return_value = True
        lock.pid_file.read_text.side_effect = OSError("io")
        self.assertTrue(lock._pid_file_stale())
        lock = SingleInstanceLock(d / "app.lock")
        lock.pid_file.write_text(str(os.getpid()), encoding="utf-8")
        with mock.patch.object(si, "_pid_alive", return_value=True):
            self.assertFalse(lock._pid_file_stale())
        with mock.patch.object(si, "_pid_alive", return_value=False):
            self.assertTrue(lock._pid_file_stale())

    def test_try_lock_file_open_failure(self):
        d = _lockdir()
        lock = SingleInstanceLock(d / "app.lock")
        with mock.patch("builtins.open", side_effect=OSError("denied")):
            self.assertFalse(lock._try_lock_file())

    def test_try_lock_file_second_instance_blocked(self):
        d = _lockdir()
        a = SingleInstanceLock(d / "app.lock")
        self.assertTrue(a.try_acquire())
        try:
            b = SingleInstanceLock(d / "app.lock")
            self.assertFalse(b._try_lock_file())
        finally:
            a.release()

    def test_release_close_failure(self):
        d = _lockdir()
        lock = SingleInstanceLock(d / "app.lock")
        self.assertTrue(lock.try_acquire())
        with mock.patch.object(lock.lock_fh, "close", side_effect=OSError("close fail")):
            lock.release()
        self.assertIsNone(lock.lock_fh)
        self.assertFalse(lock.pid_file.exists())
        self.assertFalse(lock._owned)

    def test_release_without_lock(self):
        d = _lockdir()
        lock = SingleInstanceLock(d / "app.lock")
        lock.release()
        self.assertFalse(lock._owned)


class _FakeFcntl:
    LOCK_EX = 2
    LOCK_NB = 4
    LOCK_UN = 8

    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    def flock(self, fd, op):
        self.calls.append(op)
        if self.fail:
            raise OSError("would block")


class TestPosixBranches(unittest.TestCase):
    def test_posix_acquire_and_release_success(self):
        d = Path(tempfile.mkdtemp())
        fake = _FakeFcntl()
        lock = SingleInstanceLock(d / "app.lock")  # 先以 WindowsPath 建锁
        with mock.patch.dict(sys.modules, {"fcntl": fake}), \
             mock.patch("os.name", "posix"):
            self.assertTrue(lock.try_acquire())
            self.assertIsNotNone(lock.lock_fh)
            lock.release()
            self.assertIsNone(lock.lock_fh)
        self.assertEqual(len(fake.calls), 2)

    def test_posix_blocked(self):
        d = Path(tempfile.mkdtemp())
        fake = _FakeFcntl(fail=True)
        lock = SingleInstanceLock(d / "app.lock")  # 先以 WindowsPath 建锁
        with mock.patch.dict(sys.modules, {"fcntl": fake}), \
             mock.patch("os.name", "posix"):
            self.assertFalse(lock._try_lock_file())


class TestFreeLock(unittest.TestCase):
    def test_free_lock_idempotent_with_failures(self):
        d = Path(tempfile.mkdtemp())
        pid = d / "app.lock.pid"
        pid.write_text("1", encoding="utf-8")
        fh = mock.Mock()
        fh.close.side_effect = OSError("close fail")
        lock = mock.Mock()
        lock.release.side_effect = RuntimeError("release fail")
        lock.lock_fh = fh
        lock.pid_file = pid
        _free_lock(lock)
        _free_lock(lock)

    def test_free_lock_happy_path(self):
        d = Path(tempfile.mkdtemp())
        lock = SingleInstanceLock(d / "app.lock")
        self.assertTrue(lock.try_acquire())
        _free_lock(lock)
        self.assertIsNone(lock.lock_fh)
        self.assertFalse(lock._owned)
        self.assertFalse(lock.pid_file.exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
