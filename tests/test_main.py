# -*- coding: utf-8 -*-
"""G45-3：gcm/__main__.py 覆盖率补测。

覆盖 get_data_dir 三分支（GCM_DATA_DIR 环境变量 / sys.frozen 打包 / 源码模式），
以及 main() 冒烟（mock 全部依赖，断言调用顺序与返回值，含第二实例被拦截 return 0）。
不真实创建 QApplication：以 sys.modules 注入 mock 的 PyQt6.QtWidgets / PyQt6.QtCore。
"""
from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gcm.__main__ as main_mod


def _mock_module(name: str, **attrs) -> types.ModuleType:
    mod = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(mod, k, v)
    return mod


class TestGetDataDir(unittest.TestCase):
    """G49-2 get_data_dir() 分支：GCM_DATA_DIR 优先 / portable / 默认 APPDATA / 旧 data 沿用。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_main_"))
        self._old_env = os.environ.pop("GCM_DATA_DIR", None)
        self._old_app = os.environ.get("APPDATA")
        os.environ.pop("APPDATA", None)
        os.environ.pop("XDG_DATA_HOME", None)

    def tearDown(self):
        if self._old_env is not None:
            os.environ["GCM_DATA_DIR"] = self._old_env
        else:
            os.environ.pop("GCM_DATA_DIR", None)
        if self._old_app is not None:
            os.environ["APPDATA"] = self._old_app
        else:
            os.environ.pop("APPDATA", None)
        os.environ.pop("XDG_DATA_HOME", None)

    def test_env_var_branch(self):
        """GCM_DATA_DIR 优先：创建目录并返回。"""
        target = self.tmp / "custom_data"
        os.environ["GCM_DATA_DIR"] = str(target)
        out = main_mod.get_data_dir()
        self.assertEqual(out, target)
        self.assertTrue(target.is_dir())

    def test_portable_branch(self):
        """portable=True：取 exe 同级 data（随程序移动的现状默认）。"""
        fake_exe = self.tmp / "bin" / "Git-clone-Max.exe"
        fake_exe.parent.mkdir(parents=True)
        with mock.patch.object(main_mod.sys, "frozen", True, create=True), \
             mock.patch.object(main_mod.sys, "executable", str(fake_exe)):
            out = main_mod.get_data_dir(portable=True)
        self.assertEqual(out, fake_exe.parent / "data")
        self.assertTrue((fake_exe.parent / "data").is_dir())

    def test_default_appdata(self):
        """默认（非 portable、无旧 data）：%APPDATA%/Git-clone-Max。"""
        fake_root = self.tmp / "project"
        fake_main = fake_root / "gcm" / "__main__.py"
        fake_main.parent.mkdir(parents=True)
        fake_main.write_text("", encoding="utf-8")
        appdata = self.tmp / "AppData"
        os.environ["APPDATA"] = str(appdata)
        with mock.patch.object(main_mod, "__file__", str(fake_main)):
            out = main_mod.get_data_dir()
        self.assertEqual(out, appdata / "Git-clone-Max")
        self.assertTrue((appdata / "Git-clone-Max").is_dir())
        self.assertFalse((fake_root / "data").exists())

    def test_legacy_data_fallback(self):
        """默认但旧版 data 已存在 → 沿用（升级不丢数据）。"""
        fake_root = self.tmp / "project"
        (fake_root / "data").mkdir(parents=True)
        fake_main = fake_root / "gcm" / "__main__.py"
        fake_main.parent.mkdir(parents=True)
        fake_main.write_text("", encoding="utf-8")
        os.environ["APPDATA"] = str(self.tmp / "AppData")
        with mock.patch.object(main_mod, "__file__", str(fake_main)):
            out = main_mod.get_data_dir()
        self.assertEqual(out, fake_root.resolve() / "data")
        self.assertTrue((fake_root / "data").is_dir())


class _MainSmokeBase(unittest.TestCase):
    """main() 冒烟公共装配：sys.modules 注入 mock 依赖。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_mainsmoke_"))
        self.events: list[str] = []
        self.exec_result = 7
        self.lock_result = True

        self.qt_core = _mock_module("PyQt6.QtCore", Qt=mock.MagicMock())
        self.app = mock.MagicMock()
        self.app.exec.side_effect = lambda: (
            self.events.append("app.exec") or self.exec_result)

        def _qa(*a, **k):
            self.events.append("QApplication")
            return self.app
        self.qwidgets = _mock_module(
            "PyQt6.QtWidgets", QApplication=mock.MagicMock(side_effect=_qa))

        self.lock = mock.MagicMock()
        self.lock.try_acquire.side_effect = lambda: (
            self.events.append("try_acquire") or self.lock_result)
        self.lock.release.side_effect = lambda: self.events.append("release")

        def _lock_cls(*a, **k):
            self.events.append("SingleInstanceLock")
            return self.lock
        self.single_mod = _mock_module(
            "gcm.app.single_instance",
            SingleInstanceLock=mock.MagicMock(side_effect=_lock_cls))

        self.exc_mod = _mock_module(
            "gcm.app.exc_dump",
            install_excepthook=mock.MagicMock(
                side_effect=lambda *a: self.events.append("install_excepthook")))

        self.db = mock.MagicMock()
        self.settings = mock.MagicMock()
        self.win = mock.MagicMock()
        self.win.show.side_effect = lambda: self.events.append("win.show")
        self.win.should_auto_hide.return_value = False  # G49-6 默认弹主窗

        def _db_cls(*a, **k):
            self.events.append("Database")
            return self.db

        def _st_cls(*a, **k):
            self.events.append("SettingsStore")
            return self.settings

        def _mw_cls(*a, **k):
            self.events.append("MainWindow")
            return self.win

        self.db_mod = _mock_module(
            "gcm.db.repo_db", Database=mock.MagicMock(side_effect=_db_cls))
        self.settings_mod = _mock_module(
            "gcm.db.settings", SettingsStore=mock.MagicMock(side_effect=_st_cls))
        self.mw_mod = _mock_module(
            "gcm.ui.main_window", MainWindow=mock.MagicMock(side_effect=_mw_cls))

        self._mods_patch = mock.patch.dict(sys.modules, {
            "PyQt6.QtCore": self.qt_core,
            "PyQt6.QtWidgets": self.qwidgets,
            "gcm.app.single_instance": self.single_mod,
            "gcm.app.exc_dump": self.exc_mod,
            "gcm.db.repo_db": self.db_mod,
            "gcm.db.settings": self.settings_mod,
            "gcm.ui.main_window": self.mw_mod,
        })
        self._mods_patch.start()
        self._data_dir_patch = mock.patch.object(
            main_mod, "get_data_dir", return_value=self.tmp)
        self._data_dir_patch.start()

    def tearDown(self):
        self._data_dir_patch.stop()
        self._mods_patch.stop()

    def _assert_success_flow(self):
        self.assertEqual(self.events, [
            "SingleInstanceLock", "try_acquire", "install_excepthook",
            "QApplication", "Database", "SettingsStore", "MainWindow",
            "win.show", "app.exec", "release",
        ])


class TestMainSmoke(_MainSmokeBase):
    """main() 冒烟：成功路径 + 第二实例拦截。"""

    def test_main_success_flow(self):
        rc = main_mod.main()
        self.assertEqual(rc, self.exec_result)
        self.lock.release.assert_called_once()
        self.db_mod.Database.assert_called_once_with(self.tmp / "repos.db")
        self.settings_mod.SettingsStore.assert_called_once_with(
            self.tmp / "settings.json")
        self.exc_mod.install_excepthook.assert_called_once_with(
            self.tmp / "error.log")
        self.mw_mod.MainWindow.assert_called_once_with(
            data_dir=self.tmp, db=self.db, settings=self.settings,
            start_hidden=False)
        self.win.show.assert_called_once()
        self._assert_success_flow()

    def test_main_second_instance_blocked(self):
        self.lock_result = False
        with mock.patch("builtins.print") as m_print:
            rc = main_mod.main()
        self.assertEqual(rc, 0)
        m_print.assert_called_once()
        self.assertIn("已在运行", str(m_print.call_args))
        # 第二实例不再继续初始化
        self.exc_mod.install_excepthook.assert_not_called()
        self.qwidgets.QApplication.assert_not_called()
        self.lock.release.assert_not_called()
        self.assertEqual(self.events, ["SingleInstanceLock", "try_acquire"])


if __name__ == "__main__":
    unittest.main(verbosity=2)