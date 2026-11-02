"""Rev8 C6-4 守卫：黑匣子日志按关键字过滤。

三条不变量：
1. 日志工具行有过滤输入框（`log_filter_edit`）+ 清除按钮；输入即过滤；
2. `LogBuffer.filter_text` 变化后，flush 只渲染含关键字的行；清空过滤 →
   `replay_all()` 从 LogModel 重放全量（3000 条上限内）；
3. 过滤是**显示层**行为：LogModel 永远持有全量（清过滤后数据不丢）。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QPlainTextEdit  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402


class TestLogFilter(unittest.TestCase):
    def _make(self):
        from gcm.ui.log_buffer import LogBuffer
        view = QPlainTextEdit()
        buf = LogBuffer(parent=None, log_view=view, log_count=None, interval_ms=1)
        return buf, view

    def test_filter_hides_non_matching(self):
        from gcm.ui.theme import LogEvent, LogLevel
        buf, view = self._make()
        buf.filter_text = "repoA"
        buf.append(LogEvent("10:00", LogLevel.INFO, "[repoA] cloned"))
        buf.append(LogEvent("10:01", LogLevel.INFO, "[repoB] cloned"))
        buf.flush()
        text = view.toPlainText()
        self.assertIn("repoA", text)
        self.assertNotIn("repoB", text)

    def test_replay_all_restores(self):
        from gcm.ui.theme import LogEvent, LogLevel, LogModel
        buf, view = self._make()
        model = LogModel()
        model.append("10:00", LogLevel.INFO, "[repoA] x")
        model.append("10:01", LogLevel.INFO, "[repoB] y")
        # replay_all 从 model 读；用 QObject holder 模拟 owner.log
        from PyQt6.QtCore import QObject
        holder = QObject()
        holder.log = model
        buf.setParent(holder)
        buf.filter_text = "repoA"
        buf.append(LogEvent("10:00", LogLevel.INFO, "[repoA] x"))
        buf.flush()
        buf.filter_text = ""
        buf.replay_all()
        text = view.toPlainText()
        self.assertIn("repoA", text)
        self.assertIn("repoB", text)

    def test_model_keeps_everything(self):
        """过滤不改模型：LogModel.snapshot 永远全量。"""
        from gcm.ui.theme import LogModel, LogEvent, LogLevel
        m = LogModel()
        m.append("t", LogLevel.INFO, "[repoA] x")
        m.append("t", LogLevel.INFO, "[repoB] y")
        self.assertEqual(len(m.snapshot()), 2)

    def test_settings_panel_has_filter_edit(self):
        src = (ROOT / "gcm/ui/settings_panel.py").read_text(encoding="utf-8")
        src_lf = (ROOT / "gcm/ui/log_filter.py").read_text(encoding="utf-8")
        self.assertTrue("log_filter_edit" in src or "log_filter_edit" in src_lf,
                        "日志过滤无生产调用点")


if __name__ == "__main__":
    unittest.main()
