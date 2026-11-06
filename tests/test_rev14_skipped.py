"""Rev14 F2 守卫：断点续传跳过的仓库必须到达终态，不能永远停在「等待中」。

**真事故（本轮实测）**：用户把已经下过的仓库再贴一次点「开始」→ 那一行永远「等待中」、
上一次的耗时被抹成「—」、通知说「成功 0」、状态栏一直「并行同步中」。根因是
`engine.launch()` 的 `_precheck_skip` 命中后只写日志 + 记进度，**不产生 result**。

**这条守卫必须能失败**（`.agent/rules.md` §8）：它断言的是「行状态 = 已跳过」+
「计数 +1」+「状态栏含跳过」三件串联结果 —— 把 `skipped` 信号断开、或把
`report_skipped` 改回默认，三条同时红。

**不联网**：跳过判据是「progress.json 里有已完成记录 且 目录存在」，两者都本地造。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestSkippedReachesTerminalState(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_skip_"))
        store = SettingsStore(self.tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        s.download_dir = str(self.tmp / "clones")
        s.concurrency = 2
        s.retries = 0
        store.save(s)

        # 造一个「已完成且目录存在」的仓库：目录 + .git + progress.json 记录
        target = self.tmp / "clones" / "demo__repo"
        (target / ".git").mkdir(parents=True)
        (target / "a.txt").write_text("x", encoding="utf-8")
        (self.tmp / "progress.json").write_text(
            json.dumps({"finished": [{"key": "demo/repo", "status": "success",
                                      "message": "克隆完成",
                                      "time": "2026-11-05 12:00:00"}],
                        "in_progress": {}, "failed": []}, ensure_ascii=False),
            encoding="utf-8")
        self.win = MainWindow(data_dir=self.tmp)
        self.win.resize(1440, 900)
        self.win.show()
        self._pump(500)

    def tearDown(self):
        try:
            self.win.close()
            self.win.deleteLater()
        finally:
            self._app.processEvents()

    def _pump(self, ms: int = 400) -> None:
        from PyQt6.QtTest import QTest
        QTest.qWait(ms)

    def _run(self) -> None:
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        self.win.repo_input.setPlainText("demo/repo")
        self._pump(400)
        QTest.mouseClick(self.win.btn_start, Qt.MouseButton.LeftButton)
        waited = 0
        while self.win.busy and waited < 30_000:
            self._app.processEvents()
            QTest.qWait(200)
            waited += 200
        self._pump(700)

    def test_skipped_row_reaches_terminal_state(self):
        """有行时该行必须到终态。

        注意：**零幸存者的批次**（全部被跳过）走的是既有管线 —— 表格按
        `engine.specs` 重建，可能一行都没有。这种情况下「跳过 N」由状态栏兜底
        （见 `test_summary_reports_skip_count`），而「有行时必须是已跳过」这条
        在真实网络 E2E（同一窗口连跑两轮）里已经验证过：第 2 轮行显示
        `已跳过 / — / 已完成，跳过（克隆完成）`。
        """
        self._run()
        table = self.win.progress_panel.table
        # Rev14 F2 第二轮：零幸存者批次现在**也会建行** —— `install_row` 只用
        # `setItem`（对不存在的行是静默空操作），所以预填前必须 `_prepare_table`
        # 把行数撑起来。这条断言就是钉住那个修复。
        self.assertGreaterEqual(table.rowCount(), 1,
                                "被跳过的仓库必须留一行（否则用户只看到空表）")
        status = table.item(0, 2)
        detail = table.item(0, 4)
        self.assertIsNotNone(status)
        self.assertEqual(status.text(), "已跳过",
                         "跳过的仓库必须到终态 —— 停在「等待中」就是 F2 事故")
        self.assertEqual(status.data(__import__("PyQt6.QtCore", fromlist=["Qt"])
                                     .Qt.ItemDataRole.UserRole), "skipped",
                         "UserRole 必须是 skipped，否则批次小结统计不到")
        self.assertIn("跳过", detail.text() if detail is not None else "",
                      "详情列必须说明为什么没跑（用户要能看懂）")

    def test_summary_reports_skip_count(self):
        self._run()
        msg = self.win.statusBar().currentMessage()
        self.assertIn("跳过 1", msg,
                      f"批次小结必须报「跳过 N」，否则「成功 0」看起来像什么都没发生：{msg!r}")
        self.assertNotIn("并行同步中", msg,
                         "批次已结束，状态栏不许还停在「并行同步中」（Rev14 F3）")

    def test_engine_reported_it(self):
        """反向自检：计数必须真的 +1（把 skipped 信号断开这条会红）。"""
        self._run()
        self.assertEqual(int(getattr(self.win, "_skipped_count", 0)), 1,
                         "引擎没有把「跳过」回报给 UI（skipped 信号没接或没 emit）")


if __name__ == "__main__":
    unittest.main()
