"""G80-B7-1 守卫：后台任务可见性（4 个隐形定时器 → 一张能看的表）。

四条不变量：
1. 登记表按 interval 推算 next-run；**未启用 / 未跑过**时不编造时间（返回 0）；
2. 三个调度器（看门狗 / 告警 / 报表）与自动更新在"真的跑了一轮"后必须登记；
3. 面板按状态给不同文案（已关闭 / 已启用（启动后首轮）/ 运行中），不靠留空暗示；
4. 设置页按钮 → MainWindow 转发 → 面板 的三段接线必须真的通。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402


class TestRegistry(unittest.TestCase):
    def setUp(self):
        from gcm.app import background_jobs as bj

        self.bj = bj
        bj.registry().reset()

    def tearDown(self):
        self.bj.registry().reset()

    def test_next_run_requires_enabled_and_first_run(self):
        r = self.bj.registry()
        r.register("k", "任务A", enabled=True, interval_sec=600)
        jobs = r.snapshot()
        self.assertEqual(jobs[0].next_run_at, 0.0, "还没跑过就不该编造下次时间")
        r.notify_run("k", result="ok")
        jobs = r.snapshot()
        self.assertGreater(jobs[0].next_run_at, jobs[0].last_run_at)
        self.assertEqual(jobs[0].last_result, "ok")

    def test_disabled_job_has_no_next_run(self):
        r = self.bj.registry()
        r.register("k", "任务A", enabled=False, interval_sec=600)
        r.notify_run("k")
        self.assertEqual(r.snapshot()[0].next_run_at, 0.0)

    def test_notify_unknown_key_does_not_create_entry(self):
        r = self.bj.registry()
        r.notify_run("nope")
        self.assertEqual(r.snapshot(), [], "未登记的任务不得凭空出现")

    def test_register_updates_interval_but_keeps_last_run(self):
        r = self.bj.registry()
        r.register("k", "任务A", enabled=True, interval_sec=600)
        r.notify_run("k")
        before = r.snapshot()[0]
        r.register("k", "任务A", enabled=True, interval_sec=1800)
        after = r.snapshot()[0]
        self.assertEqual(after.interval_sec, 1800)
        self.assertEqual(after.last_run_at, before.last_run_at, "改间隔不该清掉上次运行时间")

    def test_register_default_jobs_maps_settings(self):
        from gcm.db.settings import Settings

        self.bj.register_default_jobs(
            Settings(watchdog_enabled=True, watchdog_interval_min=5,
                     sched_report_enabled=True, sched_report_interval_min=60,
                     auto_update_minutes=0, g53_alerts_enabled=False))
        by = {j.key: j for j in self.bj.registry().snapshot()}
        self.assertEqual(len(by), 4)
        self.assertTrue(by[self.bj.JOB_WATCHDOG].enabled)
        self.assertEqual(by[self.bj.JOB_WATCHDOG].interval_sec, 300.0)
        self.assertEqual(by[self.bj.JOB_REPORTS].interval_sec, 3600.0)
        self.assertFalse(by[self.bj.JOB_AUTO_UPDATE].enabled, "间隔 0 = 关闭自动更新")
        self.assertFalse(by[self.bj.JOB_ALERTS].enabled)


class TestSchedulerHooks(unittest.TestCase):
    """三个调度器真的会登记（不是只写了个函数没人调）。"""

    def setUp(self):
        from gcm.app import background_jobs as bj

        self.bj = bj
        bj.registry().reset()
        bj.register_default_jobs(type("S", (), {
            "auto_update_minutes": 30, "watchdog_interval_min": 5,
            "g53_alerts_interval_min": 60, "sched_report_interval_min": 60,
            "watchdog_enabled": True, "g53_alerts_enabled": True,
            "sched_report_enabled": True})())

    def tearDown(self):
        self.bj.registry().reset()

    def test_report_scheduler_registers_run(self):
        from gcm.app.sched_reports import ReportScheduler

        with tempfile.TemporaryDirectory() as td:
            s = ReportScheduler(report_fn=lambda dest, fmt: 3, out_dir=td)
            out = s.run_once()
            self.assertIsNotNone(out)
        job = {j.key: j for j in self.bj.registry().snapshot()}[self.bj.JOB_REPORTS]
        self.assertGreater(job.last_run_at, 0)
        self.assertIn("行", job.last_result)

    def test_report_scheduler_registers_failure(self):
        from gcm.app.sched_reports import ReportScheduler

        def _boom(dest, fmt):
            raise RuntimeError("磁盘满")

        with tempfile.TemporaryDirectory() as td:
            s = ReportScheduler(report_fn=_boom, out_dir=td)
            self.assertIsNone(s.run_once())
        job = {j.key: j for j in self.bj.registry().snapshot()}[self.bj.JOB_REPORTS]
        self.assertIn("磁盘满", job.last_error)

    def test_alert_scanner_registers_run(self):
        from gcm.app.alerts import AlertScanner

        scanner = AlertScanner(scan_fn=lambda: [{"key": "a/b", "kind": "stale"}],
                               interval_sec=60)
        scanner.scan_once()
        job = {j.key: j for j in self.bj.registry().snapshot()}[self.bj.JOB_ALERTS]
        self.assertGreater(job.last_run_at, 0)
        self.assertIn("1", job.last_result)

    def test_watchdog_registers_dispatch(self):
        from gcm.app.watchdog import Watchdog

        class _Store:
            def is_resumable(self):
                return True

            def to_repo_specs(self):
                return [object(), object()]

        class _Engine:
            busy = False

        with tempfile.TemporaryDirectory() as td:
            w = Watchdog(Path(td) / "sessions.json", store=_Store(),
                         engine=_Engine(), interval_min=5)
            self.assertTrue(w.check())
        job = {j.key: j for j in self.bj.registry().snapshot()}[self.bj.JOB_WATCHDOG]
        self.assertGreater(job.last_run_at, 0)
        self.assertIn("2", job.last_result)


class TestPanelAndWiring(unittest.TestCase):
    def setUp(self):
        from gcm.app import background_jobs as bj

        self.bj = bj
        bj.registry().reset()

    def tearDown(self):
        self.bj.registry().reset()

    def test_panel_renders_states(self):
        from gcm.ui.background_jobs_panel import BackgroundJobsDialog

        self.bj.registry().register("a", "任务A", enabled=False, interval_sec=600)
        self.bj.registry().register("b", "任务B", enabled=True, interval_sec=600)
        self.bj.registry().register("c", "任务C", enabled=True, interval_sec=600)
        self.bj.registry().notify_run("c", result="成功")
        dlg = BackgroundJobsDialog()
        try:
            self.assertEqual(dlg.table.rowCount(), 3)
            self.assertEqual(dlg.table.item(0, 1).text(), "已关闭")
            self.assertEqual(dlg.table.item(1, 1).text(), "已启用（启动后首轮）")
            self.assertEqual(dlg.table.item(2, 1).text(), "运行中")
            self.assertEqual(dlg.table.item(2, 5).text(), "成功")
        finally:
            dlg.close()

    def test_empty_panel_shows_placeholder(self):
        from gcm.ui.background_jobs_panel import BackgroundJobsDialog

        dlg = BackgroundJobsDialog()
        try:
            self.assertEqual(dlg.table.rowCount(), 1)
            self.assertIn("还没有登记", dlg.table.item(0, 0).text())
        finally:
            dlg.close()

    def test_settings_button_and_forwarder_exist(self):
        src = Path("gcm/ui/settings_groups.py").read_text(encoding="utf-8")
        self.assertIn("btn_background_jobs", src)
        self.assertIn("owner.show_background_jobs", src)
        mw = Path("gcm/ui/main_window.py").read_text(encoding="utf-8")
        self.assertIn("def show_background_jobs(self)", mw)
        self.assertIn("BackgroundJobsDialog", mw)

    def test_startup_registers_jobs(self):
        """启动时真的会登记（只加了函数却没在启动处调用 = 伪闭环）。"""
        mw = Path("gcm/ui/main_window.py").read_text(encoding="utf-8")
        self.assertIn("register_default_jobs(self.settings)", mw)


if __name__ == "__main__":
    unittest.main()
