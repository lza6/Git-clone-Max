# -*- coding: utf-8 -*-
"""V3-P0 正确性修复测试：一键更新携带 local_path（B1）/ host 精确回传（B2）/ 浅克隆增量（B3）/ 本地路径远端（B4）。"""
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

from gcm.app.worker import TaskPayload
from gcm.db.repo_db import Database
from gcm.models import RepoSpec


def _init_git(path: Path, commit=True, file="a.txt", content="v1"):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    if commit:
        (path / file).write_text(content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=path, check=True)
        subprocess.run(["git", "commit", "-m", "v1"], cwd=path, check=True,
                       capture_output=True)


def _init_bare(path: Path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "--bare", "-q", str(path)], check=True)
    # 指定默认分支 main，保证 clone 检出 main（否则默认 master + HEAD 指向不存在 ref）
    subprocess.run(["git", "-C", str(path), "symbolic-ref", "HEAD", "refs/heads/main"],
                   check=True, capture_output=True)


class TestRowsToSpecs(unittest.TestCase):
    """B1：一键更新链路必须携带 local_path / is_local。"""

    def _make_rows(self):
        return [
            # 导入仓库：非规范命名目录 + 真实 local_path + 无远端 url
            {"owner": "myproj", "repo": "myproj", "url": "",
             "folder_name": "myproj", "local_path": r"D:\repos\my-proj"},
            # 远端仓库：有 url，is_local 应为 False
            {"owner": "lza6", "repo": "Git-clone-Max", "url": "https://github.com/lza6/Git-clone-Max.git",
             "folder_name": "lza6__Git-clone-Max", "local_path": r"D:\clones\lza6__Git-clone-Max"},
        ]

    def test_local_path_carried(self):
        from gcm.ui.main_window import MainWindow
        specs = MainWindow._rows_to_specs(self._make_rows())
        self.assertEqual(specs[0].local_path, r"D:\repos\my-proj")
        self.assertEqual(specs[1].local_path, r"D:\clones\lza6__Git-clone-Max")

    def test_is_local_flags(self):
        from gcm.ui.main_window import MainWindow
        specs = MainWindow._rows_to_specs(self._make_rows())
        self.assertTrue(specs[0].is_local)      # 无远端 → 纯本地
        self.assertFalse(specs[1].is_local)     # 有远端 → 可 fetch 增量

    def test_empty_local_path_no_crash(self):
        from gcm.ui.main_window import MainWindow
        rows = [{"owner": "a", "repo": "b", "url": "https://github.com/a/b.git",
                 "folder_name": "a__b", "local_path": ""}]
        specs = MainWindow._rows_to_specs(rows)
        self.assertEqual(specs[0].folder_name, "a__b")
        self.assertFalse(specs[0].is_local)

    def test_folder_name_passthrough(self):
        from gcm.ui.main_window import MainWindow
        specs = MainWindow._rows_to_specs(self._make_rows())
        self.assertEqual(specs[0].folder_name, "myproj")


class TestDBRoundtrip(unittest.TestCase):
    """审计 H1：scanner.to_spec → 入库 → _rows_to_specs 全链路语义一致性。"""

    def test_db_roundtrip_preserves_is_local_semantics(self):
        """纯本地无远端仓库：url 入库为空 → DB 行 url='' → _rows_to_specs 判 is_local=True。"""
        from gcm.app.scanner import RepoInfo, to_spec
        from gcm.db import repo_db
        # 1) scanner 判定
        info = RepoInfo(folder_name="my-proj", display="my-proj",
                        path=r"D:\repos\my-proj", remote_url="")
        spec = to_spec(info)
        self.assertTrue(spec.is_local)
        # 2) 即使只有 RepoSpec 默认字段（url_https="", local_path 特殊），入库 url 应为空
        self.assertEqual(spec.url_https, "")
        # 3) DB roundtrip：用与 local_repos_dialog 相同的写入方式
        d = Path(tempfile.mkdtemp()) / "t.db"
        db = repo_db.Database(d)
        spec2 = RepoSpec(owner=info.folder_name, repo=info.folder_name,
                         url_https=spec.url_https, folder_name=info.folder_name,
                         local_path=info.path, is_local=spec.is_local)
        db.upsert_repo(spec2, info.path, host="local")
        rows = db.list_repos()
        self.assertEqual(rows[0]["url"], "", "纯本地仓库 url 入库必须为空")
        # 4) _rows_to_specs 从 DB 行重建 → is_local 仍为 True
        from gcm.ui.main_window import MainWindow
        built = MainWindow._rows_to_specs(rows)
        self.assertTrue(built[0].is_local, "DB roundtrip 后纯本地仓库 is_local 必须保持 True")
        self.assertEqual(built[0].local_path, r"D:\repos\my-proj")
        db.close()

    def test_remote_roundtrip_is_local_false(self):
        """远端仓库（有 url）roundtrip 后 is_local 保持 False。"""
        from gcm.db import repo_db
        from gcm.models import RepoSpec
        d = Path(tempfile.mkdtemp()) / "t.db"
        db = repo_db.Database(d)
        spec = RepoSpec(owner="o", repo="r", url_https="https://g.com/o/r.git",
                        folder_name="o__r")
        db.upsert_repo(spec, r"D:\x\o__r", host="github.com")
        rows = db.list_repos()
        from gcm.ui.main_window import MainWindow
        built = MainWindow._rows_to_specs(rows)
        self.assertFalse(built[0].is_local)
        self.assertEqual(built[0].url_https, "https://g.com/o/r.git")
        db.close()


class TestPayloadHost(unittest.TestCase):
    """B2：worker 落库必须保留来源 host，不得硬编码 github.com。"""

    def test_task_payload_default_host(self):
        p = TaskPayload(spec=RepoSpec(owner="o", repo="r", url_https="u"))
        self.assertEqual(p.host, "github.com")

    def test_task_payload_custom_host(self):
        p = TaskPayload(spec=RepoSpec(owner="o", repo="r", url_https="u"), host="local")
        self.assertEqual(p.host, "local")

    def test_db_get_repo_any_host(self):
        d = Path(tempfile.mkdtemp()) / "t.db"
        db = Database(d)
        spec = RepoSpec(owner="o", repo="r", url_https="https://g.com/o/r.git",
                        folder_name="o__r")
        db.upsert_repo(spec, r"D:\x\o__r", host="local")
        rec = db.get_repo("o", "r", None)
        self.assertIsNotNone(rec)
        self.assertEqual(rec["host"], "local")
        db.close()


class TestLocalPathRemoteIncremental(unittest.TestCase):
    """B4：本地路径远端（file:// / C:/x / /x/y）必须可 fetch 增量更新，不得判为纯本地。"""

    def test_to_spec_file_url_not_local(self):
        from gcm.app.scanner import RepoInfo, to_spec
        info = RepoInfo(folder_name="w", display="w", path=r"D:\repos\w",
                        remote_url="file:///d:/remote.git")
        spec = to_spec(info)
        self.assertFalse(spec.is_local)
        self.assertTrue(spec.url_https)

    def test_to_spec_local_path_remote_not_local(self):
        from gcm.app.scanner import RepoInfo, to_spec
        info = RepoInfo(folder_name="w", display="w", path=r"D:\repos\w",
                        remote_url=r"C:\remote.git")
        spec = to_spec(info)
        self.assertFalse(spec.is_local)

    def test_to_spec_truly_local(self):
        from gcm.app.scanner import RepoInfo, to_spec
        info = RepoInfo(folder_name="w", display="w", path=r"D:\repos\w",
                        remote_url="")
        spec = to_spec(info)
        self.assertTrue(spec.is_local)
        # 无远端时 url_https 必须为空（否则入 DB 后 url 非空会让 _rows_to_specs 误判）
        self.assertEqual(spec.url_https, "")
        self.assertEqual(spec.local_path, r"D:\repos\w")

    def test_real_incremental_update_via_local_path_remote(self):
        """真实 E2E：本地路径远端 → clone → 远端推新 → sync 增量 +1 提交 → HEAD 一致。"""
        from gcm.git.service import GitService
        from gcm.models import SyncStatus
        base = Path(tempfile.mkdtemp())
        remote = base / "remote.git"
        _init_bare(remote)
        # 远端首提交
        src = base / "src"
        _init_git(src, file="a.txt", content="v1")
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=src, check=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=src, check=True)
        # 工作克隆（走本地路径远端）
        work = base / "clones" / "my-proj"
        subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
        # 远端推新提交
        (src / "a.txt").write_text("v2", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=src, check=True)
        subprocess.run(["git", "commit", "-m", "v2"], cwd=src, check=True, capture_output=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=src, check=True)
        # 通过 to_spec 导入（本地路径远端）→ sync 应增量更新而非跳过
        from gcm.app.scanner import RepoInfo, to_spec
        info = RepoInfo(folder_name="my-proj", display="x/my-proj", path=str(work),
                        remote_url=str(remote))
        spec = to_spec(info)
        self.assertFalse(spec.is_local, "本地路径远端不应判为纯本地")
        svc = GitService(base / "svcroot")
        res = svc.sync(spec)
        self.assertEqual(res.status, SyncStatus.SUCCESS)
        self.assertEqual(res.commits, 1)
        # HEAD 与远端一致
        r = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                           capture_output=True, text=True, check=True)
        self.assertEqual(r.stdout.strip(), res.head_sha)


class TestShallowIncremental(unittest.TestCase):
    """B3：浅克隆仓库增量更新（真实裸仓库，shallow fetch + merge）。"""

    def _shallow_clone(self, remote: Path, work: Path) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", "clone", "-q", "--depth=1", str(remote), str(work)], check=True)

    def test_shallow_detection(self):
        from gcm.git.service import GitService
        base = Path(tempfile.mkdtemp())
        remote = base / "r.git"
        _init_bare(remote)
        src = base / "src"
        _init_git(src, file="a.txt", content="v1")
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=src, check=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=src, check=True)
        work = base / "w"
        subprocess.run(["git", "clone", "-q", str(remote), str(work)], check=True)
        svc = GitService(base / "svc")
        self.assertFalse(svc._is_shallow_repo(work))   # 满量克隆不是 shallow

    def test_is_shallow_repo_detects_marker(self):
        """直接用 .git/shallow 标记文件验证检测逻辑（不依赖真实浅克隆）。"""
        from gcm.git.service import GitService
        base = Path(tempfile.mkdtemp())
        repo = base / "w"
        _init_git(repo)
        (repo / ".git" / "shallow").write_text(
            "4b825dc642cb6eb9a060e54bf8d69288fbee4904", encoding="utf-8")
        svc = GitService(base / "svc")
        self.assertTrue(svc._is_shallow_repo(repo))
        (repo / ".git" / "shallow").unlink()
        self.assertFalse(svc._is_shallow_repo(repo))

    def test_shallow_incremental_update_full_fetch_succeeds(self):
        """浅克隆仓库用无 depth 的全量 fetch 也能增量更新（现代 git 渐进式 shallow fetch）。"""
        from gcm.git.service import GitService
        from gcm.models import SyncStatus
        base = Path(tempfile.mkdtemp())
        remote = base / "r.git"
        _init_bare(remote)
        src = base / "src"
        _init_git(src, file="a.txt", content="v1")
        subprocess.run(["git", "remote", "add", "origin", str(remote)], cwd=src, check=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=src, check=True)
        work = base / "w"
        subprocess.run(["git", "clone", "-q", "--depth=1", str(remote), str(work)],
                       check=True, capture_output=True)
        # 远端推新
        (src / "a.txt").write_text("v2", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=src, check=True)
        subprocess.run(["git", "commit", "-m", "v2"], cwd=src, check=True, capture_output=True)
        subprocess.run(["git", "push", "-q", "origin", "HEAD:main"], cwd=src, check=True)
        svc = GitService(base / "svc", fetch_depth=1)
        spec = RepoSpec(owner="x", repo="w", url_https=str(remote),
                        folder_name="w", local_path=str(work))
        res = svc.sync(spec)
        self.assertEqual(res.status, SyncStatus.SUCCESS, res.detail)
        self.assertEqual(res.commits, 1)
        # 本地确实拿到 v2
        r = subprocess.run(["git", "-C", str(work), "rev-parse", "HEAD"],
                           capture_output=True, text=True, check=True)
        self.assertEqual(r.stdout.strip(), res.head_sha)


if __name__ == "__main__":
    unittest.main()
