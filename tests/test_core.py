# -*- coding: utf-8 -*-
"""核心逻辑单元测试（纯 Python，无需 Qt 显示）。"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.url_lib import parse_repo_url, is_github_url, host_of, normalize_url, sanitize_name
from gcm.db.repo_db import Database, load_progress, save_progress, mark_finished
from gcm.git.service import GitService, _detect_conflict, _is_dirty
from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr}")
    return r


def _init_remote(tmp: Path) -> Path:
    remote = tmp / "remote.git"
    work = tmp / "work"
    os.makedirs(tmp, exist_ok=True)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    os.makedirs(work)
    _git(work, "init")
    _git(work, "config", "user.email", "t@t.com")
    _git(work, "config", "user.name", "t")
    (work / "a.txt").write_text("v1", encoding="utf-8")
    _git(work, "add", ".")
    _git(work, "commit", "-m", "v1")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-u", "origin", "main")
    # 设置裸仓库 HEAD
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                   cwd=remote, check=True, capture_output=True)
    return remote


class TestUrlLib(unittest.TestCase):
    def test_https(self):
        s = parse_repo_url("https://github.com/vercel-labs/skills")
        self.assertEqual((s.owner, s.repo), ("vercel-labs", "skills"))
        self.assertEqual(s.folder_name, "vercel-labs__skills")

    def test_ssh(self):
        s = parse_repo_url("git@github.com:microsoft/azure-skills.git")
        self.assertEqual((s.owner, s.repo), ("microsoft", "azure-skills"))

    def test_short(self):
        s = parse_repo_url("openmeterio/openmeter")
        self.assertEqual((s.owner, s.repo), ("openmeterio", "openmeter"))

    def test_trailing_path(self):
        s = parse_repo_url("https://github.com/Arize-ai/phoenix/tree/main/docs")
        self.assertEqual((s.owner, s.repo), ("Arize-ai", "phoenix"))

    def test_invalid(self):
        self.assertIsNone(parse_repo_url("not a url"))
        self.assertIsNone(parse_repo_url(""))
        self.assertIsNone(parse_repo_url("https://gitlab.com/foo/bar") if not is_github_url("https://gitlab.com/foo/bar") else None)

    def test_host(self):
        self.assertEqual(host_of("https://gitlab.com/a/b"), "gitlab.com")
        self.assertEqual(host_of("git@github.com:x/y.git"), "github.com")

    def test_sanitize(self):
        self.assertEqual(sanitize_name("a/b:c*?"), "a_b_c__")


class TestDatabase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "t.db")

    def tearDown(self):
        self.db.close()

    def test_upsert_and_history(self):
        spec = RepoSpec("lza6", "Git-clone-Max", "https://github.com/lza6/Git-clone-Max.git",
                        folder_name="lza6__Git-clone-Max")
        rid = self.db.upsert_repo(spec, str(self.tmp / "lza6__Git-clone-Max"))
        self.assertEqual(self.db.count(), 1)
        self.db.add_sync_history(rid, SyncStatus.SUCCESS, SyncAction.CLONED, "ok")
        h = self.db.history(rid, 5)
        self.assertEqual(h[0]["status"], "success")
        # 重复 upsert 不新增行
        rid2 = self.db.upsert_repo(spec, str(self.tmp / "lza6__Git-clone-Max"),
                                   head_sha="abc")
        self.assertEqual(self.db.count(), 1)
        self.assertEqual(rid, rid2)
        self.assertEqual(self.db.get_repo("lza6", "Git-clone-Max")["head_sha"], "abc")

    def test_delete(self):
        spec = RepoSpec("a", "b", "https://github.com/a/b.git", folder_name="a__b")
        rid = self.db.upsert_repo(spec, "x")
        self.db.delete_repo(rid)
        self.assertEqual(self.db.count(), 0)


class TestProgress(unittest.TestCase):
    def test_mark_and_load(self):
        tmp = Path(tempfile.mkdtemp())
        pp = tmp / "p.json"
        save_progress(pp, {"finished": [], "in_progress": {}})
        mark_finished(pp, RepoSpec("o", "r", "u", folder_name="o__r"),
                      {"status": "success"})
        data = load_progress(pp)
        self.assertEqual(data["finished"][0]["key"], "o/r")


class TestGitService(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.remote = _init_remote(self.tmp)

    def _spec(self, owner="lza6", repo="Git-clone-Max", url=None, folder=None):
        return RepoSpec(owner, repo, url or str(self.remote),
                        folder_name=folder or f"{owner}__{repo}")

    def test_full_cycle(self):
        svc = GitService(self.tmp)
        spec = self._spec()
        r1 = svc.sync(spec)
        self.assertEqual(r1.action.value, "cloned")
        self.assertTrue((self.tmp / "lza6__Git-clone-Max" / "a.txt").exists())

        # 远端 v2
        work = self.tmp / "work"
        (work / "a.txt").write_text("v2", encoding="utf-8")
        _git(work, "commit", "-am", "v2")
        _git(work, "push")
        r2 = svc.sync(spec)
        self.assertEqual(r2.action.value, "updated")
        self.assertEqual(r2.commits, 1)
        self.assertEqual((self.tmp / spec.folder_name / "a.txt").read_text(encoding="utf-8"), "v2")

        r3 = svc.sync(spec)
        self.assertEqual(r3.action.value, "fetched")

    def test_conflict_preserved(self):
        svc = GitService(self.tmp)
        spec = self._spec()
        svc.sync(spec)
        clone = self.tmp / spec.folder_name
        (clone / "a.txt").write_text("local", encoding="utf-8")
        _git(clone, "add", "-A")
        _git(clone, "commit", "-m", "local change")
        work = self.tmp / "work"
        (work / "a.txt").write_text("remote-v3", encoding="utf-8")
        _git(work, "commit", "-am", "v3")
        _git(work, "push")
        r = svc.sync(spec)
        self.assertEqual(r.status.value, "conflict")
        self.assertEqual((clone / "a.txt").read_text(encoding="utf-8"), "local")

    def test_broken_dir_recloned(self):
        svc = GitService(self.tmp)
        d = self.tmp / "broken__repo"
        d.mkdir()
        (d / "x").write_text("x", encoding="utf-8")
        spec = RepoSpec("broken", "repo", str(self.remote), folder_name="broken__repo")
        r = svc.sync(spec)
        self.assertEqual(r.action.value, "cloned")


if __name__ == "__main__":
    unittest.main(verbosity=2)