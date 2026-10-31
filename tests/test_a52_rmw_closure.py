"""A5-2/R4 守卫：跨进程读改写收口 + key 唯一性 + 会话 merge 语义。

- C-5：RetryQueue / RuntimeSessionStore 的读-改-写在同一跨进程临界区
  （源码级守卫：写路径必须经 _rmw，不得 load() 与 _save() 分离取锁）
- C-17：同仓不同 @tag 不再互相覆盖
- C-16：_mark_done 用 owner/repo，与 _precheck_skip 匹配键一致
- C-18：重投路径 session_merge=True 不抹掉未投的失败项
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.engine import SyncEngine
from gcm.app.retry_queue import RetryQueue
from gcm.db.runtime_sessions import RuntimeSessionStore
from gcm.models import RepoSpec, SyncStatus


def _spec(owner="o", repo="r", ref="") -> RepoSpec:
    return RepoSpec(owner=owner, repo=repo,
                    url_https=f"https://github.com/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}", ref=ref)


class TestRetryKeyUniqueness(unittest.TestCase):
    def test_same_repo_different_refs_not_overwritten(self):
        """C-17：x/y@v1 与 x/y@v2 都失败 → 两条独立条目，attempts 各自累计。"""
        q = RetryQueue(path=None, enabled=True, max_retries=3,
                       jitter=lambda d: d)
        self.assertTrue(q.record_failure(_spec(ref="v1"), "Connection timed out"))
        self.assertTrue(q.record_failure(_spec(ref="v2"), "Connection timed out"))
        self.assertEqual(q.count(), 2, "不同 ref 必须是两条独立条目")
        # 再次失败同 ref → attempts 叠加在同一条上
        q.record_failure(_spec(ref="v1"), "Connection timed out")
        entries = {r["key"]: r for r in q.list_entries()}
        self.assertEqual(entries["o/r@v1"]["attempts"], 2)
        self.assertEqual(entries["o/r@v2"]["attempts"], 1)

    def test_no_ref_keeps_legacy_key(self):
        """无 ref 保持 owner/repo 形态（向后兼容旧 JSON 条目）。"""
        q = RetryQueue(path=None, enabled=True)
        q.record_failure(_spec(), "Connection timed out")
        self.assertEqual(q.count(), 1)
        self.assertIn("o/r", [r["key"] for r in q.list_entries()])

    def test_to_spec_strips_ref(self):
        q = RetryQueue(path=None, enabled=True)
        q.record_failure(_spec(ref="v9"), "Connection timed out")
        rec = q.list_entries()[0]
        spec = q.to_spec(rec)
        self.assertEqual(spec.owner, "o")
        self.assertEqual(spec.repo, "r")
        self.assertEqual(spec.ref, "v9")


class TestSessionMerge(unittest.TestCase):
    def test_merge_keeps_existing_failures(self):
        """C-18：merge=True 合入新 key，不抹掉已有失败项。"""
        tmp = Path(tempfile.mkdtemp())
        store = RuntimeSessionStore(tmp / "s.json")
        try:
            batch1 = [_spec("a", f"r{i}") for i in range(3)]
            store.replace(batch1)
            store.mark_failed("a/r0", "boom")
            # 重投只含 2 条（模拟 due_tasks(limit) 截断）
            store.replace([_spec("a", "r0"), _spec("a", "r1")], merge=True)
            keys = {t["key"]: t["status"] for t in store.load()["tasks"]}
            self.assertEqual(set(keys), {"a/r0", "a/r1", "a/r2"},
                             "merge 不得抹掉 r2")
            self.assertEqual(keys["a/r0"], "failed", "已存在的失败状态不被重置")
        finally:
            try:
                tmp.joinpath("s.json").unlink()
            except OSError:
                pass

    def test_replace_still_replaces(self):
        tmp = Path(tempfile.mkdtemp())
        store = RuntimeSessionStore(tmp / "s.json")
        try:
            store.replace([_spec("a", "r0"), _spec("a", "r1")])
            store.replace([_spec("b", "r9")])
            keys = {t["key"] for t in store.load()["tasks"]}
            self.assertEqual(keys, {"b/r9"}, "默认（merge=False）保持整体替换语义")
        finally:
            try:
                tmp.joinpath("s.json").unlink()
            except OSError:
                pass


class TestMarkDoneKeyConsistency(unittest.TestCase):
    def test_precheck_mark_done_matches_lookup_key(self):
        """C-16：预检跳过的 finished 记录 key 必须是 owner/repo
        （否则下次同仓预检永不命中，每次都重新同步）。"""
        root = Path(tempfile.mkdtemp())
        e = SyncEngine(root / "clones", concurrency=1,
                       progress_path=root / "progress.json")
        try:
            e._skip_done = True
            spec = _spec()
            # _precheck_skip 还要求「目标目录存在且有 .git」——造一个满足条件的
            target = Path(spec.local_path or e.root / spec.folder_name)
            target.mkdir(parents=True, exist_ok=True)
            (target / ".git").mkdir(exist_ok=True)
            e._mark_done(f"{spec.owner}/{spec.repo}", SyncStatus.SUCCESS, "x")
            hit = e._precheck_skip(spec)
            self.assertIsNotNone(hit, "同 key 预检必须命中")
        finally:
            e.shutdown()


class TestRmwSingleCritRegion(unittest.TestCase):
    """源码级守卫：写路径必须经 _rmw（防止后人回退到分离加锁）。"""

    def test_retry_queue_write_paths_use_rmw(self):
        src = Path("gcm/app/retry_queue.py").read_text(encoding="utf-8")
        # record_failure_ex / mark_success / remove / retry_now / retry_all_now /
        # clear / requeue_dropped 都应经 _rmw
        self.assertGreaterEqual(src.count("self._rmw("), 7,
                                "写路径方法应全部经 _rmw 收口")
        # 不允许再出现「with self._lock: data = self.load() ... self._save」的分离模式
        self.assertNotIn("data = self.load()", src.split("def _rmw")[0],
                         "load() 不应在 _rmw 之外的写路径被调用")

    def test_runtime_sessions_write_paths_use_rmw(self):
        src = Path("gcm/db/runtime_sessions.py").read_text(encoding="utf-8")
        self.assertIn("def _rmw", src, "RuntimeSessionStore 必须有 _rmw 收口")
        self.assertIn("merge: bool = False", src, "replace 必须支持 merge 语义")


if __name__ == "__main__":
    unittest.main(verbosity=2)
