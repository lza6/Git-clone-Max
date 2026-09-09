# -*- coding: utf-8 -*-
"""GitHub Token（私有仓库认证）落地测试。

覆盖：
- GitService._env：无 proxy 无 token → None；有 token 无 proxy → 注入
  GIT_CONFIG_COUNT/KEY_0/VALUE_0（http.extraHeader=Authorization: Bearer <tok>）
  且不含 http_proxy；有 proxy 无 token → 仅 proxy 变量、无 GIT_CONFIG。
- SyncEngine 构造传 token → engine._service().token == token。
- MainWindow：edit_token 初始值 = settings.token；_save_token() 更新 settings 并持久化。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication

from gcm.app.engine import SyncEngine
from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.git.service import GitService
from gcm.ui.main_window import MainWindow

_app = QApplication.instance() or QApplication(sys.argv)

# 测试用占位 token（绝不打印明文 / 不用真实凭据）
_FAKE_TOKEN = "ghp_sample_token_for_private_repo_test_0123456789"


class TestEnvToken(unittest.TestCase):
    """GitService._env 的 token 注入逻辑。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_no_proxy_no_token_returns_none(self):
        svc = GitService(self.tmp, proxy="", token="")
        self.assertIsNone(svc._env())

    def test_token_injects_extra_header_no_proxy(self):
        svc = GitService(self.tmp, proxy="", token=_FAKE_TOKEN)
        env = svc._env()
        self.assertIsNotNone(env)
        self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
        self.assertEqual(env["GIT_CONFIG_KEY_0"], "http.extraHeader")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], f"Authorization: Bearer {_FAKE_TOKEN}")
        self.assertNotIn("http_proxy", env)
        self.assertNotIn("https_proxy", env)

    def test_proxy_without_token_no_git_config(self):
        svc = GitService(self.tmp, proxy="http://127.0.0.1:9999", token="")
        env = svc._env()
        self.assertEqual(env.get("http_proxy"), "http://127.0.0.1:9999")
        self.assertEqual(env.get("https_proxy"), "http://127.0.0.1:9999")
        self.assertNotIn("GIT_CONFIG_COUNT", env)
        self.assertNotIn("GIT_CONFIG_KEY_0", env)
        self.assertNotIn("GIT_CONFIG_VALUE_0", env)

    def test_proxy_and_token_both_injected(self):
        svc = GitService(self.tmp, proxy="http://127.0.0.1:7890", token=_FAKE_TOKEN)
        env = svc._env()
        self.assertEqual(env["http_proxy"], "http://127.0.0.1:7890")
        self.assertEqual(env["GIT_CONFIG_VALUE_0"], f"Authorization: Bearer {_FAKE_TOKEN}")

    def test_token_carries_into_clone_env_not_existing_globals(self):
        # 进程级环境不受污染：token 只出现在 _env 返回的 dict，不写 os.environ
        svc = GitService(self.tmp, token=_FAKE_TOKEN)
        os.environ.pop("GIT_CONFIG_COUNT", None)
        env = svc._env()
        self.assertEqual(env["GIT_CONFIG_COUNT"], "1")
        self.assertNotIn("GIT_CONFIG_COUNT", os.environ)


class TestEngineToken(unittest.TestCase):
    """SyncEngine 透传 token → _service().token。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "t.db")

    def test_engine_forwards_token_to_service(self):
        engine = SyncEngine(self.tmp / "clones", db=self.db, token=_FAKE_TOKEN)
        self.assertEqual(engine.token, _FAKE_TOKEN)
        self.assertEqual(engine._service().token, _FAKE_TOKEN)

    def test_engine_empty_token_default(self):
        engine = SyncEngine(self.tmp / "clones", db=self.db)
        self.assertEqual(engine.token, "")
        self.assertEqual(engine._service().token, "")
        self.assertIsNone(engine._service()._env())


class TestMainWindowToken(unittest.TestCase):
    """MainWindow 设置页 token 输入框 + 保存槽。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "t.db")
        self.settings = SettingsStore(self.tmp / "settings.json")
        self.w = MainWindow(data_dir=self.tmp, db=self.db, settings=self.settings)
        self.w.show()

    def tearDown(self):
        try:
            if hasattr(self.w, "_log_batch_timer") and self.w._log_batch_timer.isActive():
                self.w._log_batch_timer.stop()
            if hasattr(self.w, "_log_batch"):
                self.w._log_batch.clear()
                self.w._flush_log_batch()
        except Exception:
            pass
        try:
            self.w.close()
        except Exception:
            pass

    def test_edit_token_initializes_from_settings(self):
        self.assertEqual(self.w.edit_token.text(), self.settings.load().token)

    def test_save_token_updates_settings(self):
        self.w.edit_token.setText(_FAKE_TOKEN)
        self.w._save_token()
        self.assertEqual(self.w.settings.token, _FAKE_TOKEN)
        self.assertEqual(self.settings.load().token, _FAKE_TOKEN, "保存后应持久化到 settings.json")

    def test_save_token_trims_and_clears(self):
        self.w.edit_token.setText(f"  {_FAKE_TOKEN}  ")
        self.w._save_token()
        self.assertEqual(self.w.settings.token, _FAKE_TOKEN)
        self.w.edit_token.setText("")
        self.w._save_token()
        self.assertEqual(self.w.settings.token, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)