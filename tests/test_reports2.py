"""G37-3 报表筛选导出测试：filters 筛选 + 导出（对应指南 G37-3）。

行为约定（明确且已测）：
- None / 空串 / 空白值 → 不筛（等效全量）。
- 非 "YYYY-MM-DD" 无效日期 → 忽略该日期条件（不崩）。
- 有效 start > 有效 end → 返回 0 行（不可达区间）。
- status / host 精确匹配；status/日期条件作用于 JOIN 后行，
  无历史仓库（h.* 为 NULL）在 status/日期筛选下被排除。
"""
from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.repo_db import Database  # noqa: E402  # sys.path 引导后导入
from gcm.models import RepoSpec, SyncAction, SyncStatus  # noqa: E402
from gcm.reports import _fetch_rows, export_csv, export_markdown  # noqa: E402


def _spec(owner, repo, host="github.com"):
    return RepoSpec(owner, repo, f"https://{host}/{owner}/{repo}.git",
                    folder_name=f"{owner}__{repo}")


def _ts(days: int) -> str:
    """返回 now + days 天的 '%Y-%m-%d %H:%M:%S' 时间文本。"""
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class TestReportsFilters(unittest.TestCase):
    """_fetch_rows 筛选逻辑。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "report2.db")
        # host=github.com / status=success / 今天
        rid1 = self.db.upsert_repo(_spec("o1", "r1"), "/x/o1__r1", host="github.com")
        self.db.add_sync_history(rid1, SyncStatus.SUCCESS, SyncAction.CLONED,
                                 "ok", commits=3, duration_ms=1000, started_at=_ts(0))
        # host=gitlab.com / status=failed / 今天
        rid2 = self.db.upsert_repo(_spec("o2", "r2"), "/x/o2__r2", host="gitlab.com")
        self.db.add_sync_history(rid2, SyncStatus.FAILED, SyncAction.FAILED,
                                 "bad", commits=0, duration_ms=2000, started_at=_ts(0))
        # host=github.com / status=success / 去年
        rid3 = self.db.upsert_repo(_spec("o3", "r3"), "/x/o3__r3", host="github.com")
        self.db.add_sync_history(rid3, SyncStatus.SUCCESS, SyncAction.UPDATED,
                                 "old", commits=1, duration_ms=500, started_at=_ts(-365))

    def tearDown(self):
        self.db.close()

    # ---------------- 空筛选 == 全量 ----------------
    def test_none_filters_equals_full(self):
        self.assertEqual(len(_fetch_rows(self.db, None)), len(_fetch_rows(self.db)))
        self.assertEqual(len(_fetch_rows(self.db, None)), 3)

    def test_empty_filters_equals_full(self):
        full = _fetch_rows(self.db)
        empty = _fetch_rows(self.db, {})
        self.assertEqual(len(empty), len(full))
        self.assertEqual([r["repo"] for r in empty], [r["repo"] for r in full])

    def test_blank_filters_ignored(self):
        rows = _fetch_rows(self.db, {"host": "", "status": "  ",
                                     "start": None, "end": ""})
        self.assertEqual(len(rows), 3)

    # ---------------- 单值筛选 ----------------
    def test_host_filter_only_host(self):
        rows = _fetch_rows(self.db, {"host": "gitlab.com"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["host"], "gitlab.com")
        self.assertEqual(rows[0]["repo"], "r2")

    def test_status_filter_only_status(self):
        rows = _fetch_rows(self.db, {"status": "failed"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "failed")
        self.assertEqual(rows[0]["repo"], "r2")

    def test_combined_host_and_status(self):
        rows = _fetch_rows(self.db, {"host": "github.com", "status": "success"})
        repos = sorted(r["repo"] for r in rows)
        self.assertEqual(repos, ["r1", "r3"])

    # ---------------- 日期范围 ----------------
    def test_date_range_this_year_only(self):
        rows = _fetch_rows(self.db, {"start": _today(), "end": _today()})
        repos = sorted(r["repo"] for r in rows)
        self.assertEqual(repos, ["r1", "r2"])  # 去年 r3 被排除

    def test_start_after_end_returns_empty(self):
        self.assertEqual(_fetch_rows(self.db, {"start": "2030-12-31",
                                               "end": "2030-01-01"}), [])

    def test_invalid_date_ignored(self):
        # 无效日期被忽略 → 相当于无日期筛选
        rows = _fetch_rows(self.db, {"start": "not-a-date", "end": ""})
        self.assertEqual(len(rows), 3)

    # ---------------- 无历史仓库 ----------------
    def test_no_history_repo_in_full_but_filtered_out(self):
        self.db.upsert_repo(_spec("o9", "r9"), "/x/o9__r9", host="github.com")
        full = _fetch_rows(self.db)
        self.assertEqual(len(full), 4)  # 含无历史仓库
        self.assertIn("r9", [r["repo"] for r in full])
        status_rows = _fetch_rows(self.db, {"status": "success"})
        self.assertNotIn("r9", [r["repo"] for r in status_rows])
        date_rows = _fetch_rows(self.db, {"start": "2000-01-01", "end": "2100-01-01"})
        self.assertNotIn("r9", [r["repo"] for r in date_rows])


class TestReportsExportFilters(unittest.TestCase):
    """export_csv / export_markdown 带 filters 导出 + 回归。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = Database(self.tmp / "report2b.db")
        rid1 = self.db.upsert_repo(_spec("o1", "r1"), "/x/o1__r1", host="github.com")
        self.db.add_sync_history(rid1, SyncStatus.SUCCESS, SyncAction.CLONED,
                                 "ok", commits=3, duration_ms=1000, started_at=_ts(0))
        rid2 = self.db.upsert_repo(_spec("o2", "r2"), "/x/o2__r2", host="gitlab.com")
        self.db.add_sync_history(rid2, SyncStatus.FAILED, SyncAction.FAILED,
                                 "bad", commits=0, duration_ms=2000, started_at=_ts(0))

    def tearDown(self):
        self.db.close()

    def test_export_csv_with_filter(self):
        out = self.tmp / "filtered.csv"
        n = export_csv(self.db, out, {"status": "failed"})
        self.assertEqual(n, 1)
        with open(out, encoding="utf-8-sig", newline="") as f:
            data = list(csv.DictReader(f))
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["status"], "failed")
        self.assertEqual(data[0]["host"], "gitlab.com")

    def test_export_markdown_with_filter(self):
        out = self.tmp / "filtered.md"
        n = export_markdown(self.db, out, {"host": "github.com"})
        self.assertEqual(n, 1)
        text = out.read_text(encoding="utf-8")
        self.assertIn("o1", text)
        self.assertNotIn("o2", text)

    def test_export_csv_full_regression(self):
        # 回归：不带 filters 参数（旧调用方）仍全量
        out = self.tmp / "full.csv"
        self.assertEqual(export_csv(self.db, out), 2)
        self.assertEqual(export_csv(self.db, out, None), 2)

    def test_export_markdown_full_regression(self):
        out = self.tmp / "full.md"
        self.assertEqual(export_markdown(self.db, out), 2)
        self.assertEqual(export_markdown(self.db, out, None), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
