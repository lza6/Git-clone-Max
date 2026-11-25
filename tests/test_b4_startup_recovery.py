# -*- coding: utf-8 -*-
"""B4 启动失败恢复测试：信息收集 / 设置备份重置 / 诊断包 / 对话框可构造。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication

from gcm.ui.startup_recovery import (StartupRecoveryDialog,
                                     collect_recovery_info,
                                     export_recovery_bundle,
                                     reset_settings_with_backup)

_APP = QApplication.instance() or QApplication([])


class TestRecoveryHelpers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="b4_recovery_")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_collect_info(self):
        info = collect_recovery_info(self.dir, "boom")
        self.assertEqual(info["data_dir"], str(self.dir))
        self.assertIn("app.log", info["app_log"])
        self.assertEqual(info["error"], "boom")
        self.assertTrue(info["version"])

    def test_reset_settings_with_backup(self):
        src = self.dir / "settings.json"
        src.write_text('{"token": "keep-me"}', encoding="utf-8")
        backup = reset_settings_with_backup(self.dir)
        self.assertIsNotNone(backup)
        assert backup is not None
        self.assertTrue(backup.exists(), "备份文件必须保留（只改名不删除）")
        self.assertFalse(src.exists(), "原文件应已改名")
        self.assertIn("keep-me", backup.read_text(encoding="utf-8"))
        self.assertIn("settings.corrupt-", backup.name)
        self.assertIsNone(reset_settings_with_backup(self.dir),
                          "无 settings.json 时返回 None")

    def test_export_bundle_contains_error_and_redacts(self):
        (self.dir / "app.log").write_text(
            "line http://h/p?token=SECRET123 https://user:pw@host/x\n",
            encoding="utf-8")
        path = export_recovery_bundle(self.dir, "Traceback: boom")
        self.assertTrue(path.exists())
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            self.assertIn("manifest.json", names)
            self.assertIn("report.txt", names)
            report = zf.read("report.txt").decode("utf-8")
            self.assertIn("startup recovery", report)
            app_log = zf.read("app.log").decode("utf-8")
        joined = report + app_log
        self.assertNotIn("SECRET123", joined, "诊断包必须打码日志中的 token")
        self.assertNotIn("user:pw", joined, "诊断包必须打码 URL 内嵌凭据")


class TestRecoveryDialog(unittest.TestCase):
    def test_dialog_constructs_with_actions(self):
        d = Path(tempfile.mkdtemp(prefix="b4_dialog_"))
        dlg = StartupRecoveryDialog(d, "Traceback: boom")
        try:
            self.assertEqual(dlg.windowTitle(), "启动失败")
            self.assertIn("重置设置", dlg._btn_reset.text())
            for btn in (dlg._btn_open, dlg._btn_export, dlg._btn_reset,
                        dlg._btn_quit):
                self.assertTrue(btn.isEnabled())
        finally:
            dlg.close()


class TestStartupRecoveryWiring(unittest.TestCase):
    """接线测试：主窗口构造抛异常时，入口必须弹恢复对话框、返回 1、释放单实例锁。"""

    def test_build_failure_runs_recovery_and_releases_lock(self):
        from unittest import mock

        import PyQt6.QtWidgets as qtw

        import gcm.__main__ as entry
        import gcm.app.single_instance as si
        import gcm.ui.startup_recovery as recovery

        tmp = Path(tempfile.mkdtemp(prefix="b4_wiring_"))
        events: dict[str, bool] = {}

        class _Lock:
            def try_acquire(self) -> bool:
                return True

            def release(self) -> None:
                events["released"] = True

        with mock.patch.object(entry, "get_data_dir", return_value=tmp), \
             mock.patch.object(entry, "_enable_event_sink"), \
             mock.patch.object(entry, "_init_applog"), \
             mock.patch.object(si, "SingleInstanceLock", return_value=_Lock()), \
             mock.patch.object(qtw, "QApplication",
                               return_value=mock.MagicMock()), \
             mock.patch.object(entry, "_build_gui_shell",
                               side_effect=RuntimeError("boom-startup")), \
             mock.patch.object(recovery, "run_recovery_dialog") as dlg, \
             mock.patch("gcm.app.exc_dump.install_excepthook"):
            rc = entry.main([])
        self.assertEqual(rc, 1, "启动失败必须返回非 0")
        dlg.assert_called_once()
        self.assertIn("boom-startup", str(dlg.call_args.args[2]),
                      "对话框必须拿到真实 traceback 文本")
        self.assertTrue(events.get("released"), "异常路径必须释放单实例锁")


if __name__ == "__main__":
    unittest.main(verbosity=2)
