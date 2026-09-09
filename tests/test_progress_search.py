# -*- coding: utf-8 -*-
"""G02-1/2 进度表搜索/排序 + G02-3 单仓库暂停 测试（对应指南 M3 里程碑）。

- 搜索框：按仓库名过滤进度表行
- 表头排序：QTableWidget 开启排序，状态列优先级排序
- 单仓库暂停：engine.pause_task 只取消目标 worker，其余继续
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

from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication([])

from gcm.app.engine import SyncEngine
from gcm.db.repo_db import Database
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


def _spec(owner, repo, remote):
    return RepoSpec(owner, repo, str(remote), folder_name=f"{owner}__{repo}")


class TestTableFilter(unittest.TestCase):
    def test_filter_rows(self):
        from gcm.ui.main_window import MainWindow
        from gcm.db.settings import SettingsStore
        d = Path(tempfile.mkdtemp())
        win = MainWindow(data_dir=d, db=Database(d / "t.db"),
                         settings=SettingsStore(d / "settings.json"))
        win._prepare_table(3)
        win._add_table_row(0, _spec("aa", "repo1", d))
        win._add_table_row(1, _spec("bb", "repo2", d))
        win._add_table_row(2, _spec("cc", "repo3", d))
        # 过滤函数：返回可见行数
        n = win._filter_progress_rows("repo2")
        self.assertEqual(n, 1)
        n_all = win._filter_progress_rows("")
        self.assertEqual(n_all, 3)
        win.close()


class TestSinglePause(unittest.TestCase):
    def test_pause_task_cancels_one(self):
        base = Path(tempfile.mkdtemp())
        remotes = {f"o{i}": _init_remote(base, f"r{i}") for i in range(3)}
        specs = [_spec(f"o{i}", f"r{i}", remotes[f"o{i}"]) for i in range(3)]
        d = base / "data"
        d.mkdir()
        db = Database(d / "t.db")
        pp = d / "progress.json"
        engine = SyncEngine(base / "clones", db=db, progress_path=pp, concurrency=3)
        results = []
        ended = []
        engine.result.connect(lambda i, r: results.append(r))
        engine.finished.connect(lambda: ended.append(True))
        engine.launch(specs)
        # 立即暂停第 0 个任务（取消其 flag），其余继续
        engine.pause_task(0)
        deadline = time.time() + 90
        while not ended and time.time() < deadline:
            _pump()
        # 至少有一个任务被取消；其余应成功
        statuses = [r.status for r in results]
        self.assertIn(SyncStatus.CANCELLED, statuses, "暂停的任务应标记 CANCELLED")
        self.assertTrue(any(s == SyncStatus.SUCCESS for s in statuses),
                        "未暂停的任务应成功")
        db.close()
        engine.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)
