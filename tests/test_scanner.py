# -*- coding: utf-8 -*-
"""scanner / manage_model / local_repos_dialog 单元测试。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.scanner import (RepoInfo, _name_to_owner_repo, filter_new,
                             known_keys_from_db, parse_remote_url,
                             remote_is_local, scan_git_dirs, to_spec)
from gcm.models import RepoSpec


def _init_git(path: Path, commit=True):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    if commit:
        (path / "a.txt").write_text("v1", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        subprocess.run(["git", "commit", "-m", "v1"], cwd=path,
                       check=True, capture_output=True)


class TestScanner(unittest.TestCase):
    def test_scan_finds_real_and_skips_fake(self):
        d = Path(tempfile.mkdtemp())
        base = d / "clones"
        real = base / "owner__repo"
        _init_git(real)
        # 伪造（仅 .git 目录，无 git 结构）
        fake = base / "fake" / ".git"
        fake.mkdir(parents=True)
        infos = scan_git_dirs(base)
        folders = {i.folder_name for i in infos}
        self.assertIn("owner__repo", folders)
        self.assertIn("fake", folders)   # 非真实 git 仓库也发现（用户自己造的目录也纳入），
        # 但 head_sha 为空
        by = {i.folder_name: i for i in infos}
        # 真实 git 仓库有 HEAD；伪造的（仅 .git 空目录）head_sha 为空
        self.assertTrue(by["owner__repo"].head_sha)
        self.assertEqual(by["fake"].head_sha, "")

    def test_max_depth(self):
        d = Path(tempfile.mkdtemp())
        base = d / "clones"
        deep = base / "a" / "b" / "c"
        (deep / ".git").mkdir(parents=True)
        infos = scan_git_dirs(base, max_depth=2)
        # a/b/c 深度 3 → 不发现；a/b 深度 2 → 不发现（需含 .git）
        self.assertNotIn("c", [i.folder_name for i in infos])
        self.assertNotIn("b", [i.folder_name for i in infos])

    def test_parse_remote_url(self):
        self.assertEqual(parse_remote_url("https://github.com/a/b.git"),
                         ("a", "b", "https://github.com/a/b.git", "github.com"))
        self.assertEqual(parse_remote_url("git@github.com:a/b.git"),
                         ("a", "b", "https://github.com/a/b.git", "github.com"))
        self.assertEqual(parse_remote_url("ssh://git@github.com/a/b.git"),
                         ("a", "b", "https://github.com/a/b.git", "github.com"))
        self.assertEqual(parse_remote_url("https://gitlab.com/x/y"),
                         ("x", "y", "https://gitlab.com/x/y.git", "gitlab.com"))
        self.assertEqual(parse_remote_url("file:///C:/x/y.git"),
                         ("", "", "", ""))
        self.assertEqual(parse_remote_url("C:/x/y"), ("", "", "", ""))
        self.assertEqual(parse_remote_url(r"C:\x\y"), ("", "", "", ""))
        self.assertEqual(parse_remote_url("/x/y"), ("", "", "", ""))
        self.assertEqual(parse_remote_url("github.com/a/b"),
                         ("a", "b", "https://github.com/a/b.git", "github.com"))

    def test_remote_is_local(self):
        self.assertTrue(remote_is_local(""))
        self.assertTrue(remote_is_local("file:///C:/x/y"))
        self.assertTrue(remote_is_local(r"C:\x\y"))
        self.assertTrue(remote_is_local("/x/y"))
        self.assertFalse(remote_is_local("https://github.com/a/b.git"))
        self.assertFalse(remote_is_local("git@github.com:a/b.git"))
        self.assertFalse(remote_is_local("github.com/a/b"))

    def test_name_to_owner_repo(self):
        self.assertEqual(_name_to_owner_repo("a__b"), ("a", "b"))
        self.assertEqual(_name_to_owner_repo("plain"), ("", "plain"))

    def test_filter_new_and_keys(self):
        d = Path(tempfile.mkdtemp())
        _init_git(d / "o__r")
        infos = scan_git_dirs(d)
        self.assertEqual(len(infos), 1)
        known = known_keys_from_db(
            [{"owner": "o", "repo": "r", "local_path": str(d / "o__r"),
              "host": "github.com"}])
        self.assertEqual(len(filter_new(infos, known)), 0)
        keys = {i.key for i in infos}
        self.assertIn("o/r", keys)      # 目录名推测 owner/repo

    def test_to_spec_local(self):
        d = Path(tempfile.mkdtemp())
        _init_git(d / "plain")
        info = scan_git_dirs(d)[0]
        spec = to_spec(info)
        self.assertTrue(spec.is_local)
        self.assertEqual(spec.folder_name, "plain")
        # 纯本地无远端：url_https 必须为空（审计 H1：回填路径会让 DB roundtrip 误判 is_local）
        self.assertEqual(spec.url_https, "")
        self.assertEqual(spec.local_path, str(d / "plain"))

    def test_to_spec_remote(self):
        info = RepoInfo(folder_name="o__r", display="o/r", path="/x/o__r",
                        remote_url="https://github.com/o/r.git",
                        owner="o", repo="r",
                        url_https="https://github.com/o/r.git", host="github.com")
        spec = to_spec(info)
        self.assertFalse(spec.is_local)
        self.assertEqual(spec.url_https, "https://github.com/o/r.git")


class TestManageModel(unittest.TestCase):
    def test_rows_and_data(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from gcm.ui.manage_model import ManageModel
        model = ManageModel([
            {"folder_name": "o__r", "owner": "o", "repo": "r",
             "local_path": "/x/o__r", "last_sync_at": "2026-01-01 12:00:00",
             "head_sha": "abcdef012345", "host": "github.com",
             "url": "https://github.com/o/r.git", "id": 7, "default_branch": "main"},
        ])
        self.assertEqual(model.rowCount(), 1)
        self.assertEqual(model.columnCount(), 6)
        from PyQt6.QtCore import Qt
        idx = model.index(0, 0)
        self.assertEqual(model.data(idx, Qt.ItemDataRole.DisplayRole), "o__r")
        idx1 = model.index(0, 1)
        self.assertEqual(model.data(idx1, Qt.ItemDataRole.DisplayRole), "o/r")
        idx2 = model.index(0, 3)
        self.assertEqual(model.data(idx2, Qt.ItemDataRole.DisplayRole), "2026-01-01 12:00:00")
        idx4 = model.index(0, 4)
        self.assertEqual(model.data(idx4, Qt.ItemDataRole.DisplayRole), "abcdef01")
        self.assertEqual(model.row_at(0).repo_id, 7)
        self.assertIsNone(model.row_at(99))

    def test_remove_rows(self):
        from gcm.ui.manage_model import ManageModel
        model = ManageModel(
            [{"folder_name": f"o__r{i}", "owner": "o", "repo": f"r{i}",
              "local_path": f"/x/{i}", "last_sync_at": "", "head_sha": "",
              "host": "github.com", "url": "", "id": i} for i in range(5)])
        self.assertEqual(model.remove_rows_at([1, 3]), 2)
        self.assertEqual(model.rowCount(), 3)
        names = [model.row_at(i).folder_name for i in range(3)]
        self.assertEqual(names, ["o__r0", "o__r2", "o__r4"])


if __name__ == "__main__":
    unittest.main(verbosity=2)