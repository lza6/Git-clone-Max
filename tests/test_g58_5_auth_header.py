"""G58-5 认证头类型选择测试：service 头注入映射 + settings_panel 下拉保存。"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _svc(host="mygit.example.com", hat=None, mapped="tok123", token=""):
    from gcm.git.service import GitService
    return GitService(
        tempfile.mkdtemp(), token=token,
        host_tokens={host: mapped} if mapped else {},
        host_auth_types=hat or {},
        single_branch=False, force_ipv4=False)


class TestHeaderMapping(unittest.TestCase):
    def test_default_bearer(self):
        svc = _svc()
        svc._current_host = "mygit.example.com"
        env = svc._env()
        self.assertIsNotNone(env)
        # 找到 http.extraHeader 的值
        vals = {env[f"GIT_CONFIG_KEY_{i}"]: env.get(f"GIT_CONFIG_VALUE_{i}")
                for i in range(int(env["GIT_CONFIG_COUNT"]))}
        self.assertEqual(vals["http.extraHeader"], "Authorization: Bearer tok123")

    def test_private_token(self):
        svc = _svc(hat={"mygit.example.com": "private_token"})
        svc._current_host = "mygit.example.com"
        env = svc._env()
        vals = {env[f"GIT_CONFIG_KEY_{i}"]: env.get(f"GIT_CONFIG_VALUE_{i}")
                for i in range(int(env["GIT_CONFIG_COUNT"]))}
        self.assertEqual(vals["http.extraHeader"], "PRIVATE-TOKEN: tok123")

    def test_basic_base64(self):
        import base64
        svc = _svc(hat={"mygit.example.com": "basic"})
        svc._current_host = "mygit.example.com"
        env = svc._env()
        vals = {env[f"GIT_CONFIG_KEY_{i}"]: env.get(f"GIT_CONFIG_VALUE_{i}")
                for i in range(int(env["GIT_CONFIG_COUNT"]))}
        expect = "Authorization: Basic " + base64.b64encode(b"tok123").decode("ascii")
        self.assertEqual(vals["http.extraHeader"], expect)

    def test_host_not_registered_no_header(self):
        svc = _svc(mapped="")  # host_tokens 无该 host
        svc._current_host = "mygit.example.com"
        env = svc._env()
        # 无 token 且无代理 → None
        self.assertIsNone(env)


class TestSettingsUiHeaderType(unittest.TestCase):
    def _win(self, store=None):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        store = store or SettingsStore(Path(tempfile.mkdtemp()) / "s.json")
        return MainWindow(data_dir=Path(tempfile.mkdtemp()), db=Database(":memory:"),
                          settings=store)

    def test_combo_exists_and_default_auto(self):
        w = self._win()
        try:
            self.assertTrue(hasattr(w, "cb_auth_header"))
            self.assertEqual(w.cb_auth_header.currentIndex(), 0)  # 自动
        finally:
            w.close()

    def test_save_bearer(self):
        w = self._win()
        try:
            w.cb_auth_header.setCurrentIndex(1)  # Bearer
            hat = dict(w.settings.host_auth_types or {})
            self.assertEqual(hat.get("*"), "bearer")
        finally:
            w.close()

    def test_save_basic_roundtrip(self):
        from gcm.db.settings import SettingsStore
        store = SettingsStore(Path(tempfile.mkdtemp()) / "s.json")
        w = self._win(store)
        try:
            w.cb_auth_header.setCurrentIndex(3)  # Basic
            hat = dict(w.settings.host_auth_types or {})
            self.assertEqual(hat.get("*"), "basic")
            # 重新构造窗口应回读 Basic（同一 store）
            w2 = self._win(store)
            try:
                self.assertEqual(w2.cb_auth_header.currentIndex(), 3)
            finally:
                w2.close()
        finally:
            w.close()


if __name__ == "__main__":
    unittest.main()