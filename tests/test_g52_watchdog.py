"""G52-3 看门狗续跑测试（幂等 / 间隔收敛 / 清单驱动）。"""
import shutil
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from gcm.app.watchdog import _DISPATCH_TTL_S, Watchdog, clamp_interval
from gcm.db.runtime_sessions import RuntimeSessionStore
from gcm.models import RepoSpec


def _spec(owner: str = "o", repo: str = "r") -> RepoSpec:
    return RepoSpec(owner=owner, repo=repo,
                    url_https=f"https://github.com/{owner}/{repo}.git",
                    display=f"{owner}/{repo}", folder_name=f"{owner}__{repo}")


class TestWatchdogLogic(unittest.TestCase):
    def test_clamp_interval(self):
        self.assertEqual(clamp_interval(1), 1)
        self.assertEqual(clamp_interval(5), 5)
        self.assertEqual(clamp_interval(15), 15)
        self.assertEqual(clamp_interval(30), 30)
        self.assertEqual(clamp_interval(0), 5)
        self.assertEqual(clamp_interval(99), 5)
        self.assertEqual(clamp_interval("abc"), 5)

    def test_empty_store_no_resume(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "session_runtime.json"
            wd = Watchdog(path, interval_min=5, silent=True)
            self.assertFalse(wd.check())
            self.assertFalse(wd.is_active())

    def test_resumable_emits_once(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "session_runtime.json"
            store = RuntimeSessionStore(path)
            store.replace([_spec()])
            wd = Watchdog(path, interval_min=5, silent=True, store=store)
            got: list = []
            wd.resume_requested.connect(got.append)
            self.assertTrue(wd.check())
            self.assertEqual(len(got), 1)
            # 幂等：已派发未复位 → 不再发
            self.assertFalse(wd.check())
            wd.reset()
            self.assertTrue(wd.check())
            self.assertEqual(len(got), 2)

    def test_completed_store_no_resume(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "session_runtime.json"
            store = RuntimeSessionStore(path)
            store.replace([_spec()])
            store.mark_completed()
            wd = Watchdog(path, interval_min=5, silent=True, store=store)
            self.assertFalse(wd.check())

    def test_start_stop(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "session_runtime.json"
            wd = Watchdog(path, interval_min=5, silent=True)
            wd.start()
            self.assertTrue(wd.is_active())
            wd.stop()
            self.assertFalse(wd.is_active())


if __name__ == "__main__":
    unittest.main()


class TestWatchdogTtlRecovery(unittest.TestCase):
    """S-09：派发闸门超时必须自动复位 —— 一次派发失败不得永久杀死看门狗。"""

    def setUp(self):
        if QApplication.instance() is None:
            QApplication([])
        self.tmp = Path(tempfile.mkdtemp(prefix="wd_ttl_"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.store = RuntimeSessionStore(self.tmp / "s.json")
        self.store.replace([RepoSpec("o", "r", "https://github.com/o/r")])
        self.wd = Watchdog(self.tmp / "s.json", interval_min=5, store=self.store)
        self.addCleanup(self.wd._timer.stop)

    def test_ttl_auto_reset(self):
        self.assertTrue(self.wd.check())          # 派发一次，闸门置位
        self.assertFalse(self.wd.check())         # 未复位期间拒绝再派发
        # 把置位时刻拨回 TTL 之前 → 下一次 check 必须自动复位并重新派发
        self.wd._dispatched_at -= _DISPATCH_TTL_S + 1
        self.assertTrue(self.wd.check(),
                        "闸门超时后必须自动复位并恢复检测（旧行为是永久 False）")
        self.assertFalse(self.wd._dispatching is False and True)

    def test_reset_within_ttl_keeps_normal_flow(self):
        self.assertTrue(self.wd.check())
        self.wd.reset()
        self.assertFalse(self.wd._dispatching)
