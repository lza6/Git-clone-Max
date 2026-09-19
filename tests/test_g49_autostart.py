# -*- coding: utf-8 -*-
"""G49-6 开机自启：命令构造（含 --minimized）+ schtasks 写入（mock 子进程）+ 非 Windows 安全。"""
from __future__ import annotations
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

import gcm.app.autostart as autostart


class TestLaunchCommand(unittest.TestCase):
    def test_frozen_without_minimized(self):
        with mock.patch.object(autostart.sys, "frozen", True, create=True), \
             mock.patch.object(autostart.sys, "executable", "C:/x/Git-clone-Max.exe"):
            cmd = autostart.launch_command(minimized=False)
        self.assertEqual(cmd, [str(Path("C:/x/Git-clone-Max.exe").resolve())])

    def test_frozen_with_minimized(self):
        with mock.patch.object(autostart.sys, "frozen", True, create=True), \
             mock.patch.object(autostart.sys, "executable", "C:/x/Git-clone-Max.exe"):
            cmd = autostart.launch_command(minimized=True)
        self.assertIn("--minimized", cmd)

    def test_source_command_has_minimized(self):
        with mock.patch.object(autostart.sys, "frozen", False, create=True), \
             mock.patch.object(autostart.sys, "executable", sys.executable):
            cmd = autostart.launch_command(minimized=True)
        self.assertEqual(cmd[:3], [sys.executable, "-m", "gcm"])
        self.assertIn("--minimized", cmd)


class TestSetAutostart(unittest.TestCase):
    def test_non_windows_skips(self):
        with mock.patch.object(autostart.os, "name", "posix"):
            ok, msg = autostart.set_autostart(True, minimized=True)
        self.assertFalse(ok)
        self.assertIn("仅支持 Windows", msg)

    @unittest.skipIf(os.name != "nt", "仅 Windows 上验证 schtasks 命令形态")
    def test_windows_create_includes_minimized(self):
        fake = mock.Mock()
        fake.return_value.returncode = 0
        fake.return_value.stderr = ""
        fake.return_value.stdout = "ok"
        with mock.patch.object(autostart.subprocess, "run", fake) as run:
            ok, msg = autostart.set_autostart(True, minimized=True)
        self.assertTrue(ok)
        call = run.call_args
        self.assertIn("/TR", call.args[0])
        tr_idx = call.args[0].index("/TR")
        self.assertIn("--minimized", call.args[0][tr_idx + 1])
        self.assertEqual(call.kwargs.get("timeout", 30), 30)

    @unittest.skipIf(os.name != "nt", "仅 Windows 上验证 schtasks 命令形态")
    def test_windows_delete(self):
        fake = mock.Mock()
        fake.return_value.returncode = 0
        fake.return_value.stderr = ""
        fake.return_value.stdout = "ok"
        with mock.patch.object(autostart.subprocess, "run", fake) as run:
            ok, msg = autostart.set_autostart(False)
        self.assertTrue(ok)
        self.assertIn("/Delete", run.call_args.args[0])

    @unittest.skipIf(os.name != "nt", "仅 Windows 上验证失败路径")
    def test_windows_failure_reports(self):
        fake = mock.Mock()
        fake.return_value.returncode = 1
        fake.return_value.stderr = "access denied"
        fake.return_value.stdout = ""
        with mock.patch.object(autostart.subprocess, "run", fake):
            ok, msg = autostart.set_autostart(True)
        self.assertFalse(ok)
        self.assertIn("access denied", msg)

    def test_exception_safe(self):
        with mock.patch.object(autostart.subprocess, "run",
                               side_effect=Exception("boom")), \
             mock.patch.object(autostart.os, "name", "nt"):
            ok, msg = autostart.set_autostart(True)
        self.assertFalse(ok)
        self.assertIn("boom", msg)
