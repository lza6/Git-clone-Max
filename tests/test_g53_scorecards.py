"""G53-1/2/3 数据洞察测试：多维评分卡 / 阈值告警 / 存储看板。"""
import tempfile
import unittest
from pathlib import Path

from gcm.app.alerts import (
    AlertScanner, overall_summary, scan_alerts, scorecard_for_repo,
    scorecards, storage_overview,
)


def _repo(owner="a", repo="b", rid=1, tags="", last_sync_at=None):
    return {"id": rid, "owner": owner, "repo": repo, "tags": tags,
            "local_path": None, "last_sync_at": last_sync_at}


def _hist(statuses, base_ts=1700000000.0):
    out = []
    for i, s in enumerate(statuses):
        out.append({"status": s, "started_at": str(base_ts + i * 86400)})
    return out


class TestScorecard(unittest.TestCase):
    def test_full_health(self):
        card = scorecard_for_repo(_repo(tags='["x"]'), _hist(["success"] * 10, 1750000000.0),
                                  size_bytes=1024 * 1024, now=1750000000.0 + 86400)
        self.assertGreaterEqual(card["overall"], 70)
        self.assertEqual(card["tags_score"], 100)

    def test_no_tags_penalty(self):
        card = scorecard_for_repo(_repo(), _hist(["success"]), now=1750000000.0)
        self.assertEqual(card["tags_score"], 0)

    def test_high_conflict_penalty(self):
        card = scorecard_for_repo(_repo(), _hist(["conflict"] * 5), now=1750000000.0)
        self.assertLess(card["conflicts"], 40)

    def test_scorecards_sorted_worst_first(self):
        repos = [_repo(owner="a", rid=1), _repo(owner="b", rid=2, tags="[x]")]
        cards = scorecards(repos, lambda rid: _hist(["success"]),
                           per_repo_size=lambda p: None, now=1750000000.0)
        self.assertEqual(cards[0]["overall"] <= cards[1]["overall"], True)

    def test_overall_summary_empty(self):
        s = overall_summary([])
        self.assertEqual(s["overall"], 0)
        self.assertIn("暂无", s["suggestions"][0])


class TestAlerts(unittest.TestCase):
    def test_stale_alert(self):
        old = 1750000000.0 - 40 * 86400
        alerts = scan_alerts([_repo(last_sync_at=old)], lambda r: [],
                             stale_days=30, now=1750000000.0)
        self.assertTrue(any(a["kind"] == "stale" for a in alerts))

    def test_repo_fail_rate(self):
        alerts = scan_alerts([_repo()], lambda r: _hist(["failed"] * 6 + ["success"] * 2),
                             repo_fail_rate_pct=50, now=1750000000.0)
        self.assertTrue(any(a["kind"] == "repo_fail_rate" for a in alerts))

    def test_disk_alert(self):
        with tempfile.TemporaryDirectory() as td:
            alerts = scan_alerts([], lambda r: [], data_dir=Path(td),
                                 disk_min_gb=99999, now=1750000000.0)
            self.assertTrue(any(a["kind"] == "disk" for a in alerts))

    def test_scanner_dedup_24h(self):
        fired = []
        sc = AlertScanner(lambda: [{"key": "k1", "kind": "x", "level": "w",
                                    "title": "t", "message": "m"}],
                          now_fn=lambda: 1000.0, on_alerts=fired.append)
        self.assertEqual(len(sc.scan_once(1000.0)), 1)
        self.assertEqual(len(sc.scan_once(1001.0)), 0)   # 24h 内去重
        self.assertEqual(len(sc.scan_once(1000.0 + 90000)), 1)  # 超 24h 再触发


class TestStorage(unittest.TestCase):
    def test_storage_overview(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "progress.json").write_text("x" * 100, encoding="utf-8")
            info = storage_overview(p, threshold_gb=5)
            self.assertGreaterEqual(info["total_bytes"], 100)
            self.assertIn("files", info)
            self.assertIn("suggest", info)

    def test_storage_overview_missing_dir(self):
        info = storage_overview(None, threshold_gb=5)
        self.assertIn("suggest", info)


if __name__ == "__main__":
    unittest.main()
