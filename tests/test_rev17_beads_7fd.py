"""Rev17（beads 7fd）：事件摘要语义化 + 终态进度条不画填充的守卫。

- 7fd-②：TASK_RESULT 摘要 `failed · cloned · 克隆完成` 三段直拼语义混淆 →
  状态词中文化 + 空 message 跳过；
- 7fd-③：终态行（含 skipped）的进度值置 -1（detail-only 分支），不再画
  100% 满条让「已跳过」看起来像在跑/刚跑完。
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


class TestSummarySemantics(unittest.TestCase):
    def test_failed_with_done_message_is_not_confusing(self):
        from gcm.app.event_bus import Event, EventType
        from gcm.ui.event_panel import _summary
        ev = Event(seq=1, ts=0.0, type=EventType.TASK_RESULT,
                   data={"status": "failed", "action": "cloned",
                         "message": "克隆完成"})
        s = _summary(ev)
        self.assertIn("失败", s, f"状态应中文化，实际：{s!r}")
        self.assertNotIn("failed", s, f"英文 status 不应裸奔，实际：{s!r}")
        # "克隆完成"（action 词）不再作为独立段与 message 重复
        self.assertEqual(s.count("克隆完成"), 1, f"重复段：{s!r}")

    def test_empty_parts_are_skipped(self):
        from gcm.app.event_bus import Event, EventType
        from gcm.ui.event_panel import _summary
        ev = Event(seq=1, ts=0.0, type=EventType.TASK_RESULT,
                   data={"status": "success", "action": "", "message": ""})
        self.assertEqual(_summary(ev), "成功")


class TestTerminalProgressBar(unittest.TestCase):
    """7fd-③：终态行 _progress_values 置 -1 而非 100。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_source_uses_negative_one_for_terminal(self):
        """源码级：终态写 -1 且有注释（防回退到 100 满条）。"""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "gcm" / "ui" / "progress_table.py").read_text(encoding="utf-8")
        seg = src[src.index("终态优先"):]
        seg = seg[:seg.index("pal = current_palette()")]
        self.assertIn("= -1", seg, "终态进度值应置 -1（detail-only），不是 100 满条")

    def test_delegate_unknown_state_draws_no_fill(self):
        """委托 -1（未知态）不画填充条 —— 变异点：把 -1 当 100 画会红。"""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "gcm" / "ui" / "progress_delegate.py").read_text(encoding="utf-8")
        # else 分支（pct<0）只画文本；确认 `if pct >= 0` 包住填充绘制
        fill = src[src.index("if pct >= 0:"):]
        self.assertIn("drawRoundedRect", fill[:fill.index("else")],
                      "填充绘制应在 pct>=0 分支内")
        unknown = src[src.index("else:"):]
        self.assertNotIn("drawRoundedRect(\n                QRect(rect.x(), rect.y(), max(4, width)",
                         unknown[:unknown.index("painter.setFont")],
                         "未知态不应画填充")


class TestTerminalDetailCleared(unittest.TestCase):
    """瑕疵②：终态清 _detail_batch 与 DETAIL_ROLE（竞态：80% 速率 → 终态 → flush）。"""

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_race_fixed(self):
        import tempfile
        from pathlib import Path as _P
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        from gcm.ui.progress_delegate import DETAIL_ROLE
        from gcm.models import RepoSpec, SyncResult, SyncStatus, SyncAction
        d = _P(tempfile.mkdtemp(prefix="gcm_race_"))
        w = MainWindow(data_dir=d, db=Database(d / "t.db"),
                       settings=SettingsStore(d / "s.json"))
        spec = RepoSpec(owner="o", repo="r", url_https="https://github.com/o/r")
        w.progress_panel._add_table_row(0, spec)
        w.progress_panel._detail_batch[0] = "1.2MB/s · 3 objects"
        res = SyncResult(spec=spec, status=SyncStatus.SKIPPED,
                         action=SyncAction.SKIPPED, message="已存在")
        w.progress_panel._on_worker_result(0, res)
        w.progress_panel._flush_detail_batch()
        it = w.progress_panel.table.item(0, 1)
        self.assertEqual(it.data(DETAIL_ROLE), "",
                         "终态行被 flush 写回运行态速率（竞态未修）")
        w.close()

    def test_status_word_reuses_result_labels(self):
        """瑕疵③：_status_word 必须走 result_labels（单一来源），不得复刻映射。"""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "gcm" / "ui" / "event_panel.py").read_text(encoding="utf-8")
        self.assertIn("from .result_labels import status_label", src,
                      "_status_word 未复用 result_labels（第二份真相）")


if __name__ == "__main__":
    unittest.main()
