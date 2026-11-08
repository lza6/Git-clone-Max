"""R-01：prune 三态语义矩阵（Rev10 W4，T3 高风险守卫）。

3 模式 × 3 类仓库 = 9 例 + 危险项反向自检。
三重护栏逐条断言：protected 永不删 / oplog 留痕 undoable=0 / 计数正确。
"""
import tempfile
import unittest
from pathlib import Path

from gcm.app.prune import collect_prune_items, execute_prune, is_remote_gone
from gcm.db.repo_db import Database
from gcm.models import RepoSpec, SyncAction, SyncStatus
from gcm.util.oplog import OpLog


def _mk(tmp: Path) -> Database:
    return Database(tmp / "repos.db")


class _World(unittest.TestCase):
    """造三类仓库：gone（远端已删）/ gone_protected（远端已删+excluded）/ alive。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="r01_")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.db = _mk(self.tmp)
        self.addCleanup(self.db.close)
        self.oplog = OpLog(self.tmp / "oplog.json")
        self.dirs = {}
        for name, gone, protected in (("gone", True, False),
                                      ("gonep", True, True),
                                      ("alive", False, False)):
            d = self.tmp / name
            d.mkdir()
            (d / ".git").mkdir()
            self.dirs[f"o__{name}"] = d
            rid = self.db.upsert_repo(
                RepoSpec("o", name, f"https://github.com/o/{name}"),
                local_path=str(d))
            if name == "alive":
                self.db.add_sync_history(rid, SyncStatus.SUCCESS,
                                         SyncAction.CLONED, "ok", "")
            else:
                self.db.add_sync_history(
                    rid, SyncStatus.FAILED, SyncAction.UPDATED,
                    "remote: Repository not found.", "")
            if protected:
                self.db.set_excluded(rid, True)

    def _items(self):
        return collect_prune_items(self.db)

    def _by_name(self, items):
        return {it.folder_name: it for it in items}


class TestSemanticsMatrix(_World):
    def test_matrix_no_clean(self):
        counts = execute_prune(self.db, self._items(), "no-clean",
                               oplog=self.oplog)
        self.assertEqual(counts["reported"], 1)       # 未保护的 gone 只报告
        self.assertEqual(counts["pruned"], 0)
        self.assertEqual(counts["skipped_protected"], 1,
                         "protected+gone 在 no-clean 下也算被跳过（护栏优先于模式）")
        self.assertEqual(self.db.count(), 3, "no-clean 不删任何 DB 行")
        self.assertTrue(self.dirs["o__gone"].exists(), "不删目录")

    def test_matrix_prune(self):
        counts = execute_prune(self.db, self._items(), "prune",
                               oplog=self.oplog)
        self.assertEqual(counts["pruned"], 1)
        self.assertEqual(counts["skipped_protected"], 1)
        self.assertEqual(counts["dir_removed"], 0, "prune 不删本地目录")
        self.assertEqual(self.db.count(), 2, "只删 gone 那行")
        self.assertIsNone(self.db.get_repo("o", "gone"))
        self.assertIsNotNone(self.db.get_repo("o", "gonep"), "protected 不删")
        self.assertTrue(self.dirs["o__gone"].exists())

    def test_matrix_prune_untouched(self):
        counts = execute_prune(self.db, self._items(), "prune-untouched",
                               oplog=self.oplog)
        self.assertEqual(counts["pruned"], 1)
        self.assertEqual(counts["dir_removed"], 1)
        self.assertEqual(counts["skipped_protected"], 1)
        self.assertEqual(self.db.count(), 2)
        self.assertFalse(self.dirs["o__gone"].exists(), "目录已删")
        self.assertTrue(self.dirs["o__gonep"].exists(),
                        "protected 的目录必须原样保留（最危险护栏）")


class TestGuards(_World):
    def test_oplog_records_undable_false(self):
        execute_prune(self.db, self._items(), "prune", oplog=self.oplog)
        recs = self.oplog.recent(10)
        prune_recs = [r for r in recs if r.action == "prune"]
        self.assertEqual(len(prune_recs), 1)
        self.assertFalse(prune_recs[0].undoable, "prune 必须 undoable=0")

    def test_local_modified_repo_not_deleted(self):
        """最危险场景反向自检：本地有改动（excluded=protected）+ 远端 gone
        → prune-untouched 不得删其目录。"""
        items = self._by_name(self._items())
        self.assertTrue(items["o__gonep"].remote_gone)
        self.assertTrue(items["o__gonep"].protected)
        execute_prune(self.db, [items["o__gonep"]], "prune-untouched",
                      oplog=self.oplog)
        self.assertTrue(self.dirs["o__gonep"].exists())
        self.assertIsNotNone(self.db.get_repo("o", "gonep"))

    def test_not_gone_repo_never_touched(self):
        """最近同步不是 remote-not-found 的仓库，任何模式都不碰。"""
        items = self._by_name(self._items())
        self.assertFalse(items["o__alive"].remote_gone)
        counts = execute_prune(self.db, [items["o__alive"]], "prune-untouched",
                               oplog=self.oplog)
        self.assertEqual(counts["pruned"], 0)
        self.assertEqual(counts["dir_removed"], 0)

    def test_unknown_mode_rejected(self):
        with self.assertRaises(ValueError):
            execute_prune(self.db, self._items(), "rm-rf", oplog=self.oplog)


class TestDetection(unittest.TestCase):
    def test_is_remote_gone(self):
        self.assertTrue(is_remote_gone(
            [{"status": "failed", "message": "remote: Repository not found."}]))
        self.assertFalse(is_remote_gone(
            [{"status": "failed", "message": "disk full"}]))
        self.assertFalse(is_remote_gone(
            [{"status": "success", "message": "remote: Repository not found."}]))
        self.assertFalse(is_remote_gone([]))

    def test_only_latest_failure_counts(self):
        """历史上的 404 之后恢复又成功 → 不算 gone。"""
        self.assertFalse(is_remote_gone([
            {"status": "success", "message": "ok"},
            {"status": "failed", "message": "remote: Repository not found."},
        ]))


if __name__ == "__main__":
    unittest.main()


class TestCliGuards(unittest.TestCase):
    """审查 P1/P2 整改：--dry-run 降级与 --yes 闸（进程级，走 cli.main）。"""

    def _run(self, *argv, data_dir):
        import contextlib
        import io
        import os

        from gcm import cli
        os.environ["GCM_DATA_DIR"] = str(data_dir)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli.main(list(argv))
        return rc, out.getvalue()

    def test_dry_run_forces_no_clean(self):
        """`--prune --dry-run` 必须降级为只报告（绝不能删）。"""
        import json
        import tempfile as _tf
        with _tf.TemporaryDirectory(prefix="r01dry_") as td:
            rc, out = self._run("--cli", "--json", "--prune", "--dry-run",
                                data_dir=td)
            doc = json.loads(out)
            self.assertEqual(rc, 0)
            self.assertEqual(doc["mode"], "prune:no-clean",
                             "--dry-run 必须把删除模式降为只报告")
            self.assertEqual(doc["pruned"], 0)

    def test_prune_without_yes_rejected(self):
        """删除模式无 --yes（非 json）→ exit 2 + 指引，不执行。"""
        import tempfile as _tf
        with _tf.TemporaryDirectory(prefix="r01yes_") as td:
            rc, out = self._run("--cli", "--prune", data_dir=td)
            self.assertEqual(rc, 2)
            self.assertIn("--yes", out)
