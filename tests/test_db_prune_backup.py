# -*- coding: utf-8 -*-
"""DB 层改进测试（对应指南 G33-3 / G33-5）：
- G33-5 sync_history 容量治理：每仓库保留最近 200 条 + 全局 90 天窗口
  （_maybe_prune_history，计数器每 100 次 add_sync_history 触发一次）
- G33-3 数据库备份 backup_to：WAL checkpoint 后拷贝主库文件
"""
from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.repo_db import Database
from gcm.models import RepoSpec, SyncAction, SyncStatus


def _spec(owner, repo):
    return RepoSpec(owner, repo, f"https://github.com/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


def _days_ago(days: int, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """生成 days 天前的本地时间文本（与 add_sync_history 的默认格式一致）。"""
    return time.strftime(fmt, time.localtime(time.time() - days * 86400))


def _add_history(db: Database, repo_id: int, started_at: str | None = None,
                 status=SyncStatus.SUCCESS, action=SyncAction.CLONED):
    db.add_sync_history(repo_id, status, action, "m", started_at=started_at)


class TestPruneCapacity(unittest.TestCase):
    """G33-5：每仓库保留最近 200 条。"""

    def test_prune_keeps_max_200_per_repo(self):
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "t.db")
        rid = db.upsert_repo(_spec("o", "a"), str(tmp / "o__a"))
        for _ in range(250):
            _add_history(db, rid, started_at=_days_ago(30))
        # force 清理
        deleted = db._maybe_prune_history(force=True)
        self.assertGreaterEqual(deleted, 50, "至少应删除 250-200=50 条")
        remaining = len(db.history(rid, limit=1000))
        self.assertLessEqual(remaining, 200, "每仓库保留不超过 200 条")
        db.close()

    def test_prune_removes_only_old_window(self):
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "t.db")
        rid = db.upsert_repo(_spec("o", "b"), str(tmp / "o__b"))
        _add_history(db, rid, started_at=_days_ago(100))  # 超 90 天窗口
        _add_history(db, rid, started_at=_days_ago(1))    # 窗口内
        db._maybe_prune_history(force=True)
        remaining = db.history(rid, limit=1000)
        self.assertEqual(len(remaining), 1, "90 天前的旧记录应被删")
        self.assertEqual(remaining[0]["started_at"], _days_ago(1),
                         "今天的记录应保留")
        db.close()

    def test_prune_counter_triggers_cleanup(self):
        """计数器到 100 时，add_sync_history 自动触发清理。"""
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "t.db")
        rid = db.upsert_repo(_spec("o", "c"), str(tmp / "o__c"))
        _add_history(db, rid, started_at=_days_ago(100))  # 超窗记录
        db._prune_counter = 99                            # 模拟接近触发点
        _add_history(db, rid, started_at=_days_ago(1))    # 第 100 次 → 触发清理
        # 清理被执行：超窗行被删，仅剩今天的行
        remaining = db.history(rid, limit=1000)
        self.assertEqual(len(remaining), 1, "计数器触发的清理应删除 100 天前的行")
        self.assertEqual(remaining[0]["started_at"], _days_ago(1))
        # 计数器应已复位
        self.assertEqual(db._prune_counter, 0)
        db.close()

    def test_prune_not_run_before_counter(self):
        """未到 100 且非 force：不执行清理，超窗记录保留。"""
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "t.db")
        rid = db.upsert_repo(_spec("o", "d"), str(tmp / "o__d"))
        _add_history(db, rid, started_at=_days_ago(100))
        db._prune_counter = 5
        deleted = db._maybe_prune_history(force=False)
        self.assertEqual(deleted, 0, "未到阈值不清理")
        self.assertEqual(len(db.history(rid, limit=1000)), 1, "超窗记录仍保留")
        db.close()

    def test_stats_overview_still_correct_after_prune(self):
        """清理后 stats_overview 统计仍正确（不破坏既有统计）。"""
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "t.db")
        rid1 = db.upsert_repo(_spec("o", "e1"), str(tmp / "o__e1"))
        rid2 = db.upsert_repo(_spec("o", "e2"), str(tmp / "o__e2"))
        # 每个仓库 5 条窗口内 + 1 条超窗 → 清理后全局应剩 10 条
        for rid in (rid1, rid2):
            for _ in range(5):
                _add_history(db, rid, started_at=_days_ago(1))
            _add_history(db, rid, started_at=_days_ago(100))
        before = db.stats_overview()
        self.assertEqual(before["total_syncs"], 12, "清理前 2 仓 × 6 条")
        db._maybe_prune_history(force=True)
        after = db.stats_overview()
        self.assertEqual(after["total_syncs"], 10, "清理后全局剩 10 条")
        self.assertEqual(after["total_repos"], 2)
        self.assertEqual(after["success_syncs"], 10)
        db.close()


class TestBackupTo(unittest.TestCase):
    """G33-3：backup_to 备份可自查、可恢复、含 WAL 未检查点数据。"""

    def test_backup_returns_count_and_restores(self):
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "src.db")
        rid = db.upsert_repo(_spec("o", "ba"), str(tmp / "o__ba"))
        rid2 = db.upsert_repo(_spec("o", "bb"), str(tmp / "o__bb"))
        rid3 = db.upsert_repo(_spec("o", "bc"), str(tmp / "o__bc"))
        for _ in range(3):
            _add_history(db, rid, started_at=_days_ago(1))
        _add_history(db, rid2, started_at=_days_ago(1))
        _add_history(db, rid3, started_at=_days_ago(1))  # 全局共 5 条

        backup = tmp / "bk" / "backup.db"  # 父目录不存在 → 应自动创建
        n = db.backup_to(backup)
        self.assertEqual(n, 3, "backup_to 返回仓库总数")
        self.assertTrue(backup.exists(), "备份文件应存在")

        # 用 Database 重开备份库，逐项核对
        db2 = Database(backup)
        self.assertEqual(db2.count(), 3)
        repos = db2.list_repos()
        self.assertEqual(repos[0]["owner"], "o")
        self.assertEqual(db2.stats_overview()["total_syncs"],
                         db.stats_overview()["total_syncs"],
                         "备份库历史统计与原库一致")
        db2.close()
        db.close()

    def test_backup_includes_uncheckpointed_wal_data(self):
        """插入后不 close、直接备份：WAL 中未检查点数据也必须进入备份库。"""
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "src2.db")
        rid = db.upsert_repo(_spec("o", "wc"), str(tmp / "o__wc"))
        _add_history(db, rid, started_at=_days_ago(1))
        _add_history(db, rid, started_at=_days_ago(1))
        # 连接保持打开、WAL 可能有未检查点数据 → 直接备份
        backup = tmp / "backup2.db"
        db.backup_to(backup)
        db2 = Database(backup)
        self.assertEqual(db2.count(), 1, "备份库能看到刚插入的仓库")
        hist = db2.history(rid, limit=1000)
        self.assertEqual(len(hist), 2, "WAL 未检查点历史也被备份")
        db2.close()
        db.close()

    def test_backup_after_close_without_wal(self):
        """备份目标目录已存在、非 WAL 异常路径也不抛错。"""
        tmp = Path(tempfile.mkdtemp())
        db = Database(tmp / "src3.db")
        rid = db.upsert_repo(_spec("o", "x"), str(tmp / "o__x"))
        _add_history(db, rid, started_at=_days_ago(1))
        db.close()
        # 重新打开（WAL 可能已无内容）
        db = Database(tmp / "src3.db")
        backup = tmp / "backup3.db"
        self.assertIsInstance(db.backup_to(backup), int)
        self.assertTrue(backup.exists())
        db.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
