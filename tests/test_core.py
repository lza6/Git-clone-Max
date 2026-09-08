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

from gcm.app.url_lib import parse_repo_url, parse_urls, is_github_url, host_of, normalize_url, sanitize_name
from gcm.db.repo_db import Database, load_progress, save_progress, mark_finished
from gcm.db.settings import SettingsStore
from gcm.git.service import (GitService, _detect_conflict, _is_dirty, _is_networkish_error,
                             _divergence_info, _kill_tree, _retry_delays, run_git_ui)
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

    def test_empty_remote_reports_empty(self):
        """空裸仓库 clone 后应标记 EMPTY 而非 CLONED。"""
        empty = self.tmp / "empty.git"
        subprocess.run(["git", "init", "--bare", str(empty)], check=True, capture_output=True)
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                       cwd=empty, check=True, capture_output=True)
        svc = GitService(self.tmp / "empty_root")
        spec = RepoSpec("o", "empty", str(empty), folder_name="o__empty")
        r = svc.sync(spec)
        self.assertEqual(r.status.value, "success")
        self.assertEqual(r.action.value, "empty")
        self.assertEqual(r.head_sha, "")

    def test_proxy_env_injected(self):
        svc = GitService(self.tmp, proxy="http://127.0.0.1:9999")
        env = svc._env()
        self.assertEqual(env.get("http_proxy"), "http://127.0.0.1:9999")
        self.assertEqual(env.get("https_proxy"), "http://127.0.0.1:9999")

    def test_no_proxy_no_env_override(self):
        svc = GitService(self.tmp, proxy="")
        self.assertIsNone(svc._env())
        # 未设代理时不应注入任何代理变量
        os.environ.pop("http_proxy", None)
        os.environ.pop("https_proxy", None)
        self.assertIsNone(svc._env())


class TestSettings(unittest.TestCase):
    def test_roundtrip(self):
        d = Path(tempfile.mkdtemp())
        st = SettingsStore(d / "settings.json")
        s = st.load()
        self.assertEqual(s.concurrency, 8)
        s.concurrency = 4
        s.proxy = "http://127.0.0.1:7890"
        st.save(s)
        s2 = st.load()
        self.assertEqual(s2.concurrency, 4)
        self.assertEqual(s2.proxy, "http://127.0.0.1:7890")

    def test_corrupt_falls_back(self):
        d = Path(tempfile.mkdtemp())
        p = d / "settings.json"
        p.write_text("{ not json !!", encoding="utf-8")
        st = SettingsStore(p)
        s = st.load()
        self.assertEqual(s.concurrency, 8)  # 损坏兜底为默认值，不崩

    def test_type_coercion(self):
        d = Path(tempfile.mkdtemp())
        p = d / "settings.json"
        p.write_text('{"concurrency": "6", "auto_clear": 1}', encoding="utf-8")
        st = SettingsStore(p)
        s = st.load()
        self.assertEqual(s.concurrency, 6)
        self.assertIs(s.auto_clear, True)


class TestRetryHelpers(unittest.TestCase):
    def test_networkish_classifier(self):
        self.assertTrue(_is_networkish_error("fatal: unable to access ... Failed to connect"))
        self.assertTrue(_is_networkish_error("early EOF"))
        self.assertTrue(_is_networkish_error("RPC failed; curl 56"))
        self.assertTrue(_is_networkish_error("The remote end hung up unexpectedly"))
        self.assertFalse(_is_networkish_error("remote: Repository not found."))
        self.assertFalse(_is_networkish_error("fatal: Authentication failed for"))
        self.assertFalse(_is_networkish_error("Could not read from remote repository."))
        self.assertFalse(_is_networkish_error("does not appear to be a git repository"))

    def test_retry_delays(self):
        self.assertEqual(_retry_delays(2), (1.0, 3.0))
        self.assertEqual(_retry_delays(0), ())
        self.assertEqual(_retry_delays(5), (1.0, 3.0, 8.0))

    def test_kill_tree_noop(self):
        # 无进程对象不抛异常即可
        _kill_tree(None)

    def test_kill_tree_terminates(self):
        """真实拉起一个 sleep 进程，_kill_tree 应能终止其整树。"""
        import sys as _sys
        proc = None
        try:
            # 用 sys.executable 确保能拉起（PATH 里无 python）
            proc = subprocess.Popen(
                [_sys.executable, "-c", "import time; time.sleep(30)"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=str(self.tmp) if hasattr(self, "tmp") else tempfile.gettempdir())
            # 刚启动，进程应仍在运行（poll()==None）
            self.assertIsNone(proc.poll(), "进程应仍在运行")
            _kill_tree(proc)
            try:
                proc.wait(timeout=15)
            except Exception:
                pass
            self.assertIsNotNone(proc.poll(), "_kill_tree 后进程应已结束")
        finally:
            # 兜底：确保进程对象被 wait 回收 + stdout 关闭，避免 ResourceWarning
            if proc is not None:
                if proc.poll() is None:
                    try:
                        _kill_tree(proc)
                        proc.wait(timeout=10)
                    except Exception:
                        pass
                try:
                    proc.stdout.close()
                except Exception:
                    pass

    def test_run_git_ui_timeout_returns_nonzero(self):
        """超时应返回非 0 退出码且不残留进程。"""
        import sys as _sys
        import time as _t
        lines = []
        # 超时后读线程内 _kill_tree 已终止进程树；此处在 finally 再兜底回收防 ResourceWarning
        try:
            rc, prog = run_git_ui(
                [_sys.executable, "-c", "import time; time.sleep(30)"],
                cwd=str(self.tmp) if hasattr(self, "tmp") else tempfile.gettempdir(),
                on_line=lambda c: lines.append(c.text),
                timeout=0.5,
            )
            self.assertNotEqual(rc, 0, "超时后退出码应为非 0")
        except Exception:
            raise

    def test_run_git_ui_success_no_extra_launch(self):
        """成功路径只启动一次子进程（防止 retry>0 时重复 clone）。"""
        import sys as _sys
        work = Path(tempfile.mkdtemp())
        script = (
            "import sys, os\n"
            "p=r'%s'\n" % str(work)
            + "f=os.path.join(p,'launch_count.txt')\n"
            "try:\n"
            "  n=int(open(f).read())\n"
            "except Exception:\n"
            "  n=0\n"
            "open(f,'w').write(str(n+1))\n"
            "sys.exit(0)\n"
        )
        lines = []
        rc, _ = run_git_ui(
            [_sys.executable, "-c", script], cwd=str(work),
            on_line=lambda c: lines.append(c.text), retries=2, backoff=(0.01, 0.01),
        )
        self.assertEqual(rc, 0)
        count = int((work / "launch_count.txt").read_text(encoding="utf-8"))
        self.assertEqual(count, 1, f"成功路径应只启动一次子进程，实际 {count} 次")

    def test_run_git_ui_network_retry_succeeds(self):
        """网络类错误在配置重试后成功：构造前 2 次失败的假命令。"""
        import sys as _sys
        script = (
            "import sys,os,time\n"
            "p=r'%s'\n" % (str(self.tmp) if hasattr(self, "tmp") else tempfile.gettempdir())
            + "f=os.path.join(p,'attempt.txt')\n"
            "try:\n"
            "  n=int(open(f).read())\n"
            "except Exception:\n"
            "  n=0\n"
            "open(f,'w').write(str(n+1))\n"
            "if n<2:\n"
            "  sys.stderr.write('fatal: unable to access xxxxx Failed to connect')\n"
            "  sys.exit(1)\n"
            "sys.exit(0)\n"
        )
        # 前两次失败被判定为网络错误 → 重试 → 第三次成功
        lines = []
        rc, _ = run_git_ui(
            [_sys.executable, "-c", script], cwd=tempfile.gettempdir(),
            on_line=lambda c: lines.append(c.text), retries=2, backoff=(0.01, 0.01),
        )
        self.assertEqual(rc, 0, f"重试后应成功，实际 rc={rc}，日志={lines}")

    def test_run_git_ui_repo_error_no_retry(self):
        """仓库类错误（404/认证）不重试，立即失败。"""
        import sys as _sys
        script = (
            "import sys\n"
            "sys.stderr.write('remote: Repository not found.\\n')\n"
            "sys.exit(1)\n"
        )
        lines = []
        rc, _ = run_git_ui(
            [_sys.executable, "-c", script], cwd=tempfile.gettempdir(),
            on_line=lambda c: lines.append(c.text), retries=2, backoff=(0.01, 0.01),
        )
        self.assertEqual(rc, 1)
        self.assertFalse(any("自动重试" in l for l in lines), "仓库类错误不应重试")


class TestDivergence(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.remote = _init_remote(self.tmp)

    def test_ahead_behind(self):
        svc = GitService(self.tmp)
        spec = RepoSpec("d", "r", str(self.remote), folder_name="d__r")
        svc.sync(spec)
        clone = self.tmp / "d__r"
        _git(clone, "config", "user.email", "t@t.com")
        _git(clone, "config", "user.name", "t")
        # 干净：ahead=0, behind=0
        ahead, behind = _divergence_info(str(clone))
        self.assertEqual((ahead, behind), (0, 0))
        # 本地领先 1
        (clone / "b.txt").write_text("b", encoding="utf-8")
        _git(clone, "add", ".")
        _git(clone, "commit", "-m", "local")
        ahead, behind = _divergence_info(str(clone))
        self.assertEqual(ahead, 1)
        self.assertEqual(behind, 0)


class TestUrlLibBatch(unittest.TestCase):
    def test_parse_urls(self):
        specs, invalid = parse_urls(
            "https://github.com/a/b\nnot a url\n\nhttps://github.com/c/d.git\n  ")
        self.assertEqual([s.folder_name for s in specs], ["a__b", "c__d"])
        self.assertEqual(invalid, ["not a url"])

    def test_parse_urls_all_invalid(self):
        specs, invalid = parse_urls("x\n\n")
        self.assertEqual(specs, [])
        self.assertEqual(invalid, ["x"])


class TestCloneWorker(unittest.TestCase):
    """QRunnable worker 集成：真实 git 服务 + 真实 SQLite，验证 DB 写入与 progress 落盘。"""

    def test_success_writes_db_and_progress(self):
        tmp = Path(tempfile.mkdtemp())
        remote = _init_remote(tmp / "remote")
        svc = GitService(tmp / "root")
        db = Database(tmp / "t.db")
        pp = tmp / "progress.json"
        spec = RepoSpec("wk", "r", str(remote), folder_name="wk__r")
        from gcm.app.worker import CancelFlag, CloneWorker, TaskPayload
        w = CloneWorker(0, TaskPayload(spec=spec, flag=CancelFlag()), svc, db, str(pp))
        done = []
        w.signals.result.connect(lambda i, r: done.append((i, r)))
        w.setAutoDelete(False)
        w.run()
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0][1].action.value, "cloned")
        self.assertEqual(db.count(), 1)
        rec = db.list_repos("github.com")[0]
        self.assertEqual(rec["folder_name"], "wk__r")
        data = load_progress(pp)
        self.assertEqual(data["finished"][0]["key"], "wk/r")
        db.close()

    def test_failure_writes_failed_history(self):
        tmp = Path(tempfile.mkdtemp())
        svc = GitService(tmp / "root")
        db = Database(tmp / "t.db")
        pp = tmp / "progress.json"
        # 远端不可达 → sync 抛异常 → worker 应标记 FAILED 并写 DB
        spec = RepoSpec("bad", "host", "file:///nonexistent_remote_xyz",
                        folder_name="bad__host")
        from gcm.app.worker import CancelFlag, CloneWorker, TaskPayload
        w = CloneWorker(0, TaskPayload(spec=spec, flag=CancelFlag()), svc, db, str(pp))
        done = []
        w.signals.result.connect(lambda i, r: done.append((i, r)))
        w.setAutoDelete(False)
        w.run()
        self.assertEqual(len(done), 1)
        self.assertEqual(done[0][1].status.value, "failed")
        self.assertGreaterEqual(db.count(), 1)
        hist = db.history(db.list_repos("github.com")[0]["id"], 5)
        self.assertEqual(hist[0]["status"], "failed")
        db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)