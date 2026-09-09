# -*- coding: utf-8 -*-
"""进度条列实时速率/对象数显示测试：parse_progress_meta 单测 + UI 槽 + 真实 git E2E。"""
from __future__ import annotations

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

from PyQt6.QtWidgets import QApplication, QProgressBar

_app = QApplication.instance() or QApplication([])

from gcm.git.service import GitService, parse_progress_meta
from gcm.models import RepoSpec


class TestParseProgressMeta(unittest.TestCase):
    def test_receiving_with_rate(self):
        p, r, f = parse_progress_meta(
            "Receiving objects:  25% (1781/7124), 732.00 KiB | 1.28 MiB/s")
        self.assertEqual(p, "25")
        self.assertEqual(r, "1.28 MiB/s")
        self.assertEqual(f, "7124")

    def test_receiving_complete(self):
        p, r, f = parse_progress_meta(
            "Receiving objects: 100% (7124/7124), 7.41 MiB | 7.03 MiB/s")
        self.assertEqual(p, "100")
        self.assertEqual(r, "7.03 MiB/s")
        self.assertEqual(f, "7124")

    def test_updating_files(self):
        p, r, f = parse_progress_meta("Updating files: 45% (2817/6260)")
        self.assertEqual(p, "45")
        self.assertIsNone(r)
        self.assertEqual(f, "6260")

    def test_resolving_deltas(self):
        p, r, f = parse_progress_meta("Resolving deltas: 12% (73/590)")
        self.assertEqual(p, "12")
        self.assertIsNone(r)
        self.assertEqual(f, "590")

    def test_pure_percent_line(self):
        p, r, f = parse_progress_meta("Total 100%")
        self.assertEqual(p, "100")
        self.assertIsNone(r)
        self.assertIsNone(f)

    def test_garbage_line(self):
        self.assertEqual(parse_progress_meta("not a progress line"), (None, None, None))

    def test_single_digit_rate(self):
        p, r, f = parse_progress_meta("Receiving objects: 50% (3/6), 5.00 KiB | 1 MiB/s")
        self.assertEqual(r, "1 MiB/s")
        self.assertEqual(f, "6")

    def test_kb_unit(self):
        p, r, f = parse_progress_meta("Receiving objects: 10% (1/10), 512.00 KiB | 256 KB/s")
        self.assertEqual(r, "256 KB/s")


class TestProgressDetailSlot(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        cls._d = Path(tempfile.mkdtemp())
        db = Database(cls._d / "t.db")
        ss = SettingsStore(cls._d / "settings.json")
        cls._w = MainWindow(data_dir=cls._d, db=db, settings=ss)

    @classmethod
    def tearDownClass(cls):
        cls._w.close()

    def test_progress_detail_updates_bar_format(self):
        spec = RepoSpec("owner", "repo", "https://github.com/owner/repo.git")
        self._w._prepare_table(1)
        self._w._add_table_row(0, spec)
        # 节流批量刷新：调用槽后需手动 flush（模拟定时器到期）
        self._w._on_worker_progress_detail(0, "7.03 MiB/s 7124")
        self._w._flush_detail_batch()
        bar = self._w.table.cellWidget(0, 1)
        self.assertIsInstance(bar, QProgressBar)
        self.assertIn("7.03 MiB/s", bar.format())

    def test_progress_detail_ignores_bad_index(self):
        # 越界 index 不应抛异常
        self._w._on_worker_progress_detail(99, "1 MiB/s")
        self._w._on_worker_progress_detail(-1, "1 MiB/s")

    def test_progress_detail_initial_format_percent(self):
        spec = RepoSpec("owner", "repo", "https://github.com/owner/repo.git")
        self._w._prepare_table(1)
        self._w._add_table_row(0, spec)
        bar = self._w.table.cellWidget(0, 1)
        self.assertIsInstance(bar, QProgressBar)
        self.assertIn("%p%", bar.format())


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


def _pump(app, n=20):
    for _ in range(n):
        app.processEvents()
        time.sleep(0.01)


class TestProgressDetailE2E(unittest.TestCase):
    """真实 git E2E：引擎路径下 progress_detail 信号应至少被 emit 一次。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        remote = self.tmp / "remote.git"
        work = self.tmp / "work"
        subprocess.run(["git", "init", "--bare", "-q", str(remote)], check=True)
        subprocess.run(["git", "-C", str(remote), "symbolic-ref", "HEAD", "refs/heads/main"],
                       check=True, capture_output=True)
        work.mkdir(parents=True, exist_ok=True)
        _git(work, "init", "-q")
        _git(work, "config", "user.email", "t@t.com")
        _git(work, "config", "user.name", "t")
        for i in range(3):
            (work / f"f{i}.txt").write_text(f"data {i}", encoding="utf-8")
        _git(work, "add", ".")
        _git(work, "commit", "-m", "v1")
        _git(work, "remote", "add", "origin", str(remote))
        _git(work, "push", "-q", "origin", "HEAD:main")
        self.remote = remote

    def test_engine_emits_progress_detail_during_clone(self):
        from gcm.app.engine import SyncEngine
        from gcm.db.repo_db import Database
        # file:// URI 强制 git 走远程传输路径（raw path 走本地硬链接优化，
        # 不产生 Receiving objects 进度行 → 无法驱动速率/对象数解析）
        spec = RepoSpec("o", "r", self.remote.as_uri(), folder_name="o__r")
        d = self.tmp / "data"
        d.mkdir()
        db = Database(d / "t.db")
        engine = SyncEngine(self.tmp / "clones", db=db, progress_path=d / "progress.json",
                            concurrency=1)
        seen = []
        ended = []
        engine.progress_detail.connect(lambda i, t: seen.append(t))
        engine.finished.connect(lambda: ended.append(True))
        launched = engine.launch([spec])
        self.assertEqual(launched, 1)
        deadline = time.time() + 120
        while not ended and time.time() < deadline:
            _pump(_app)
        self.assertTrue(ended, "finished 信号应在克隆完成后触发")
        self.assertTrue(seen, "克隆过程中应至少 emit 一次 progress_detail")
        db.close()
        engine.shutdown()


if __name__ == "__main__":
    unittest.main()
