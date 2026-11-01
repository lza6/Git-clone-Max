"""A5-5/R3 守卫：per-host 并发闸门（C-4）。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
import unittest.mock as mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.engine import SyncEngine
from gcm.models import RepoSpec


def _spec(owner, repo, host="github.com"):
    return RepoSpec(owner=owner, repo=repo,
                    url_https=f"https://{host}/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


class TestHostGate(unittest.TestCase):
    def _engine(self, **kw):
        return SyncEngine(Path(tempfile.mkdtemp()) / "clones",
                          concurrency=8, **kw)

    def test_same_host_gate_created_once(self):
        e = self._engine()
        try:
            g1 = e._host_gate("github.com")
            g2 = e._host_gate("github.com")
            self.assertIs(g1, g2, "同 host 必须共享同一闸门")
            self.assertIsNot(e._host_gate("gitlab.com"), g1, "不同 host 各自独立")
        finally:
            e.shutdown()

    def test_disabled_gate_returns_none(self):
        e = self._engine()
        try:
            e.set_host_concurrency(0)
            self.assertIsNone(e._host_gate("github.com"),
                              "host_concurrency=0 应视为不限流")
        finally:
            e.shutdown()

    def test_gate_limits_concurrent_holders(self):
        """真实并发验证：闸门容量 2，同 host 6 个 acquire 尝试 → 同时持票 ≤2。"""
        e = self._engine()
        try:
            e.set_host_concurrency(2)
            gate = e._host_gate("github.com")
            active = []
            peak = [0]
            lock = threading.Lock()

            def worker():
                gate.acquire()
                try:
                    with lock:
                        active.append(1)
                        peak[0] = max(peak[0], len(active))
                    time.sleep(0.05)
                finally:
                    with lock:
                        active.pop()
                    gate.release()

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=10)
            self.assertEqual(peak[0], 2, f"同 host 同时持票必须 ≤2，实测 {peak[0]}")
        finally:
            e.shutdown()

    def test_worker_receives_gate(self):
        """_start_worker 必须把闸门传给 worker（接线守卫，防参数丢失）。"""
        e = self._engine()
        try:
            real = e._host_gate("github.com")
            with unittest.mock.patch(
                    "gcm.app.engine.CloneWorker") as m_cw:
                spec = _spec("a", "b")
                e._start_worker(0, spec, lambda: False)
            kwargs = m_cw.call_args.kwargs
            self.assertIs(kwargs.get("host_gate"), real,
                          "worker 必须接到对应 host 的闸门实例")
        finally:
            e.shutdown()


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestGateLeakThroughWorkerRun(unittest.TestCase):
    """G82-hotfix4 回归守卫：worker.run 全路径票必须回收（_gate_held 缺失曾致死锁）。"""

    def test_gate_value_restored_after_worker_run(self):
        from gcm.app.worker import CloneWorker
        from gcm.app.engine import SyncEngine  # noqa: F401 确保模块加载
        e = SyncEngine(Path(tempfile.mkdtemp()) / "clones", concurrency=2)
        try:
            e.set_host_concurrency(1)
            gate = e._host_gate("github.com")
            from types import SimpleNamespace
            spec = _spec("a", "b")
            res_stub = SimpleNamespace(status=SimpleNamespace(value="failed"),
                                       spec=spec, message="", detail="",
                                       action=SimpleNamespace(value="failed"),
                                       path="", mirror_used="", degrade_path="")
            worker = CloneWorker.__new__(CloneWorker)
            # 绕过 Qt __init__：直接装配 run() 需要的最小属性（SimpleNamespace 免 Mock 陷阱）
            worker.index = 0
            worker.payload = SimpleNamespace(spec=spec, flag=lambda: False)
            svc = SimpleNamespace()
            def _boom(*a, **k):
                raise RuntimeError("boom")
            svc.sync = _boom
            worker.service = svc
            worker.db = None
            worker.progress_path = None
            worker.on_line = None
            worker.fetch_depth = 0
            worker.unshallow = False
            worker.engine = None
            worker._is_update = False
            worker._external_line = None
            worker._host_gate = gate
            worker.signals = SimpleNamespace(
                line=SimpleNamespace(emit=lambda *a: None),
                progress=SimpleNamespace(emit=lambda *a: None),
                progress_detail=SimpleNamespace(emit=lambda *a: None),
                result=SimpleNamespace(emit=lambda *a: None),
                finished=SimpleNamespace(emit=lambda *a: None))
            try:
                worker.run()
            except Exception:
                pass
            self.assertEqual(gate._value, 1,
                             "worker.run 异常路径后票必须归还（泄漏=死锁）")
        finally:
            e.shutdown()

    def test_gate_value_restored_success_path(self):
        """正常路径（sync 成功）也必须归还票。"""
        from types import SimpleNamespace
        from gcm.app.worker import CloneWorker
        e = SyncEngine(Path(tempfile.mkdtemp()) / "clones", concurrency=2)
        try:
            e.set_host_concurrency(1)
            gate = e._host_gate("github.com")
            spec = _spec("a", "b")
            worker = CloneWorker.__new__(CloneWorker)
            worker.index = 0
            worker.payload = SimpleNamespace(spec=spec, flag=lambda: False)
            res_stub = SimpleNamespace(status=SimpleNamespace(value="success"),
                                       spec=spec, message="", detail="",
                                       action=SimpleNamespace(value="cloned"),
                                       path="", mirror_used="", degrade_path="")
            worker.service = SimpleNamespace(sync=lambda *a, **k: res_stub)
            worker.db = None
            worker.progress_path = None
            worker.on_line = None
            worker.fetch_depth = 0
            worker.unshallow = False
            worker.engine = None
            worker._is_update = False
            worker._external_line = None
            worker._host_gate = gate
            worker.signals = SimpleNamespace(
                line=SimpleNamespace(emit=lambda *a: None),
                progress=SimpleNamespace(emit=lambda *a: None),
                progress_detail=SimpleNamespace(emit=lambda *a: None),
                result=SimpleNamespace(emit=lambda *a: None),
                finished=SimpleNamespace(emit=lambda *a: None))
            worker.run()
            self.assertEqual(gate._value, 1,
                             "worker.run 正常路径后票必须归还")
        finally:
            e.shutdown()
