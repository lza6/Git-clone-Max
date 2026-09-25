"""G52 引擎集成测试：自动重试 / 续跑清单 / 进度 GC / 并发记忆（真实本地裸仓 E2E）。"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from gcm.app.engine import SyncEngine
from gcm.db.runtime_sessions import RuntimeSessionStore
from gcm.db import progress_gc
from gcm.models import RepoSpec, SyncResult, SyncStatus


def _make_bare_repo(td: Path, name: str) -> Path:
    """创建本地裸仓并提交一个初始 commit（真实 git）。"""
    src = td / "src"
    src.mkdir()
    (src / "f.txt").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.email", "t@e.com"], cwd=src, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=src, check=True)
    subprocess.run(["git", "add", "."], cwd=src, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=src, check=True)
    bare = td / name
    subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
    return bare


def _wait_idle(eng, timeout: float = 60.0) -> bool:
    """泵事件直到 engine 空闲（busy=False）；超过 timeout 秒返回 False。

    比 QEventLoop+finished 更稳：不受前序测试残留信号影响。
    """
    import time
    app = QApplication.instance()
    deadline = time.time() + timeout
    while eng.busy and time.time() < deadline:
        if app is not None:
            app.processEvents()
        time.sleep(0.05)
    return not eng.busy

def _spec_from_bare(bare: Path) -> RepoSpec:
    return RepoSpec(owner=bare.parent.name, repo=bare.stem,
                    url_https=str(bare), folder_name=bare.stem)


class TestEngineRetryQueue(unittest.TestCase):
    def test_network_failure_queued_and_success_clears(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            retry_path = td / "retry_queue.json"
            eng = SyncEngine(root=td / "out", progress_path=None,
                             retry_queue_path=retry_path, retry_enabled=True,
                             retry_max=2, retry_backoff_base_sec=30)
            spec = RepoSpec(owner="o", repo="r",
                            url_https="https://github.com/o/r.git",
                            folder_name="o__r")
            res = SyncResult(spec=spec, status=SyncStatus.FAILED,
                             message="Connection timed out", detail="",
                             path="", commits=0, head_sha="", remote_sha="",
                             started=1.0, ended=2.0)
            eng._on_result(0, res)
            self.assertEqual(eng._retry_queue.count(), 1)
            # 成功移除
            res2 = SyncResult(spec=spec, status=SyncStatus.SUCCESS,
                              path=str(td / "o__r"), message="ok", detail="",
                              commits=0, head_sha="", remote_sha="",
                              started=1.0, ended=2.0)
            eng._on_result(0, res2)
            self.assertEqual(eng._retry_queue.count(), 0)

    def test_non_network_failure_not_queued(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            eng = SyncEngine(root=td / "out", progress_path=None,
                             retry_queue_path=td / "retry_queue.json",
                             retry_enabled=True)
            spec = RepoSpec(owner="o", repo="r",
                            url_https="https://github.com/o/r.git",
                            folder_name="o__r")
            res = SyncResult(spec=spec, status=SyncStatus.FAILED,
                             message="Repository not found", detail="",
                             path="", commits=0, head_sha="", remote_sha="",
                             started=1.0, ended=2.0)
            eng._on_result(0, res)
            self.assertEqual(eng._retry_queue.count(), 0)


class TestEngineSessionStore(unittest.TestCase):
    def test_launch_records_pending_and_completed_marks_ok(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            bare = _make_bare_repo(td, "r0.git")
            store_path = td / "session_runtime.json"
            store = RuntimeSessionStore(store_path)
            eng = SyncEngine(root=td / "out", progress_path=None,
                             session_store=store)
            spec = _spec_from_bare(bare)
            n = eng.launch([spec], check_existing=False)
            self.assertEqual(n, 1)
            self.assertTrue(store.is_resumable())
            # 等待 E2E 完成
            self.assertTrue(_wait_idle(eng), "engine 未在超时内空闲")
            self.assertFalse(store.is_resumable())  # 正常完成 → finished_ok


class TestEngineProgressGc(unittest.TestCase):
    def test_gc_runs_at_batch_end(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prog_path = td / "progress.json"
            prog_path.write_text(json.dumps({
                "finished": [{"key": f"o/r{i}", "status": "success",
                              "message": "ok", "time": "2026-09-25 00:00:00"}
                             for i in range(300)],
                "in_progress": {}, "failed": []}), encoding="utf-8")
            bare = _make_bare_repo(td, "r0.git")
            eng = SyncEngine(root=td / "out", progress_path=prog_path,
                             progress_gc=progress_gc)
            spec = _spec_from_bare(bare)
            n = eng.launch([spec], check_existing=False)
            self.assertEqual(n, 1)
            self.assertTrue(_wait_idle(eng), "engine 未在超时内空闲")
            eng.shutdown()
            data = json.loads(prog_path.read_text(encoding="utf-8"))
            self.assertLessEqual(len(data["finished"]), 200)


class TestEngineConcurrencyMemory(unittest.TestCase):
    def test_restores_concurrency_bounded(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            state = td / "concurrency_state.json"
            state.write_text(json.dumps({"concurrency": 16, "fail_rate": 0.1}),
                             encoding="utf-8")
            eng = SyncEngine(root=td / "out", progress_path=None,
                             concurrency=8, concurrency_state_path=state)
            self.assertEqual(eng.concurrency, 8)  # 受设置上限约束
            self.assertEqual(eng._adapt_current, 8)

    def test_persist_after_batch(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            state = td / "concurrency_state.json"
            bare = _make_bare_repo(td, "r0.git")
            eng = SyncEngine(root=td / "out", progress_path=None,
                             concurrency=4, concurrency_state_path=state)
            spec = _spec_from_bare(bare)
            eng.launch([spec], check_existing=False)
            self.assertTrue(_wait_idle(eng), "engine 未在超时内空闲")
            self.assertTrue(state.exists())
            eng.shutdown()
            data = json.loads(state.read_text(encoding="utf-8"))
            self.assertEqual(data["concurrency"], 4)


if __name__ == "__main__":
    unittest.main()
