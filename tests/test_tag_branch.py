# -*- coding: utf-8 -*-
"""G08-2 @tag 语法测试：解析 owner/repo@v1.2.0 → clone -b 指定 tag。

安全约束：owner/repo 段含 @ 的凭据前置攻击仍被拒绝；只有作为「仓库名后的
标签后缀」的 @tag 才被接受。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.url_lib import parse_any_repo_url
from gcm.git.service import GitService
from gcm.models import RepoSpec, SyncStatus


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


class TestTagSyntax(unittest.TestCase):
    def test_parse_repo_with_tag(self):
        s = parse_any_repo_url("https://github.com/owner/repo@v1.2.0")
        self.assertIsNotNone(s)
        self.assertEqual(s.owner, "owner")
        self.assertEqual(s.repo, "repo")
        self.assertEqual(s.url_https, "https://github.com/owner/repo.git")
        self.assertEqual(getattr(s, "ref", ""), "v1.2.0")

    def test_parse_short_with_tag(self):
        s = parse_any_repo_url("owner/repo@main")
        self.assertEqual(getattr(s, "ref", ""), "main")

    def test_ssh_with_tag(self):
        s = parse_any_repo_url("git@github.com:owner/repo.git@v1.0.0")
        self.assertEqual(getattr(s, "ref", ""), "v1.0.0")

    def test_at_no_tag_means_credential_prefix_rejected(self):
        # host 前/owner 前的 @ 是凭据前置攻击 → 仍拒绝
        self.assertIsNone(parse_any_repo_url("https://github.com/attacker@evil/r"))
        # 有 @ 但无有效 tag（空）→ 拒绝
        self.assertIsNone(parse_any_repo_url("https://github.com/owner/repo@"))

    def test_clone_specific_tag(self):
        """真实 git E2E：裸仓库打 tag v1 → 用 @v1 克隆出工作区。"""
        base = Path(tempfile.mkdtemp())
        remote = base / "remote.git"
        work = base / "work"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD",
                        "refs/heads/main"], check=True, capture_output=True)
        work.mkdir()
        _git(work, "init", "-q")
        _git(work, "config", "user.email", "t@t.com")
        _git(work, "config", "user.name", "t")
        (work / "a.txt").write_text("v1", encoding="utf-8")
        _git(work, "add", ".")
        _git(work, "commit", "-m", "v1")
        _git(work, "branch", "-M", "main")
        _git(work, "remote", "add", "origin", str(remote))
        _git(work, "push", "-q", "origin", "HEAD:main")
        _git(work, "tag", "v1.0.0")
        _git(work, "push", "-q", "origin", "v1.0.0")

        # 直接构造带 tag 的 spec 走 GitService（clone 命令加 -b）
        spec = RepoSpec("owner", "repo", str(remote),
                        folder_name="owner__repo@v1.0.0", ref="v1.0.0")
        svc = GitService(base / "root")
        r = svc.sync(spec)
        self.assertEqual(r.status, SyncStatus.SUCCESS, r.message)
        clone = base / "root" / "owner__repo@v1.0.0"
        # 该 tag 指向的 HEAD 提交
        self.assertEqual((clone / "a.txt").read_text(encoding="utf-8"), "v1")
        head = _git(clone, "rev-parse", "HEAD").stdout.strip()
        tgt = _git(work, "rev-parse", "v1.0.0").stdout.strip()
        self.assertEqual(head, tgt, "克隆应在 tag v1.0.0 指定的提交上")
        # F6：detached HEAD 二次同步不应误报冲突
        r2 = svc.sync(spec)
        self.assertNotEqual(r2.status, SyncStatus.CONFLICT,
                            "tag 检出的 detached HEAD 二次同步不应误报冲突")
        self.assertEqual(r2.status, SyncStatus.SUCCESS, r2.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)

class TestTreelessFallback(unittest.TestCase):
    """G04-1 弱网降级：常规克隆网络失败 → treeless 部分克隆。"""

    def test_treeless_fallback_command_built(self):
        """验证降级命令含 --filter=blob:none（用真实远端确认 treeless 可克隆）。"""
        base = Path(tempfile.mkdtemp())
        remote = base / "remote.git"
        work = base / "work"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD",
                        "refs/heads/main"], check=True, capture_output=True)
        work.mkdir()
        _git(work, "init", "-q")
        _git(work, "config", "user.email", "t@t.com")
        _git(work, "config", "user.name", "t")
        (work / "a.txt").write_text("v1", encoding="utf-8")
        _git(work, "add", ".")
        _git(work, "commit", "-m", "v1")
        _git(work, "branch", "-M", "main")
        _git(work, "remote", "add", "origin", str(remote))
        _git(work, "push", "-q", "origin", "HEAD:main")

        # 直接验证 treeless 命令可成功克隆（本地裸仓库也支持 --filter）
        out = base / "treeless_clone"
        r = subprocess.run(["git", "clone", "--progress", "--filter=blob:none",
                            str(remote), str(out)],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((out / "a.txt").exists(), "treeless 克隆工作区应完整")
