"""A5-5/R3 守卫：per-host 并发闸门（C-4）。"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
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
