# -*- coding: utf-8 -*-
"""G21-1/G22-1/G22-2 引擎与主窗修复 E2E。

- G21-1: 排序/过滤后暂停精确定位（行内 UserRole+1 存 engine index）
- G21-1: launch 后表格与 engine 去重索引对齐（无悬空行）
- G22-1: engine.drain 优雅收敛（取消 + 等待，非硬杀）
- G22-2: in_progress/failed 全生命周期跟踪 + 崩溃恢复预填
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

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gcm.app.engine import SyncEngine
from gcm.db.repo_db import Database, load_progress
from gcm.db.settings import SettingsStore
from gcm.models import RepoSpec, SyncStatus


def _pump():
    for _ in range(20):
        _app.processEvents()
        time.sleep(0.01)


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


def _spec(owner, repo, remote, ref=""):
    return RepoSpec(owner, repo, str(remote), folder_name=f"{owner}__{repo}", ref=ref)


class TestPauseAfterSort(unittest.TestCase):
    """G21-1：排序后暂停必须命中正确 index。"""

    def test_row_data_carries_engine_index(self):
        from gcm.ui.main_window import MainWindow
        d = Path(tempfile.mkdtemp())
        win = MainWindow(data_dir=d, db=Database(d / "t.db"),
                         settings=SettingsStore(d / "settings.json"))
        try:
            win._prepare_table(3)
            for i, name in enumerate(("c__z", "a__y", "b__x")):
                owner, repo = name.split("__")
                win._add_table_row(i, _spec(owner, repo, d))
            # 排序后行序会变，但每行携带的 engine index 不变
            win.table.sortItems(0)
            seen = set()
            for r in range(3):
                it = win.table.item(r, 0)
                idx = it.data(Qt.ItemDataRole.UserRole + 1)
                self.assertIsNotNone(idx, "每行必须携带 engine index")
                seen.add(int(idx))
            self.assertEqual(seen, {0, 1, 2})
        finally:
            win.close()

    def test_pause_selected_uses_stored_index(self):
        """模拟排序态：选中视觉第 0 行，暂停应发到其存储的 engine index。"""
        from gcm.ui.main_window import MainWindow
        d = Path(tempfile.mkdtemp())
        win = MainWindow(data_dir=d, db=Database(d / "t.db"),
                         settings=SettingsStore(d / "settings.json"))
        try:
            win._prepare_table(2)
            win._add_table_row(0, _spec("b", "two", d))   # engine index 0
            win._add_table_row(1, _spec("a", "one", d))   # engine index 1
            win.table.sortItems(0)  # 排序后 a__one 跑到视觉第 0 行
            sent = []
            engine = type("E", (), {"pause_task": staticmethod(
                lambda i: sent.append(i) or True)})()
            win.engine = engine
            win.table.selectAll()
            win.pause_selected()
            self.assertEqual(sorted(sent), [0, 1], "暂停应按存储 index 派发")
        finally:
            win.close()

    def test_launch_aligns_rows_with_deduped_specs(self):
        """重复地址去重后：表格行数 == engine 唯一清单，无悬空行。"""
        from gcm.ui.main_window import MainWindow
        base = Path(tempfile.mkdtemp())
        remote = _init_remote(base, "r0")
        d = base / "data"
        d.mkdir()
        win = MainWindow(data_dir=d, db=Database(d / "t.db"),
                         settings=SettingsStore(d / "settings.json"))
        try:
            win.engine = SyncEngine(d / "clones", db=None, progress_path=None,
                                    concurrency=2)
            dup = [_spec("o", "r", remote)] * 3
            win._launch(dup, target_root=base / "clones", shallow=False, depth=1)
            deadline = time.time() + 60
            while win.busy and time.time() < deadline:
                _pump()
            self.assertEqual(win.table.rowCount(), 1,
                             "去重后表格应只剩唯一仓库行")
            it = win.table.item(0, 0)
            self.assertEqual(it.data(Qt.ItemDataRole.UserRole + 1), 0)
        finally:
            try:
                win.engine.shutdown()
            except Exception:
                pass
            win.close()


class TestDrainGraceful(unittest.TestCase):
    """G22-1：drain = 取消 + 等待收敛，且全部任务达终态。"""

    def test_drain_waits_for_terminal_states(self):
        base = Path(tempfile.mkdtemp())
        remotes = {f"o{i}": _init_remote(base, f"r{i}") for i in range(4)}
        specs = [_spec(f"o{i}", f"r{i}", remotes[f"o{i}"]) for i in range(4)]
        d = base / "data"
        d.mkdir()
        pp = d / "progress.json"
        engine = SyncEngine(base / "clones", db=None, progress_path=pp, concurrency=2)
        ended = []
        engine.finished.connect(lambda: ended.append(True))
        engine.launch(specs)
        ok = engine.drain(60000)  # 真实等待：允许全部自然完成或被取消
        self.assertTrue(ok, "drain 应在时限内收敛")
        prog = load_progress(pp)
        keys_pending = list(prog.get("in_progress", {}).keys())
        self.assertEqual(keys_pending, [], "drain 后 in_progress 必须清空")
        engine.shutdown()

    def test_drain_after_cancel_all_cancelled(self):
        base = Path(tempfile.mkdtemp())
        remotes = {f"o{i}": _init_remote(base, f"r{i}") for i in range(3)}
        specs = [_spec(f"o{i}", f"r{i}", remotes[f"o{i}"]) for i in range(3)]
        d = base / "data"
        d.mkdir()
        engine = SyncEngine(base / "clones", db=None, progress_path=d / "progress.json",
                            concurrency=1)
        results = []
        engine.result.connect(lambda i, r: results.append(r))
        engine.launch(specs)
        engine.cancel_all()
        ok = engine.drain(60000)
        self.assertTrue(ok)
        self.assertTrue(all(r.status in (SyncStatus.SUCCESS, SyncStatus.CANCELLED)
                            for r in results))
        engine.shutdown()


class TestCrashRecoveryTracking(unittest.TestCase):
    """G22-2：in_progress/failed 全生命周期。"""

    def test_launch_writes_in_progress_then_clears(self):
        base = Path(tempfile.mkdtemp())
        remotes = {f"o{i}": _init_remote(base, f"r{i}") for i in range(2)}
        specs = [_spec(f"o{i}", f"r{i}", remotes[f"o{i}"]) for i in range(2)]
        d = base / "data"
        d.mkdir()
        pp = d / "progress.json"
        engine = SyncEngine(base / "clones", db=None, progress_path=pp, concurrency=2)
        ended = []
        engine.finished.connect(lambda: ended.append(True))
        engine.launch(specs)
        # launch 后立刻应有 in_progress 且已落盘
        mid = load_progress(pp)
        self.assertTrue(mid.get("in_progress"), "launch 后必须登记待完成清单")
        deadline = time.time() + 60
        while not ended and time.time() < deadline:
            _pump()
        prog = load_progress(pp)
        self.assertEqual(list(prog.get("in_progress", {}).keys()), [],
                         "全部完成后 in_progress 清空")
        self.assertEqual(len(prog.get("finished", [])), 2)
        engine.shutdown()

    def test_failed_recorded_in_failed_list(self):
        base = Path(tempfile.mkdtemp())
        bad = [_spec("no", "such", "https://127.0.0.1:1/o/r.git"),  # 连接拒绝
               _spec("ok", "repo", _init_remote(base, "good"))]
        d = base / "data"
        d.mkdir()
        pp = d / "progress.json"
        engine = SyncEngine(base / "clones", db=None, progress_path=pp,
                            concurrency=2, retries=0)
        ended = []
        engine.finished.connect(lambda: ended.append(True))
        engine.launch(bad)
        deadline = time.time() + 90
        while not ended and time.time() < deadline:
            _pump()
        prog = load_progress(pp)
        failed_keys = [x.get("key") for x in prog.get("failed", [])]
        self.assertIn("no/such", failed_keys, "失败项必须登记 failed")
        self.assertEqual(list(prog.get("in_progress", {}).keys()), [])
        engine.shutdown()

    def test_startup_recovery_prefill(self):
        """崩溃残留 → 启动自检回填输入区（in_progress 优先，failed 一并）。"""
        from gcm.ui.main_window import MainWindow
        d = Path(tempfile.mkdtemp())
        (d / "progress.json").write_text(json.dumps({
            "finished": [],
            "in_progress": {"crash/ed": "2026-09-15 10:00:00"},
            "failed": [{"key": "fail/ed", "message": "x", "time": "t"}],
        }), encoding="utf-8")
        win = MainWindow(data_dir=d, db=Database(d / "t.db"),
                         settings=SettingsStore(d / "settings.json"))
        try:
            text = win.repo_input.toPlainText()
            self.assertIn("crash/ed", text, "未完成项应回填")
            self.assertIn("fail/ed", text, "失败项应回填")
        finally:
            win.close()

    def test_shutdown_clears_in_progress(self):
        """主动优雅退出（shutdown）不应留下「未完成」误报。"""
        base = Path(tempfile.mkdtemp())
        d = base / "data"
        d.mkdir()
        pp = d / "progress.json"
        engine = SyncEngine(base / "clones", db=None, progress_path=pp, concurrency=1)
        engine.launch([_spec("o", "r", _init_remote(base, "rr"))])
        engine.shutdown()
        prog = load_progress(pp)
        self.assertEqual(list(prog.get("in_progress", {}).keys()), [],
                         "shutdown 应清空待完成清单")
        self.assertEqual(list(prog.get("failed", [])), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
