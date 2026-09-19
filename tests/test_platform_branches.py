# -*- coding: utf-8 -*-
"""G50-3 平台分支跨平台覆盖：single_instance(msvcrt/ctypes) / autostart(schtasks) /
download_tools(startfile) 的 Windows 专属分支用 mock 在全部平台执行，消除 POSIX 覆盖率缺口。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestSingleInstanceWindowsBranch(unittest.TestCase):
    """os.name 掩为 nt + 假 msvcrt/ctypes：Windows 分支在任意平台执行。"""

    def _fake_msvcrt(self, fail_lock=False):
        msvcrt = mock.MagicMock()
        if fail_lock:
            msvcrt.locking.side_effect = OSError("locked")
        return msvcrt

    def test_try_lock_file_nt_success(self):
        from gcm.app.single_instance import SingleInstanceLock
        msvcrt = self._fake_msvcrt()
        lock = SingleInstanceLock(Path(tempfile.mkdtemp()) / "x.lock")
        with mock.patch("os.name", "nt"), mock.patch.dict(sys.modules, {"msvcrt": msvcrt}):
            self.assertTrue(lock._try_lock_file())
        self.assertEqual(msvcrt.locking.call_count, 1)
        lock.lock_fh.close()

    def test_try_lock_file_nt_blocked(self):
        from gcm.app.single_instance import SingleInstanceLock
        msvcrt = self._fake_msvcrt(fail_lock=True)
        lock = SingleInstanceLock(Path(tempfile.mkdtemp()) / "x.lock")
        with mock.patch("os.name", "nt"), mock.patch.dict(sys.modules, {"msvcrt": msvcrt}):
            self.assertFalse(lock._try_lock_file())
        self.assertIsNone(lock.lock_fh)

    def test_release_nt_unlock_and_close(self):
        from gcm.app.single_instance import SingleInstanceLock
        msvcrt = self._fake_msvcrt()
        lock = SingleInstanceLock(Path(tempfile.mkdtemp()) / "x.lock")
        with mock.patch("os.name", "nt"), mock.patch.dict(sys.modules, {"msvcrt": msvcrt}):
            lock._try_lock_file()
            lock.release()
        self.assertEqual(msvcrt.locking.call_count, 2)  # lock + unlock
        self.assertIsNone(lock.lock_fh)

    def test_release_nt_unlock_failure_still_closes(self):
        from gcm.app.single_instance import SingleInstanceLock
        msvcrt = mock.MagicMock()
        msvcrt.locking.side_effect = OSError("unlock fail")
        lock = SingleInstanceLock(Path(tempfile.mkdtemp()) / "x.lock")
        with mock.patch("os.name", "nt"), mock.patch.dict(sys.modules, {"msvcrt": msvcrt}):
            lock.lock_fh = open(lock.path, "a+")
            lock.release()
        self.assertIsNone(lock.lock_fh)

    def test_pid_alive_nt(self):
        from gcm.app.single_instance import _pid_alive
        fake_ctypes = mock.MagicMock()
        fake_ctypes.windll.kernel32.OpenProcess.return_value = 123
        fake_ctypes.c_ulong.return_value.value = 259  # STILL_ACTIVE
        with mock.patch("os.name", "nt"), mock.patch.dict(sys.modules, {"ctypes": fake_ctypes}):
            self.assertTrue(_pid_alive(42))
        fake_ctypes.c_ulong.return_value.value = 0
        with mock.patch("os.name", "nt"), mock.patch.dict(sys.modules, {"ctypes": fake_ctypes}):
            self.assertFalse(_pid_alive(42))
        fake_ctypes.windll.kernel32.OpenProcess.return_value = 0
        with mock.patch("os.name", "nt"), mock.patch.dict(sys.modules, {"ctypes": fake_ctypes}):
            self.assertFalse(_pid_alive(42))
        fake_ctypes.windll.kernel32.OpenProcess.side_effect = OSError("denied")
        with mock.patch("os.name", "nt"), mock.patch.dict(sys.modules, {"ctypes": fake_ctypes}):
            self.assertFalse(_pid_alive(42))


class TestAutostartWindowsBranch(unittest.TestCase):
    """os.name 掩为 nt：schtasks 写入/删除/查询全分支。"""

    def test_launch_command_frozen_minimized(self):
        from gcm.app.autostart import launch_command
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch("sys.executable", "C:/x/Git-clone-Max.exe"):
            self.assertEqual(launch_command(minimized=True),
                             [str(Path("C:/x/Git-clone-Max.exe").resolve()), "--minimized"])

    def test_set_autostart_enable_nt(self):
        from gcm.app.autostart import TASK_NAME, set_autostart
        with mock.patch("os.name", "nt"), mock.patch("subprocess.run") as m_run:
            m_run.return_value.returncode = 0
            ok, msg = set_autostart(True, minimized=True)
        self.assertTrue(ok)
        args = m_run.call_args.args[0]
        self.assertIn("/Create", args)
        self.assertEqual(args[args.index("/TN") + 1], TASK_NAME)
        self.assertIn("--minimized", args[args.index("/TR") + 1])

    def test_set_autostart_disable_nt(self):
        from gcm.app.autostart import set_autostart
        with mock.patch("os.name", "nt"), mock.patch("subprocess.run") as m_run:
            m_run.return_value.returncode = 0
            ok, _ = set_autostart(False)
        self.assertTrue(ok)
        self.assertIn("/Delete", m_run.call_args.args[0])

    def test_set_autostart_failures(self):
        from gcm.app.autostart import autostart_enabled, set_autostart
        with mock.patch("os.name", "nt"), mock.patch("subprocess.run") as m_run:
            m_run.return_value.returncode = 1
            m_run.return_value.stderr = "denied"
            ok, msg = set_autostart(True)
        self.assertFalse(ok)
        self.assertIn("denied", msg)
        with mock.patch("os.name", "nt"), mock.patch("subprocess.run", side_effect=FileNotFoundError):
            ok, msg = set_autostart(True)
        self.assertFalse(ok)
        self.assertIn("schtasks", msg)
        with mock.patch("os.name", "nt"), mock.patch("subprocess.run", side_effect=OSError("boom")):
            ok, msg = set_autostart(True)
        self.assertFalse(ok)
        self.assertIn("boom", msg)
        with mock.patch("os.name", "nt"), mock.patch("subprocess.run") as m_run:
            m_run.return_value.returncode = 0
            self.assertTrue(autostart_enabled())
            m_run.return_value.returncode = 1
            self.assertFalse(autostart_enabled())
        with mock.patch("os.name", "nt"), mock.patch("subprocess.run", side_effect=OSError("q")):
            self.assertFalse(autostart_enabled())


class TestDownloadToolsWindowsBranch(unittest.TestCase):
    """sys.platform 掩为 win32：open_target 走 startfile 分支。"""

    def test_open_target_win32_branch(self):
        from gcm.ui.download_tools import open_target
        owner = mock.MagicMock()
        owner.target_edit.text.return_value = str(Path(tempfile.mkdtemp()))
        with mock.patch("sys.platform", "win32"), \
                mock.patch("os.startfile", create=True) as m_start, \
                mock.patch("gcm.ui.download_tools._msgbox"):
            open_target(owner)
        m_start.assert_called_once()


if __name__ == "__main__":
    unittest.main(verbosity=2)
