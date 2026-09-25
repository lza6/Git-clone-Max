"""G52-5 失败原因聚合测试（分类映射 / 聚合计数 / 面板空态）。"""
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from gcm.ui.failure_panel import (
    CAT_AUTH, CAT_LOCAL, CAT_NETWORK, CAT_OTHER, CAT_PLATFORM,
    FailureDialog, FailurePanel, aggregate_failures, categorize_failure,
)


class TestCategorizeFailure(unittest.TestCase):
    def test_network(self):
        self.assertEqual(categorize_failure("Connection timed out"), CAT_NETWORK)
        self.assertEqual(categorize_failure("Could not resolve host github.com"), CAT_NETWORK)

    def test_auth(self):
        self.assertEqual(categorize_failure("Authentication failed"), CAT_AUTH)
        self.assertEqual(categorize_failure("Invalid username or password"), CAT_AUTH)
        self.assertEqual(categorize_failure("HTTP 403 Forbidden"), CAT_AUTH)

    def test_platform(self):
        self.assertEqual(categorize_failure("Invalid path"), CAT_PLATFORM)
        self.assertEqual(categorize_failure("file already exists"), CAT_PLATFORM)

    def test_local(self):
        self.assertEqual(categorize_failure("No space left on device"), CAT_LOCAL)
        self.assertEqual(categorize_failure("Cannot create directory"), CAT_LOCAL)

    def test_other(self):
        self.assertEqual(categorize_failure("Some unknown weird error"), CAT_OTHER)


class TestAggregate(unittest.TestCase):
    def test_grouping(self):
        rows = [
            {"key": "a/b", "message": "Connection timed out"},
            {"key": "c/d", "message": "Authentication failed"},
            {"key": "e/f", "message": "Connection reset"},
        ]
        agg = aggregate_failures(rows)
        by = {a["category"]: a["count"] for a in agg}
        self.assertEqual(by[CAT_NETWORK], 2)
        self.assertEqual(by[CAT_AUTH], 1)

    def test_empty(self):
        self.assertEqual(aggregate_failures([]), [])


class TestFailurePanelEmpty(unittest.TestCase):
    def test_empty_state(self):
        with tempfile.TemporaryDirectory() as td:
            owner = type("O", (), {"progress_path": Path(td) / "progress.json"})()
            panel = FailurePanel(owner=owner)
            self.assertEqual(panel.refresh([]), 0)


if __name__ == "__main__":
    unittest.main()

class TestFailurePanelData(unittest.TestCase):
    def test_refresh_with_rows(self):
        """refresh 有数据 → 表格行数 = 聚合类别数，占位隐藏。"""
        rows = [
            {"key": "a/b", "message": "Connection timed out"},
            {"key": "c/d", "message": "Authentication failed"},
        ]
        panel = FailurePanel(owner=None)
        n = panel.refresh(rows)
        self.assertEqual(n, 2)
        self.assertFalse(panel.empty_lbl.isVisible())
        self.assertEqual(panel.table.rowCount(), 2)

    def test_load_failed_rows_from_progress(self):
        """_load_failed_rows 从 progress.json 读取 failed 记录。"""
        import json
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            prog = td / "progress.json"
            prog.write_text(json.dumps({
                "finished": [],
                "in_progress": {},
                "failed": [{"key": "x/y", "message": "Connection reset"}],
            }), encoding="utf-8")
            owner = type("O", (), {"progress_path": prog})()
            panel = FailurePanel(owner=owner)
            rows = panel._load_failed_rows()
            self.assertEqual(len(rows), 1)

    def test_load_failed_rows_missing_path(self):
        panel = FailurePanel(owner=type("O", (), {"progress_path": None})())
        self.assertEqual(panel._load_failed_rows(), [])


class TestFailureDialog(unittest.TestCase):
    def test_dialog_construct_and_refresh(self):
        """FailureDialog 构造 + refresh（owner 无 progress 文件 → 空态）。"""
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as td:
            owner = type("O", (), {"progress_path": Path(td) / "progress.json"})()
            dlg = FailureDialog(owner=owner)
            self.assertEqual(dlg.refresh(), 0)
            dlg.close()
