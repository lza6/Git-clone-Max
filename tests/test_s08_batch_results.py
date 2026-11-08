"""S-08：会话结果批量提交（Rev10 W2 P1）。

原行为：每仓一次 `update_status` = 一次整文件重写 + 一次跨进程锁，
实测 ≈16ms/次，全部落在 GUI 线程；500 仓批次累计 ≈8s 纯阻塞。
修法：engine 侧帧缓冲（`_SESSION_FLUSH_N` 满即刷）+ 批次收尾强制刷 +
`RuntimeSessionStore.update_results` 批量入口（一次 `_rmw`）。DB 单写者
语义不变（SQLite 写仍在引擎主线程串行，改的只是 JSON 会话清单）。
"""
import tempfile
import time
import unittest
from pathlib import Path

from gcm.db.runtime_sessions import RuntimeSessionStore
from gcm.models import RepoSpec


class TestBatchUpdateResults(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="s08_")
        self.addCleanup(self._tmp.cleanup)
        self.store = RuntimeSessionStore(Path(self._tmp.name) / "s.json")
        self.store.replace(
            [RepoSpec("o", f"r{i}", f"https://github.com/o/r{i}")
             for i in range(50)])

    def _keys(self):
        return [t["key"] for t in self.store.load()["tasks"]]

    def test_batch_semantics_match_per_item(self):
        """批量与逐条的终态语义一致：失败保留、成功移除。"""
        keys = self._keys()
        self.store.update_results(
            [(keys[0], "failed", "boom"),
             (keys[1], "success", ""),
             (keys[2], "cancelled", "")])
        remaining = {t["key"]: t for t in self.store.load()["tasks"]}
        self.assertEqual(len(remaining), 48)
        self.assertIn(keys[0], remaining)
        self.assertEqual(remaining[keys[0]]["status"], "failed")
        self.assertNotIn(keys[1], remaining)
        self.assertNotIn(keys[2], remaining)

    def test_batch_redacts_message(self):
        keys = self._keys()
        self.store.update_results([(keys[0], "failed",
                                    "https://u:secret@github.com/o/r")])
        t = next(t for t in self.store.load()["tasks"] if t["key"] == keys[0])
        self.assertNotIn("secret", t.get("message", ""))

    def test_batch_is_faster_and_correct_under_50(self):
        """性能契约：50 条批量必须显著快于逐条（且语义一致）。"""
        keys = self._keys()
        t0 = time.perf_counter()
        self.store.update_results([(k, "failed", "x") for k in keys])
        batch_ms = (time.perf_counter() - t0) * 1000
        self.assertEqual(len(self.store.load()["tasks"]), 50)
        # 逐条基线实测 ≈15.8ms/次 = 790ms；批量必须至少快 10 倍
        self.assertLess(batch_ms, 79.0,
                        f"批量提交 {batch_ms:.1f}ms 未达 10x 契约（逐条 ≈790ms）")

    def test_empty_batch_noop(self):
        self.store.update_results([])
        self.assertEqual(len(self.store.load()["tasks"]), 50)


if __name__ == "__main__":
    unittest.main()


class TestFlushFrameRestore(unittest.TestCase):
    """审查 P2 整改：flush 提交失败必须把帧放回缓冲（不丢条目）。"""

    def test_engine_flush_restores_frame_on_failure(self):
        import tempfile as _tf
        from pathlib import Path as _P
        from unittest.mock import patch

        from gcm.app.engine import SyncEngine
        store = RuntimeSessionStore(_P(_tf.mkdtemp(prefix="s08f_")) / "s.json")
        e = SyncEngine.__new__(SyncEngine)
        e.session_store = store
        e._session_frame = [("a", "failed", "m")]
        with patch.object(store, "update_results",
                          side_effect=RuntimeError("disk full")):
            e._session_flush()
        self.assertEqual(len(e._session_frame), 1,
                         "提交失败后帧必须还原（否则条目静默消失）")
        self.assertEqual(e._session_frame[0][0], "a")

    def test_engine_flush_clears_on_success(self):
        import tempfile as _tf
        from pathlib import Path as _P

        from gcm.app.engine import SyncEngine
        store = RuntimeSessionStore(_P(_tf.mkdtemp(prefix="s08s_")) / "s.json")
        store.replace([RepoSpec("o", "r", "https://github.com/o/r")])
        e = SyncEngine.__new__(SyncEngine)
        e.session_store = store
        e._session_frame = [(store.load()["tasks"][0]["key"], "failed", "m")]
        e._session_flush()
        self.assertEqual(e._session_frame, [], "成功提交后缓冲清空")
