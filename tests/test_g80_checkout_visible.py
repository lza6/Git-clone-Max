"""G80-B4 检出校验守卫：**"取回了但没检出" 不许伪装成成功**。

背景（差距 G-01）：原来的成功判定只看 `git rev-parse HEAD` 能否解析，于是只剩 `.git`
的半成品目录会被当成健康仓库 —— 参考库里 101 个这样的目录、合计 7.4GB，就是这类后果。
本地扫描随后还会把它当仓库导入，形成僵尸条目。

四条不变量：
1. 正常克隆/更新 → `checkout_state == "ok"` 且 SUCCESS；
2. 工作树空（只剩 .git）→ FAILED + 「已取回但未能检出」+ **目录保留**（可增量重试）；
3. 裸仓库 → `"skip"`，**不得**判失败；
4. 空仓库（无提交）→ 仍走既有 EMPTY，**不得**被判 missing。
外加：`SyncStatus` 取值集合不得变化（agent 契约）。
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from gcm.models import RepoSpec, SyncAction, SyncStatus

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def _init_remote(tmp: Path, name: str, commit: bool = True) -> Path:
    remote = tmp / f"{name}.git"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
                   check=True, capture_output=True)
    if commit:
        work = tmp / f"w_{name}"
        work.mkdir(parents=True, exist_ok=True)
        _git(work, "init", "-q")
        _git(work, "config", "user.email", "t@t.com")
        _git(work, "config", "user.name", "t")
        (work / "a.txt").write_text("v1", encoding="utf-8")
        _git(work, "add", ".")
        _git(work, "commit", "-m", "v1")
        _git(work, "remote", "add", "origin", str(remote))
        _git(work, "push", "-q", "origin", "HEAD:main")
    return remote


def _spec(owner: str, repo: str, remote: Path) -> RepoSpec:
    return RepoSpec(owner, repo, str(remote), folder_name=f"{owner}__{repo}")


class _Base(unittest.TestCase):
    def setUp(self):
        from gcm.git.service import GitService

        self._td = tempfile.TemporaryDirectory()
        self.tmp = Path(self._td.name)
        self.root = self.tmp / "clones"
        self.root.mkdir()
        self.svc = GitService(self.root)

    def tearDown(self):
        self._td.cleanup()


class TestCheckoutProbe(_Base):
    def test_healthy_clone_is_ok(self):
        remote = _init_remote(self.tmp, "ok")
        res = self.svc.sync(_spec("acme", "ok", remote))
        self.assertEqual(res.status, SyncStatus.SUCCESS, res.message)
        self.assertEqual(res.action, SyncAction.CLONED)
        self.assertEqual(res.checkout_state, "ok")
        self.assertTrue((Path(res.path) / "a.txt").exists())

    def test_worktree_empty_is_reported_missing(self):
        """删掉工作树里除 .git 外的一切，再同步一次 → 必须失败且保留目录。"""
        remote = _init_remote(self.tmp, "half")
        spec = _spec("acme", "half", remote)
        first = self.svc.sync(spec)
        self.assertEqual(first.status, SyncStatus.SUCCESS)
        repo_dir = Path(first.path)
        for child in repo_dir.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir():
                import shutil
                shutil.rmtree(child)
            else:
                child.unlink()

        res = self.svc.sync(spec)
        self.assertEqual(res.checkout_state, "missing")
        self.assertEqual(res.status, SyncStatus.FAILED, "工作树空不能再报成功")
        self.assertIn("未能检出", res.message)
        self.assertIn("怎么办", res.detail, "失败详情必须是三段式（发生了什么/为什么/怎么办）")
        self.assertTrue((repo_dir / ".git").exists(), "必须保留 .git 让下次能增量补齐")

    def test_bare_repo_is_skipped_not_failed(self):
        """裸仓库本就没有工作树 → skip，且不得判失败（避免误报）。"""
        remote = _init_remote(self.tmp, "src")
        bare = self.tmp / "mirror.git"
        subprocess.run(["git", "clone", "--bare", "-q", str(remote), str(bare)], check=True)

        self.assertEqual(self.svc._probe_checkout(bare), "skip")
        # 直接把裸仓库当作 repo_dir 走检出校验：不得被降级为 FAILED
        from gcm.models import SyncResult
        res = SyncResult(spec=_spec("acme", "mirror", remote), status=SyncStatus.SUCCESS,
                         action=SyncAction.FETCHED, path=str(bare))
        out = self.svc._finalize_checkout(res)
        self.assertEqual(out.status, SyncStatus.SUCCESS, "裸仓库不得被判失败")
        self.assertEqual(out.checkout_state, "skip")

    def test_empty_repo_stays_ok(self):
        """空仓库（无提交）走既有 EMPTY 分支，不得被检出校验误伤。"""
        remote = _init_remote(self.tmp, "empty", commit=False)
        res = self.svc.sync(_spec("acme", "empty", remote))
        self.assertEqual(res.status, SyncStatus.SUCCESS)
        self.assertEqual(res.action, SyncAction.EMPTY)
        self.assertEqual(res.checkout_state, "ok")

    def test_missing_dir_probe(self):
        """目录不存在 → missing（防御性：调用方不该传不存在的路径）。"""
        self.assertEqual(self.svc._probe_checkout(self.tmp / "nope"), "missing")


class TestContractUnchanged(unittest.TestCase):
    def test_sync_status_enum_is_still_seven_values(self):
        """检出校验不得改 status 取值集合（CLI/MCP/HTTP 契约都按这 7 个值协商）。"""
        self.assertEqual(
            sorted(s.value for s in SyncStatus),
            ["cancelled", "conflict", "failed", "pending", "running", "skipped", "success"],
        )


if __name__ == "__main__":
    unittest.main()
