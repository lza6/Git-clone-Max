"""G52-3 看门狗续跑测试（幂等 / 间隔收敛 / 清单驱动）。"""
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from gcm.app.watchdog import Watchdog, clamp_interval
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
