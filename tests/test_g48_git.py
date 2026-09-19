# -*- coding: utf-8 -*-
"""G48 Git/网络深化测试：并发自适应/LFS/mirror/SSH env/多账号/钩子/归档/诊断/慢速提示。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gcm.app.engine import SyncEngine
from gcm.models import RepoSpec, SyncStatus


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def _init_remote(tmp: Path, name: str) -> Path:
    remote = tmp / f"{name}.git"
    work = tmp / f"w_{name}"
    _git(tmp, "init", "--bare", "-q", str(remote))
    _git(tmp, "--git-dir", str(remote), "symbolic-ref", "HEAD", "refs/heads/main")
    work.mkdir(parents=True, exist_ok=True)
    _git(work, "init", "-q")
    _git(work, "config", "user.email", "t@t.com")
    _git(work, "config", "user.name", "t")
    (work / "a.txt").write_text("v1", encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "-q", "-m", "c1")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-q", "origin", "main")
    return remote


class TestConcurrencyAdapt(unittest.TestCase):
    """G48-1：连续网络失败降并发，连续成功回升。"""

    def _engine(self):
        tmp = Path(tempfile.mkdtemp())
        return SyncEngine(tmp / "clones", concurrency=8), tmp

    def test_failures_halve_concurrency(self):
        e, tmp = self._engine()
        try:
            e._adapt_max = 8
            e._adapt_current = 8
            msgs = []
            e.adapt_changed.connect(lambda n, t: msgs.append((n, t)))
            for i in range(3):
                e._adapt_result(SyncStatus.FAILED, "Connection timed out")
            self.assertEqual(e._adapt_current, 4)
            self.assertTrue(msgs)
        finally:
            e.shutdown()

    def test_recovery_restores(self):
        e, tmp = self._engine()
        try:
            e._adapt_max = 8
            e._adapt_current = 8
            e._adapt_result(SyncStatus.FAILED, "timed out")  # 1
            e._adapt_result(SyncStatus.FAILED, "timed out")
            e._adapt_result(SyncStatus.FAILED, "timed out")  # 3 → 4
            self.assertEqual(e._adapt_current, 4)
            for i in range(3):
                e._adapt_result(SyncStatus.SUCCESS, "ok")
            self.assertEqual(e._adapt_current, 8)
        finally:
            e.shutdown()


class TestCloneCmd(unittest.TestCase):
    """G48-2/6：LFS 与 --mirror 出现在 clone 命令。"""

    def _svc(self, **kw):
        from gcm.git.service import GitService
        return GitService(Path(tempfile.mkdtemp()), lfs_enabled=kw.get("lfs", False),
                          mirror=kw.get("mirror", False))

    def test_lfs_flag(self):
        svc = self._svc(lfs=True)
        cmd = svc.build_clone_cmd(RepoSpec("o", "r", url_https="https://github.com/o/r.git"))
        self.assertIn("-c", cmd)
        self.assertIn("filter.lfs.required=false", cmd)

    def test_mirror_flag(self):
        svc = self._svc(mirror=True)
        cmd = svc.build_clone_cmd(RepoSpec("o", "r", url_https="https://github.com/o/r.git"))
        self.assertIn("--mirror", cmd)


class TestSshKeyEnv(unittest.TestCase):
    """G48-3：指定 SSH key 时注入 GIT_SSH_COMMAND。"""

    def test_env_contains_ssh_command(self):
        from gcm.git.service import GitService
        svc = GitService(Path(tempfile.mkdtemp()), ssh_key="C:/keys/id_rsa")
        env = svc._env()
        self.assertIsNotNone(env)
        self.assertIn("GIT_SSH_COMMAND", env)
        self.assertIn("id_rsa", env["GIT_SSH_COMMAND"])


class TestMultiAccount(unittest.TestCase):
    """G48-4：host_tokens 多账号解析。"""

    def test_resolve_account(self):
        from gcm.git.service import GitService
        ht = {"gitlab.com": "user1:tok1\nuser2:tok2"}
        self.assertEqual(GitService.resolve_host_token(ht, "gitlab.com", "user2"), "tok2")
        self.assertEqual(GitService.resolve_host_token(ht, "gitlab.com"), "user1:tok1\nuser2:tok2")
        self.assertEqual(GitService.resolve_host_token({}, "github.com"), "")


class TestArchiveE2E(unittest.TestCase):
    """G48-7：真实本地裸仓归档下载 zip。"""

    def test_archive_zip_contains_file(self):
        from gcm.git.archive import download_archive
        import zipfile
        tmp = Path(tempfile.mkdtemp())
        remote = _init_remote(tmp, "r")
        dest = tmp / "out" / "r.zip"
        download_archive(str(remote), dest)
        self.assertTrue(dest.exists())
        with zipfile.ZipFile(dest) as z:
            names = z.namelist()
        self.assertTrue(any("a.txt" in n for n in names), names)


class TestPostCloneHookE2E(unittest.TestCase):
    """G48-5：克隆后钩子真实执行（%PATH% 替换）。"""

    def test_hook_runs_after_clone(self):
        from gcm.git.service import GitService
        tmp = Path(tempfile.mkdtemp())
        remote = _init_remote(tmp, "r")
        marker = tmp / "marker.txt"
        hook = f"echo done > {marker}"
        svc = GitService(tmp / "clones", post_clone_hook=hook, ssh_key="")
        spec = RepoSpec("o", "r", url_https=str(remote))
        from gcm.models import SyncResult, SyncStatus, SyncAction
        res = SyncResult(spec=spec, status=SyncStatus.PENDING, action=SyncAction.CLONED)
        svc._clone(spec, res)
        self.assertEqual(res.status, SyncStatus.SUCCESS)
        self.assertTrue(marker.exists(), "钩子应已执行")


class TestDiag(unittest.TestCase):
    """G48-8：诊断报告分级。"""

    def test_grade(self):
        from gcm.app.diag import grade
        self.assertEqual(grade([{"status": "ok"}, {"status": "ok"}]), "绿（全部正常）")
        self.assertIn("黄", grade([{"status": "ok"}, {"status": "warn"}]))
        self.assertIn("红", grade([{"status": "ok"}, {"status": "err"}]))


class TestSlowHint(unittest.TestCase):
    """G48-9：慢速预提示。"""

    def test_hint(self):
        from gcm.git.service import GitService
        self.assertIn("浅克隆", GitService.slow_remote_hint(5.0))
        self.assertEqual(GitService.slow_remote_hint(0.5), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)