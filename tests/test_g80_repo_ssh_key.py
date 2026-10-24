"""G80-B8 仓库级 SSH 私钥守卫（用户决策 U-2：把死列接线成功能）。

边界（必须一直守住）：
1. **只存路径，不存内容** —— 粘私钥内容进来必须被拒；
2. 优先级 per-repo > 全局 `settings.ssh_key_path`；清除后回落全局；
3. 路径不存在/是目录/不是私钥 → 明确报错，不静默生效；
4. 该列在 HTTP/MCP 出站时仍被剔除（沿用 `_SENSITIVE_COLS`）。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _write_key(path: Path, header: bytes = b"-----BEGIN OPENSSH PRIVATE KEY-----") -> Path:
    path.write_bytes(header + b"\nAAAAfakekeybody\n-----END OPENSSH PRIVATE KEY-----\n")
    return path


class TestValidateSshKeyPath(unittest.TestCase):
    """校验器：能挡的必须挡住，能放的必须放过。"""

    def test_accepts_real_looking_key_file(self):
        from gcm.util.sshkey import validate_ssh_key_path

        with tempfile.TemporaryDirectory() as td:
            key = _write_key(Path(td) / "id_ed25519")
            ok, err = validate_ssh_key_path(str(key))
        self.assertTrue(ok, err)

    def test_accepts_putty_key(self):
        from gcm.util.sshkey import validate_ssh_key_path

        with tempfile.TemporaryDirectory() as td:
            key = _write_key(Path(td) / "id.ppk", b"PuTTY-User-Key-File-3: ssh-ed25519")
            ok, err = validate_ssh_key_path(str(key))
        self.assertTrue(ok, err)

    def test_rejects_pasted_key_content(self):
        """把私钥**内容**粘进来必须拒绝（这是本功能最危险的误用）。"""
        from gcm.util.sshkey import validate_ssh_key_path

        ok, err = validate_ssh_key_path("-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
        self.assertFalse(ok)
        self.assertIn("路径", err)

    def test_rejects_missing_path(self):
        from gcm.util.sshkey import validate_ssh_key_path

        ok, err = validate_ssh_key_path(str(Path(tempfile.gettempdir()) / "no_such_key_xyz"))
        self.assertFalse(ok)
        self.assertIn("不存在", err)

    def test_rejects_directory(self):
        from gcm.util.sshkey import validate_ssh_key_path

        with tempfile.TemporaryDirectory() as td:
            ok, err = validate_ssh_key_path(td)
        self.assertFalse(ok)
        self.assertIn("目录", err)

    def test_rejects_non_key_file(self):
        from gcm.util.sshkey import validate_ssh_key_path

        with tempfile.TemporaryDirectory() as td:
            plain = Path(td) / "notes.txt"
            plain.write_text("hello\n", encoding="utf-8")
            ok, err = validate_ssh_key_path(str(plain))
        self.assertFalse(ok)
        self.assertIn("私钥", err)

    def test_empty_is_rejected_by_validator(self):
        from gcm.util.sshkey import validate_ssh_key_path

        ok, _err = validate_ssh_key_path("")
        self.assertFalse(ok)


class TestDbRoundTrip(unittest.TestCase):
    """repos.ssh_key 的读-写-清链路（此前只有写入方法，没有读取点）。"""

    def setUp(self):
        from gcm.db.repo_db import Database
        from gcm.models import RepoSpec

        self._td = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._td.name) / "repos.db")
        self.spec = RepoSpec(owner="acme", repo="rocket",
                             url_https="https://github.com/acme/rocket")
        self.rid = self.db.upsert_repo(self.spec, str(Path(self._td.name) / "acme__rocket"))

    def tearDown(self):
        self.db.close()
        self._td.cleanup()

    def test_default_is_empty(self):
        self.assertEqual(self.db.get_repo_ssh_key("acme", "rocket"), "")

    def test_set_then_get_then_clear(self):
        self.db.set_ssh_key(self.rid, "/keys/id_ed25519_acme")
        self.assertEqual(self.db.get_repo_ssh_key("acme", "rocket"), "/keys/id_ed25519_acme")
        self.db.set_ssh_key(self.rid, "")
        self.assertEqual(self.db.get_repo_ssh_key("acme", "rocket"), "",
                         "清除后必须回落空串（= 用全局 key）")

    def test_unknown_repo_returns_empty(self):
        self.assertEqual(self.db.get_repo_ssh_key("nobody", "nothing"), "")

    def test_sync_upsert_does_not_wipe_ssh_key(self):
        """同步时 upsert_repo 不得把用户设的 key 冲掉（它不写该列）。"""
        self.db.set_ssh_key(self.rid, "/keys/k1")
        self.db.upsert_repo(self.spec, str(Path(self._td.name) / "acme__rocket"))
        self.assertEqual(self.db.get_repo_ssh_key("acme", "rocket"), "/keys/k1")

    def test_ssh_key_column_is_stripped_from_agent_output(self):
        """HTTP/MCP 出站仍剔除该列（契约要求，别因为"接线了"就放出去）。"""
        from gcm.http_api import _sanitize_row
        from gcm.mcp_server import _sanitize_row as mcp_sanitize

        self.db.set_ssh_key(self.rid, "/keys/secret-path")
        row = self.db.list_repos()[0]
        self.assertIn("ssh_key", row, "DB 行里应该有该列（UI 要用）")
        self.assertNotIn("ssh_key", _sanitize_row(row), "HTTP 出站必须剔除")
        self.assertNotIn("ssh_key", mcp_sanitize(row), "MCP 出站必须剔除")


class TestEngineResolution(unittest.TestCase):
    """引擎侧：per-repo 覆盖全局；无 db / 查不到 / 读失败 → 回落全局（不炸整批）。"""

    def setUp(self):
        from gcm.app.engine import SyncEngine
        from gcm.db.repo_db import Database
        from gcm.models import RepoSpec

        self._td = tempfile.TemporaryDirectory()
        root = Path(self._td.name)
        self.db = Database(root / "repos.db")
        self.spec = RepoSpec(owner="acme", repo="rocket",
                             url_https="https://github.com/acme/rocket")
        self.rid = self.db.upsert_repo(self.spec, str(root / "acme__rocket"))
        self.engine = SyncEngine(root=root, db=self.db, progress_path=None)

    def tearDown(self):
        self.engine.shutdown()
        self.db.close()
        self._td.cleanup()

    def test_returns_per_repo_key(self):
        self.db.set_ssh_key(self.rid, "/keys/per-repo")
        self.assertEqual(self.engine._repo_ssh_key(self.spec, "github.com"), "/keys/per-repo")

    def test_empty_when_not_set(self):
        self.assertEqual(self.engine._repo_ssh_key(self.spec, "github.com"), "")

    def test_worker_service_applies_override_and_falls_back(self):
        """有 per-repo key → service 用它；无 → 保持全局（默认空）。"""
        from gcm.app.worker import CancelFlag

        flag = CancelFlag()
        svc_override = self.engine._worker_service(flag, ssh_key="/keys/per-repo")
        self.assertEqual(svc_override._ssh_key, "/keys/per-repo")
        svc_global = self.engine._worker_service(flag, ssh_key="")
        self.assertNotEqual(svc_global._ssh_key, "/keys/per-repo",
                            "未指定仓库级 key 时不得串用别的仓库的 key")

    def test_db_error_degrades_to_global(self):
        """读库抛错时必须降级全局，而不是把整批带崩。"""
        class _Boom:
            def get_repo_ssh_key(self, *a, **kw):
                raise RuntimeError("db gone")

        self.engine.db = _Boom()
        self.assertEqual(self.engine._repo_ssh_key(self.spec, "github.com"), "")


if __name__ == "__main__":
    unittest.main()
