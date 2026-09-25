"""G53 补充覆盖：scan_alerts 全分支 / AlertScanner 线程 / ReportScheduler 线程。"""
import tempfile
import time
import unittest
from pathlib import Path

from gcm.app.alerts import AlertScanner, scan_alerts
from gcm.app.sched_reports import ReportScheduler


class TestScanAlertsBranches(unittest.TestCase):
    def test_no_alerts_clean(self):
        alerts = scan_alerts([], lambda r: [], stale_days=30, now=1750000000.0)
        self.assertEqual(alerts, [])

    def test_missing_history_no_crash(self):
        def boom(rid):
            raise RuntimeError("x")
        alerts = scan_alerts([{"id": 1, "owner": "a", "repo": "b",
                               "last_sync_at": 1750000000.0}],
                             boom, stale_days=30, now=1750000000.0)
        self.assertIsInstance(alerts, list)

    def test_disk_alert_when_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            alerts = scan_alerts([], lambda r: [], data_dir=Path(td),
                                 disk_min_gb=999999, now=1750000000.0)
            self.assertTrue(any(a["kind"] == "disk" for a in alerts))


class TestScannerThread(unittest.TestCase):
    def test_start_stop(self):
        calls = []
        sc = AlertScanner(lambda: ([{"key": "k", "kind": "x", "level": "w",
                                    "title": "t", "message": "m"}] if not calls else []),
                          interval_sec=0.1, on_alerts=lambda a: calls.append(a))
        sc.start()
        time.sleep(1.5)
        sc.stop()
        self.assertGreaterEqual(len(calls), 1)


class TestSchedThread(unittest.TestCase):
    def test_start_stop(self):
        def report_fn(dest, fmt):
            dest.write_text("x\n", encoding="utf-8")
            return 1
        with tempfile.TemporaryDirectory() as td:
            sched = ReportScheduler(report_fn, interval_min=0.01,
                                    out_dir=Path(td) / "out", fmt="csv")
            first = sched.run_once()
            self.assertIsNotNone(first)
            sched.start()
            time.sleep(1.0)
            sched.stop()
            self.assertTrue((Path(td) / "out").exists())


if __name__ == "__main__":
    unittest.main()
