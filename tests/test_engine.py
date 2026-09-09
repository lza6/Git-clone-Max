# -*- coding: utf-8 -*-
"""并发调度引擎（SyncEngine）E2E 测试：真实 git 批量并发、去重、进度落盘、断点续传。

对应 计划书/下一步改进指南.md 第五章 T5 验收标准：
- 并发 8/32 批量克隆（真实裸仓库）全部成功，无 PermissionError
- 相同 owner/repo 重复提交 → 去重，仅同步一次
- 失败仓库不中断整体，其余继续
- progress.json 由引擎统一落盘（周期 + 完成时强制），原子且结构正确
- 断点续传：已完成且目录在 → 二次 launch 直接跳过
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 所有 engine 测试都需要 Qt 事件循环（QRunnable 后台信号投递到主线程）。
# 用 QApplication（而非 QCoreApplication）：与 tests/test_ui.py 共用同一实例，
# 避免先建 QCoreApplication 再建 QApplication 触发 Qt 致命错误。
from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gcm.app.engine import SyncEngine
from gcm.db.repo_db import Database, load_progress, save_progress
from gcm.models import RepoSpec, SyncStatus


def _pump():
    """泵 Qt 事件：让跨线程信号得以派发（QRunnable 后台线程 emit → 主线程槽）。"""
    for _ in range(20):
        _app.processEvents()
        time.sleep(0.01)


# pylint: disable=no-member


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def _init_remote(tmp: Path, name: str) -> Path:
    remote = tmp / f"{name}.git"
    work = tmp / f"w_{name}"
    subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
    subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
                   check=True, capture_output=True)
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


class TestEngineConcurrency(unittest.TestCase):
    """验证：并发 8 批量克隆真实 git 全部成功，progress 落盘正确，finished 收敛。"""

    def test_batch_clone_8_concurrent(self):
        base = Path(tempfile.mkdtemp())
        N = 8
        remotes = {f"o{i}": _init_remote(base, f"r{i}") for i in range(N)}
        specs = [_spec(f"o{i}", f"r{i}", remotes[f"o{i}"]) for i in range(N)]

        d = base / "data"
        d.mkdir()
        db = Database(d / "t.db")
        pp = d / "progress.json"
        engine = SyncEngine(base / "clones", db=db, progress_path=pp, concurrency=8)
        results = []
        lines = []
        ended = []
        engine.result.connect(lambda i, r: results.append((i, r)))
        engine.line.connect(lambda i, t, l: lines.append(t))
        engine.finished.connect(lambda: ended.append(True))

        launched = engine.launch(specs)
        self.assertEqual(launched, N)
        # 等待全部完成（泵事件循环：跨线程信号需要事件循环派发）
        deadline = time.time() + 120
        while not ended and time.time() < deadline:
            _pump()
        self.assertTrue(ended, "finished 信号应在所有 worker 完成后触发")
        self.assertEqual(len(results), N, "应收到 N 个 result")
        for i, r in results:
            self.assertEqual(r.status, SyncStatus.SUCCESS, f"仓库 {i} 应成功，got {r.status}: {r.message}")
            # 目录确实存在
            self.assertTrue((base / "clones" / r.spec.folder_name / "a.txt").exists())

        # progress 落盘：结构正确，N 个 finished
        data = json.loads(Path(pp).read_text(encoding="utf-8"))
        self.assertEqual(len(data["finished"]), N)
        self.assertTrue(all(x.get("status") == "success" for x in data["finished"]))

        # 数据库已记录
        self.assertEqual(db.count(), N)
        db.close()
        engine.shutdown()

    def test_dedupe_same_repo(self):
        """重复提交同一仓库 → 去重为 1，且日志有提示。"""
        base = Path(tempfile.mkdtemp())
        remote = _init_remote(base, "dup")
        d = base / "data"
        d.mkdir()
        db = Database(d / "t.db")
        pp = d / "progress.json"
        engine = SyncEngine(base / "clones", db=db, progress_path=pp, concurrency=4)
        results = []
        lines = []
        ended = []
        engine.result.connect(lambda i, r: results.append(r))
        engine.line.connect(lambda i, t, l: lines.append(t))
        engine.finished.connect(lambda: ended.append(True))

        specs = [_spec("dup", "r", remote) for _ in range(3)]  # 同一仓库 x3
        launched = engine.launch(specs)
        self.assertEqual(launched, 1, "去重后只应调度 1 个 worker")
        deadline = time.time() + 60
        while not ended and time.time() < deadline:
            _pump()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].status, SyncStatus.SUCCESS)
        self.assertTrue(any("去重" in l for l in lines), "应有去重日志")
        db.close()
        engine.shutdown()

    def test_failure_does_not_abort_batch(self):
        """一个仓库坏远端（仓库类错误）失败，其余照常成功。"""
        base = Path(tempfile.mkdtemp())
        good = _init_remote(base, "good")
        d = base / "data"
        d.mkdir()
        db = Database(d / "t.db")
        pp = d / "progress.json"
        engine = SyncEngine(base / "clones", db=db, progress_path=pp, concurrency=4)
        results = []
        ended = []
        engine.result.connect(lambda i, r: results.append(r))
        engine.finished.connect(lambda: ended.append(True))

        specs = [
            _spec("u", "bad", base / "nonexistent_beyond_zzz"),  # 仓库类错误（不重试）
            _spec("o1", "good", good),
        ]
        engine.launch(specs)
        deadline = time.time() + 90
        while not ended and time.time() < deadline:
            _pump()
        self.assertEqual(len(results), 2)
        by_folder = {r.spec.folder_name: r for r in results}
        self.assertEqual(by_folder["o1__good"].status, SyncStatus.SUCCESS)
        self.assertEqual(by_folder["u__bad"].status, SyncStatus.FAILED)
        db.close()
        engine.shutdown()

    def test_resume_skips_done(self):
        """断点续传：第一次成功后 progress 有记录，二次 launch 直接跳过（finished 且目录在）。"""
        base = Path(tempfile.mkdtemp())
        remote = _init_remote(base, "resume")
        d = base / "data"
        d.mkdir()
        db = Database(d / "t.db")
        pp = d / "progress.json"
        engine = SyncEngine(base / "clones", db=db, progress_path=pp, concurrency=2)
        ended = []
        engine.finished.connect(lambda: ended.append(True))
        spec = _spec("resume", "r", remote)
        engine.launch([spec])
        deadline = time.time() + 60
        while not ended and time.time() < deadline:
            _pump()
        # 第一次完成
        data = json.loads(Path(pp).read_text(encoding="utf-8"))
        self.assertEqual(len(data["finished"]), 1)
        # 二次 launch：目标目录存在 → 直接跳过（不重新 clone）
        ended2 = []
        engine2 = SyncEngine(base / "clones", db=db, progress_path=pp, concurrency=2)
        engine2.finished.connect(lambda: ended2.append(True))
        lines = []
        engine2.line.connect(lambda i, t, l: lines.append(t))
        engine2.launch([spec], check_existing=True)
        deadline2 = time.time() + 60
        while not ended2 and time.time() < deadline2:
            _pump()
        self.assertTrue(any("跳过" in l for l in lines), "应跳过已完成仓库")
        db.close()
        engine.shutdown()
        engine2.shutdown()


class TestEngineConcurrencyExtra(unittest.TestCase):
    """额外：高并发拉满 + 平台限制类错误不重试。"""

    def test_high_concurrency_32(self):
        """并发 32：验证拉满速度时无 PermissionError / 锁死，全部成功。"""
        base = Path(tempfile.mkdtemp())
        N = 12
        remotes = {f"o{i}": _init_remote(base, f"r{i}") for i in range(N)}
        specs = [_spec(f"o{i}", f"r{i}", remotes[f"o{i}"]) for i in range(N)]

        d = base / "data"
        d.mkdir()
        db = Database(d / "t.db")
        pp = d / "progress.json"
        engine = SyncEngine(base / "clones", db=db, progress_path=pp, concurrency=32)
        results = []
        ended = []
        engine.result.connect(lambda i, r: results.append((i, r)))
        engine.finished.connect(lambda: ended.append(True))

        launched = engine.launch(specs)
        self.assertEqual(launched, N)
        deadline = time.time() + 180
        while not ended and time.time() < deadline:
            _pump()
        self.assertTrue(ended, "并发 32 finished 应触发")
        self.assertEqual(len(results), N, "并发 32 应有 N 个 result")
        for i, r in results:
            self.assertEqual(r.status, SyncStatus.SUCCESS, f"仓库 {i} 应成功: {r.message}")
        data = json.loads(Path(pp).read_text(encoding="utf-8"))
        self.assertEqual(len(data["finished"]), N)
        db.close()
        engine.shutdown()

    def test_all_platform_errors_no_retry(self):
        """平台限制类错误（invalid path / file exists）不进入重试。"""
        from gcm.git.service import _classify_failure, _is_networkish_error
        self.assertFalse(_is_networkish_error("fatal: invalid path 'docs/x:y.html'"))
        self.assertFalse(_is_networkish_error("fatal: cannot copy ... File exists"))
        self.assertFalse(_is_networkish_error("error: unable to checkout working tree"))
        # 网络类仍应重试
        self.assertTrue(_is_networkish_error("fatal: unable to access ... Failed to connect"))
        # 分类给出明确提示
        msg, detail = _classify_failure("fatal: invalid path 'docs/research/layerfs:ai.html'")
        self.assertIn("Windows 不允许", msg)
        self.assertIn("WSL", detail)

    def test_db_busy_timeout_pragma(self):
        """SQLite 应设置 busy_timeout，避免高并发 database is locked。"""
        import sqlite3
        base = Path(tempfile.mkdtemp())
        db = Database(base / "t.db")
        cur = db._conn.cursor()
        cur.execute("PRAGMA busy_timeout")
        self.assertEqual(cur.fetchone()[0], 30000)
        db.close()

    def test_file_lock_concurrent_save(self):
        """并发写同一 progress.json 不抛 PermissionError，结构完整。"""
        import threading
        base = Path(tempfile.mkdtemp())
        pp = base / "p.json"
        save_progress(pp, {"finished": [], "in_progress": {}})
        errors = []

        def worker(i):
            try:
                save_progress(pp, {"finished": [{"key": f"k{i}"}], "in_progress": {}})
            except Exception as e:  # noqa
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [], f"并发写 progress 不应抛异常: {errors}")
        data = load_progress(pp)
        self.assertIn("finished", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)