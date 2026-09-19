"""P-perf 扫描重构测试：无子进程读取 / 裸仓 / worktree / packed-refs / 进度取消 / 批量写库。"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app import scanner  # noqa: E402
from gcm.db.repo_db import Database  # noqa: E402
from gcm.models import RepoSpec  # noqa: E402


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_repo(path: Path, with_origin: str = "") -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "t@t.com")
    _git(path, "config", "user.name", "t")
    if with_origin:
        _git(path, "remote", "add", "origin", with_origin)
    (path / "a.txt").write_text("v1", encoding="utf-8")
    _git(path, "add", "-A")
    _git(path, "commit", "-m", "v1")


class TestFileReadsNoSubprocess(unittest.TestCase):
    def test_remote_and_head_no_subprocess(self):
        d = Path(tempfile.mkdtemp())
        repo = d / "o__r"
        _init_repo(repo, with_origin="https://github.com/o/r.git")
        with mock.patch("gcm.app.scanner.subprocess.run") as mrun:
            url = scanner._remote_url(repo)
            sha = scanner._head_sha(repo)
        self.assertEqual(url, "https://github.com/o/r.git")
        self.assertTrue(sha)
        mrun.assert_not_called()

    def test_scan_no_subprocess_per_repo(self):
        d = Path(tempfile.mkdtemp())
        base = d / "clones"
        for name in ("a__1", "b__2", "c__3"):
            _init_repo(base / name, with_origin="https://github.com/x/r.git")
        with mock.patch("gcm.app.scanner.subprocess.run") as mrun:
            infos = scanner.scan_git_dirs(base, max_depth=2)
        self.assertEqual(len(infos), 3)
        mrun.assert_not_called()


class TestCompleteness(unittest.TestCase):
    def test_bare_repo_detected(self):
        d = Path(tempfile.mkdtemp())
        bare = d / "repo.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        subprocess.run(
            ["git", "--git-dir", str(bare), "symbolic-ref", "HEAD", "refs/heads/main"],
            check=True, capture_output=True)
        subprocess.run(
            ["git", "--git-dir", str(bare), "remote", "add", "origin",
             "https://github.com/o/r.git"], check=True, capture_output=True)
        infos = scanner.scan_git_dirs(d, max_depth=1)
        self.assertTrue(any(i.folder_name == "repo.git" for i in infos),
                        "裸仓应被发现")
        bi = next(i for i in infos if i.folder_name == "repo.git")
        self.assertEqual(bi.remote_url, "https://github.com/o/r.git")

    def test_worktree_detected(self):
        d = Path(tempfile.mkdtemp())
        main = d / "main"
        _init_repo(main, with_origin="https://github.com/o/r.git")
        wt = d / "wt"
        subprocess.run(
            ["git", "-C", str(main), "worktree", "add", "-q", "-b", "wt-branch", str(wt)],
            check=True, capture_output=True)
        infos = scanner.scan_git_dirs(d, max_depth=1)
        names = {i.folder_name for i in infos}
        self.assertIn("main", names)
        self.assertIn("wt", names, "worktree（.git 为文件）应被发现")
        wi = next(i for i in infos if i.folder_name == "wt")
        self.assertTrue(wi.head_sha, "worktree 应有 HEAD")

    def test_packed_refs_head(self):
        d = Path(tempfile.mkdtemp())
        repo = d / "packed"
        _init_repo(repo)
        subprocess.run(["git", "-C", str(repo), "pack-refs", "--all"], check=True)
        loose = repo / ".git" / "refs" / "heads"
        for f in loose.glob("*"):
            f.unlink()
        sha = scanner._head_sha_from_files(repo / ".git")
        self.assertTrue(sha, "packed-refs 应能读到 HEAD")

    def test_resolve_gitdir_file(self):
        d = Path(tempfile.mkdtemp())
        real = d / "real"
        _init_repo(real)
        linked = d / "linked"
        linked.mkdir()
        (linked / ".git").write_text("gitdir: " + str(real / ".git"), encoding="utf-8")
        self.assertTrue(scanner.is_git_repo_path(linked))
        self.assertFalse(scanner.is_git_repo_path(d / "not_a_repo"))


class TestProgressAndCancel(unittest.TestCase):
    def test_progress_callback_reports(self):
        d = Path(tempfile.mkdtemp())
        base = d / "clones"
        for i in range(5):
            _init_repo(base / f"repo{i}")
        seen = []

        def on_progress(done, total):
            seen.append((done, total))

        scanner.scan_git_dirs(base, max_depth=2, progress=on_progress)
        self.assertGreater(len(seen), 1)
        self.assertEqual(seen[0], (0, 5))
        self.assertEqual(seen[-1][0], 5)

    def test_cancelled_stops_early(self):
        d = Path(tempfile.mkdtemp())
        base = d / "clones"
        for i in range(8):
            _init_repo(base / f"repo{i}")
        t0 = time.time()
        infos = scanner.scan_git_dirs(base, max_depth=2, cancelled=lambda: True)
        self.assertEqual(infos, [])
        self.assertLess(time.time() - t0, 30)


class TestBulkUpsert(unittest.TestCase):
    def test_bulk_upsert_writes_all(self):
        d = Path(tempfile.mkdtemp())
        db = Database(d / "t.db")
        try:
            items = []
            for i in range(20):
                spec = RepoSpec(
                    owner=f"o{i}", repo=f"r{i}",
                    url_https=f"https://github.com/o{i}/r{i}.git",
                    folder_name=f"o{i}__r{i}")
                items.append((spec, str(d / f"o{i}__r{i}"), "github.com",
                              f"abc{i}" + "0" * 36))
            n = db.bulk_upsert_repos(items)
            self.assertEqual(n, 20)
            self.assertEqual(db.count(), 20)
            row = db.get_repo("o5", "r5", None)
            self.assertIsNotNone(row)
        finally:
            db.close()

    def test_bulk_upsert_empty_and_conflict_update(self):
        d = Path(tempfile.mkdtemp())
        db = Database(d / "t.db")
        try:
            self.assertEqual(db.bulk_upsert_repos([]), 0)
            spec = RepoSpec("o", "r", "https://github.com/o/r.git", folder_name="o__r")
            db.bulk_upsert_repos([(spec, "/a", "github.com", "sha1" + "0" * 36)])
            db.bulk_upsert_repos([(spec, "/b", "github.com", "sha2" + "0" * 36)])
            row = db.get_repo("o", "r", None)
            self.assertEqual(row["local_path"], "/b")
            self.assertEqual(row["head_sha"], "sha2" + "0" * 36)
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
