# -*- coding: utf-8 -*-
"""补充测试：补齐 baseline 中未覆盖的边界路径（url_lib / settings / repo_db / worker / git service / updater / tray / __main__ / MainWindow）。"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.url_lib import (
    parse_repo_url, parse_urls, is_github_url, host_of, normalize_url, sanitize_name,
)
from gcm.db.repo_db import Database, load_progress, mark_finished
from gcm.db.settings import SettingsStore
from gcm.git.service import (
    GitService, _detect_conflict, _is_dirty, _progress_from_line, run_git_ui,
    _divergence_info, _kill_tree,
)
from gcm.models import RepoSpec
from gcm.ui.theme import LogLevel


def _git(cwd, *args):
    return subprocess.run(["git", "-C", str(cwd), *args],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


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
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                   cwd=remote, check=True, capture_output=True)
    return remote


# ---------------------------------------------------------------- url_lib
class TestUrlLibExtra(unittest.TestCase):
    def test_normalize_url(self):
        self.assertEqual(normalize_url("https://github.com/a/b"),
                         "https://github.com/a/b.git")
        self.assertIsNone(normalize_url("not a url"))

    def test_is_github_url(self):
        self.assertTrue(is_github_url("github.com/a/b"))
        self.assertFalse(is_github_url("gitlab.com/a/b"))

    def test_host_known_and_ssh(self):
        self.assertEqual(host_of("https://github.com/a/b"), "github.com")
        self.assertEqual(host_of("ssh://git@gitlab.com/x/y.git"), "gitlab.com")
        self.assertEqual(host_of("git@custom.host:o/r.git"), "custom.host")
        self.assertEqual(host_of("https://codeberg.org/a/b"), "codeberg.org")
        self.assertEqual(host_of(""), "")
        self.assertEqual(host_of("https://example.com/a/b"), "example.com")

    def test_sanitize_boundary(self):
        self.assertEqual(sanitize_name("..."), "repo")      # 点清洗后为空 → 兜底
        self.assertEqual(sanitize_name("   ok   "), "ok")
        self.assertEqual(sanitize_name("a\x00b"), "a_b")    # 控制字符

    def test_whitespace_only_lines_ignored(self):
        specs, invalid = parse_urls("  \n\t\n")
        self.assertEqual(specs, [])
        self.assertEqual(invalid, [])


# ---------------------------------------------------------------- settings
class TestSettingsExtra(unittest.TestCase):
    def test_coerce_failure_falls_back_to_default(self):
        d = Path(tempfile.mkdtemp())
        p = d / "settings.json"
        p.write_text('{"concurrency": "abc", "depth": "zz"}', encoding="utf-8")
        s = SettingsStore(p).load()
        self.assertEqual(s.concurrency, 8)   # int("abc") 抛异常 → 保持默认
        self.assertEqual(s.depth, 1)

    def test_unknown_keys_ignored(self):
        d = Path(tempfile.mkdtemp())
        p = d / "settings.json"
        p.write_text('{"no_such_key": 123, "concurrency": 2}', encoding="utf-8")
        s = SettingsStore(p).load()
        self.assertEqual(s.concurrency, 2)
        self.assertFalse(hasattr(s, "no_such_key"))


# ---------------------------------------------------------------- repo_db
class TestRepoDbExtra(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "t.db")

    def tearDown(self):
        self.db.close()

    def test_load_progress_missing_and_corrupt(self):
        missing = load_progress(self.tmp / "nope.json")
        self.assertEqual(missing, {"finished": [], "in_progress": {}})
        bad = self.tmp / "bad.json"
        bad.write_text("{oops", encoding="utf-8")
        self.assertEqual(load_progress(bad), {"finished": [], "in_progress": {}})

    def test_set_repo_head(self):
        spec = RepoSpec("o", "r", "https://github.com/o/r.git", folder_name="o__r")
        rid = self.db.upsert_repo(spec, "x")
        self.db.set_repo_head(rid, "sha1", default_branch="main")
        rec = self.db.get_repo("o", "r")
        self.assertEqual(rec["head_sha"], "sha1")
        self.assertEqual(rec["default_branch"], "main")
        # 不传分支：只更新 head
        self.db.set_repo_head(rid, "sha2")
        rec = self.db.get_repo("o", "r")
        self.assertEqual(rec["head_sha"], "sha2")
        self.assertEqual(rec["default_branch"], "main")

    def test_close_idempotent(self):
        self.db.close()
        self.db.close()  # 不应抛异常


class TestMarkFinishedReplace(unittest.TestCase):
    def test_mark_finished_dedups_key(self):
        p = Path(tempfile.mkdtemp()) / "p.json"
        mark_finished(p, RepoSpec("o", "r", "u", folder_name="o__r"), {"status": "ok"})
        mark_finished(p, RepoSpec("o", "r", "u", folder_name="o__r"), {"status": "ok2"})
        data = load_progress(p)
        self.assertEqual(len(data["finished"]), 1)
        self.assertEqual(data["finished"][0]["status"], "ok2")


# ---------------------------------------------------------------- worker / service 边界
class TestServiceExtra(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.remote = _init_remote(self.tmp)

    def test_progress_from_line(self):
        self.assertEqual(_progress_from_line("Receiving objects:  45% (9/20)"), "45")
        self.assertIsNone(_progress_from_line("no percent here"))
        self.assertEqual(_progress_from_line("Total 100%"), "100")

    def test_run_git_ui_success(self):
        lines = []
        rc, prog = run_git_ui(["git", "--version"], str(self.tmp),
                              lambda c: lines.append(c.text))
        self.assertEqual(rc, 0)
        self.assertTrue(any("git version" in L for L in lines))

    def test_run_git_ui_cancelled(self):
        flag = {"v": False}
        rc, _ = run_git_ui(["cmd.exe", "/c", "echo hi && exit 0"], str(self.tmp),
                           lambda c: None,
                           cancelled=lambda: True)
        self.assertEqual(rc, -1)  # 取消路径被整树终止 → 非零退出码

    def test_run_git_ui_timeout(self):
        # 用 python sleep（可可靠地被 _kill_tree 终止）验证超时返回非 0
        import sys as _sys
        rc, _ = run_git_ui([_sys.executable, "-c", "import time; time.sleep(30)"],
                           str(self.tmp), lambda c: None, timeout=0.5)
        self.assertNotEqual(rc, 0, "超时应返回非 0 退出码")

    def test_run_git_ui_retries_repo_error_no_retry(self):
        calls = []
        rc, _ = run_git_ui(["git", "clone", "file:///nonexistent_xyz", "x"],
                           str(self.tmp), lambda c: calls.append(c.text),
                           retries=2, timeout=10)
        # 仓库类错误：不重试（只跑 1 次），直接返回失败
        self.assertNotEqual(rc, 0)
        # 一次 clone 会输出多行（Cloning into… / fatal… 等），按"Cloning into"计数调用次数
        launches = sum(1 for c in calls if "Cloning into" in c)
        self.assertEqual(launches, 1, f"仓库类错误不应重试，实际 {launches} 次启动")

    def test_detect_conflict_dirty_and_ahead(self):
        """本地领先上游（已提交）+ 存在未提交改动 → 冲突；仅已提交领先不冲突。"""
        svc = GitService(self.tmp)
        spec = RepoSpec("d", "r", str(self.remote), folder_name="d__r")
        svc.sync(spec)
        clone = self.tmp / "d__r"
        _git(clone, "config", "user.email", "t@t.com")
        _git(clone, "config", "user.name", "t")
        (clone / "b.txt").write_text("b", encoding="utf-8")
        _git(clone, "add", ".")
        _git(clone, "commit", "-m", "local")   # 已提交：本地领先 1
        # 仅已提交领先（clean）→ 不判冲突（该场景由 sync 的 ahead 分支/merge 失败处理）
        conflict, reason = _detect_conflict(str(clone))
        self.assertFalse(conflict)
        self.assertEqual(reason, "")
        # 制造「已提交领先 + 额外未提交改动」→ 此时才应判冲突
        (clone / "c.txt").write_text("c", encoding="utf-8")   # 未跟踪：dirty
        conflict, reason = _detect_conflict(str(clone))
        self.assertTrue(conflict)
        self.assertIn("领先", reason)

    def test_dirty_without_divergence_not_conflict(self):
        svc = GitService(self.tmp)
        spec = RepoSpec("e", "r", str(self.remote), folder_name="e__r")
        svc.sync(spec)
        clone = self.tmp / "e__r"
        (clone / "ut.txt").write_text("untracked", encoding="utf-8")  # 未跟踪：dirty 但未领先
        conflict, reason = _detect_conflict(str(clone))
        self.assertFalse(conflict)

    def test_helpers_on_nonrepo(self):
        svc = GitService(self.tmp)
        nope = self.tmp / "nonexistent"
        self.assertEqual(svc._local_branch(nope), "main")   # git 失败回退
        self.assertEqual(svc._head_sha(nope), "")
        self.assertEqual(svc._remote_head(nope), "")
        self.assertEqual(_is_dirty(str(nope)), True)         # 异常时默认脏
        self.assertEqual(_divergence_info(str(nope)), (0, 0))

    def test_current_branch(self):
        svc = GitService(self.tmp)
        spec = RepoSpec("c", "r", str(self.remote), folder_name="c__r")
        svc.sync(spec)
        self.assertEqual(svc.current_branch(self.tmp / "c__r"), "main")

    def test_kill_tree_real_proc(self):
        proc = subprocess.Popen(["cmd.exe", "/c", "ping -n 30 127.0.0.1"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _kill_tree(proc)  # 不应抛异常
        proc.wait(timeout=15)


class TestCancelFlag(unittest.TestCase):
    def test_flag(self):
        from gcm.app.worker import CancelFlag
        f = CancelFlag()
        self.assertFalse(f())
        f.cancel()
        self.assertTrue(f())


class TestWorkerCancel(unittest.TestCase):
    def test_cancelled_result(self):
        from gcm.app.worker import CancelFlag, CloneWorker, TaskPayload
        tmp = Path(tempfile.mkdtemp())
        remote = _init_remote(tmp / "remote")
        db = Database(tmp / "t.db")
        pp = tmp / "progress.json"
        spec = RepoSpec("c", "r", str(remote), folder_name="c__r")
        flag = CancelFlag()
        flag.cancel()  # 预置取消：sync 内 clone 前 cancelled() 为 True
        cw = tmp / "root2"
        svc2 = GitService(cw)
        svc2.cancelled = flag
        w = CloneWorker(0, TaskPayload(spec=spec, flag=flag), svc2, db, str(pp))
        done = []
        w.signals.result.connect(lambda i, r: done.append((i, r)))
        w.setAutoDelete(False)
        w.run()
        self.assertEqual(done[0][1].status.value, "cancelled")
        db.close()


# ---------------------------------------------------------------- updater
class TestUpdaterExtra(unittest.TestCase):
    def test_parse_version_edge(self):
        from gcm.app.updater import _parse_version
        self.assertEqual(_parse_version(""), (0, 0, 0, 0))
        self.assertEqual(_parse_version("v2"), (2, 0, 0, 0))
        self.assertEqual(_parse_version("v2.0.0-alpha.1"), (2, 0, 0, -1))
        self.assertEqual(_parse_version("v2.0.0-pre.2"), (2, 0, 0, -1))
        self.assertLess(_parse_version("v2.0.0-beta.1"), _parse_version("v2.0.0"))

    def test_check_latest_no_exe_asset(self):
        from gcm.app import updater
        # 同版本（v3.0.0 = 当前 __version__）且无 exe 资产 → 不是新版本
        payload = {"tag_name": "v3.0.0", "assets": [],
                   "html_url": "https://example.com/releases/latest"}
        has_new, ver, url, err = updater.check_latest(fetcher=lambda: json.dumps(payload))
        self.assertFalse(has_new, "同版本不应判定为更新")
        # 更高版本 + 无 exe 资产 → 有更新且 url 兜底 html_url
        payload2 = {"tag_name": "v9.9.9", "assets": [],
                    "html_url": "https://example.com/releases/latest"}
        has_new2, ver2, url2, err2 = updater.check_latest(fetcher=lambda: json.dumps(payload2))
        self.assertTrue(has_new2)
        self.assertEqual(url2, "https://example.com/releases/latest")  # 兜底 html_url
        self.assertEqual(ver2, "v9.9.9")

    def test_check_latest_bad_json(self):
        from gcm.app import updater
        has_new, _, _, err = updater.check_latest(fetcher=lambda: "{not json")
        self.assertFalse(has_new)
        self.assertIn("检查更新失败", err)


# ---------------------------------------------------------------- tray
class TestTray(unittest.TestCase):
    def test_ensure_supported_bool(self):
        from gcm.ui.tray import ensure_supported
        self.assertIsInstance(ensure_supported(), bool)

    def test_notify_fallback(self):
        from gcm.ui.tray import TrayController
        got = []
        tc = TrayController(parent=None, notify=lambda t, m: got.append((t, m)))
        tc.notify("标题", "消息")   # tray 为 None → 走 fallback
        self.assertEqual(got, [("标题", "消息")])


# ---------------------------------------------------------------- __main__
class TestGetDataDir(unittest.TestCase):
    def test_env_override(self):
        import gcm.__main__ as m
        d = Path(tempfile.mkdtemp()) / "custom"
        os.environ["GCM_DATA_DIR"] = str(d)
        try:
            got = m.get_data_dir()
            self.assertEqual(got, d)
            self.assertTrue(d.exists())
        finally:
            os.environ.pop("GCM_DATA_DIR", None)

    def test_default_dir_created(self):
        import gcm.__main__ as m
        # 清掉环境变量，默认应落在包上级 data/
        os.environ.pop("GCM_DATA_DIR", None)
        got = m.get_data_dir()
        self.assertTrue(got.is_dir())
        self.assertEqual(got.name, "data")


# ---------------------------------------------------------------- MainWindow 补充
class TestMainWindowExtra(unittest.TestCase):
    def test_slots_and_actions(self):
        from PyQt6.QtWidgets import QApplication, QFileDialog, QMessageBox
        from gcm.ui.main_window import MainWindow
        app = QApplication.instance() or QApplication(sys.argv)
        d = Path(tempfile.mkdtemp())
        win = MainWindow(data_dir=d,
                         db=Database(d / "t.db"),
                         settings=SettingsStore(d / "settings.json"))
        # clear_log
        win.log.append("2026-01-01 00:00:00", LogLevel.INFO, "x")
        win.clear_log()
        self.assertEqual(len(win.log._entries), 0)
        # _save_proxy
        win.edit_proxy.setText("http://p:1")
        win._save_proxy()
        self.assertEqual(win.settings.proxy, "http://p:1")
        # _any_cancel / cancel_all（无任务时不抛）
        self.assertFalse(win._any_cancel())
        win.cancel_all()
        # show_history 空记录 → 弹"暂无记录"
        win.db.upsert_repo(RepoSpec("o", "r", "https://github.com/o/r.git"), "x")
        infos = []
        orig_info = QMessageBox.information
        QMessageBox.information = lambda *a, **k: infos.append(a)
        rid = win.db.list_repos("github.com")[0]["id"]
        win.show_history(rid)
        self.assertEqual(infos[0][2], "暂无记录")
        QMessageBox.information = orig_info
        # delete_selected：未选中 → 提示（不崩，需先补桩弹窗）
        info2 = []
        QMessageBox.information = lambda *a, **k: info2.append(a)
        win.delete_selected()
        self.assertGreaterEqual(len(info2), 1)
        QMessageBox.information = orig_info
        # save_log 取消（无路径）
        orig_sf = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = lambda *a, **k: ("", "")
        win.save_log()
        QFileDialog.getSaveFileName = orig_sf
        # closeEvent（无 busy）
        win.close()
        # tray 置 None 后 finished 槽不崩
        win.tray = None
        win._on_worker_finished()  # busy False → 直接返回
        self.assertFalse(win.busy)


if __name__ == "__main__":
    unittest.main(verbosity=2)