"""G73 DLQ（已放弃任务）查看与重新入队测试。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from gcm.app.retry_queue import RetryQueue  # noqa: E402
from gcm.models import RepoSpec  # noqa: E402


def _spec() -> RepoSpec:
    return RepoSpec(owner="o", repo="r",
                    url_https="https://github.com/o/r.git",
                    folder_name="o__r@v1", ref="v1")


class TestDlqQueue(unittest.TestCase):
    def test_dropped_keeps_spec_fields_and_requeues(self):
        with tempfile.TemporaryDirectory() as td:
            q = RetryQueue(Path(td) / "rq.json",
                           enabled=True, max_retries=0,
                           jitter=lambda d: d)
            self.assertFalse(q.record_failure(_spec(), "Connection timed out"))
            rows = q.dropped()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["folder_name"], "o__r@v1")
            self.assertEqual(rows[0]["ref"], "v1")
            self.assertEqual(rows[0]["url_https"], "https://github.com/o/r.git")

            self.assertTrue(q.requeue_dropped("o/r"))
            self.assertEqual(q.count(), 1)
            entry = q.snapshot()[0]
            self.assertEqual(entry["attempts"], 0)
            self.assertEqual(entry["folder_name"], "o__r@v1")
            self.assertEqual(entry["ref"], "v1")
            self.assertEqual(q.dropped(), [])
            self.assertFalse(q.requeue_dropped("o/r"))   # 已不在 DLQ

    def test_requeue_missing_returns_false(self):
        q = RetryQueue(path=None, enabled=True)
        self.assertFalse(q.requeue_dropped("nope/r"))


class TestDlqPanel(unittest.TestCase):
    def test_toggle_and_requeue(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])  # noqa: F841
        from gcm.ui.retry_panel import RetryQueuePanel
        q = RetryQueue(path=None, enabled=True, max_retries=0, jitter=lambda d: d)
        q.record_failure(_spec(), "Connection timed out")
        panel = RetryQueuePanel(owner=None, queue=q)
        try:
            panel.refresh()
            self.assertEqual(panel.table.rowCount(), 0)   # 默认看待重试
            panel.ck_show_dropped.setChecked(True)
            self.assertEqual(panel.refresh(), 1)          # DLQ 视图
            self.assertIn("已放弃", panel.summary_lbl.text())
            panel.table.selectRow(0)
            self.assertEqual(panel.requeue_selected(), 1)
            self.assertEqual(q.count(), 1)
            panel.ck_show_dropped.setChecked(False)
            self.assertEqual(panel.refresh(), 1)
        finally:
            panel.close()


if __name__ == "__main__":
    unittest.main()
