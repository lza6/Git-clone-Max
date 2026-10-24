"""G80-B4-1b 守卫：半成品目录的**可见性与可清理性**。

三条不变量：
1. 扫描出的 RepoInfo 必须带 `needs_attention`（有 .git 但工作树除 .git 外零条目）；
2. 导入对话框里疑似半成品**默认不勾选**（用户仍可手动勾）；
3. 进度表右键只在疑似半成品行提供「删除半成品目录…」，且**必须二次确认**。
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


class TestScannerNeedsAttention(unittest.TestCase):
    def test_halfcloned_dir_is_flagged(self):
        from gcm.app.scanner import _looks_halfcloned

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            half = root / "half"
            (half / ".git").mkdir(parents=True)
            self.assertTrue(_looks_halfcloned(half))

    def test_healthy_repo_is_not_flagged(self):
        from gcm.app.scanner import _looks_halfcloned

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            good = root / "good"
            (good / ".git").mkdir(parents=True)
            (good / "a.txt").write_text("x", encoding="utf-8")
            self.assertFalse(_looks_halfcloned(good))

    def test_non_repo_is_not_flagged(self):
        from gcm.app.scanner import _looks_halfcloned

        with tempfile.TemporaryDirectory() as td:
            plain = Path(td) / "plain"
            plain.mkdir()
            self.assertFalse(_looks_halfcloned(plain))

    def test_scan_marks_halfcloned_repo(self):
        """端到端：扫描根目录里只有一个半成品仓库 → 结果被标记。"""
        from gcm.app.scanner import scan_git_dirs

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "owner__half" / ".git").mkdir(parents=True)
            rows = scan_git_dirs(str(root), max_depth=3)
        self.assertTrue(rows, "扫描没发现那个半成品目录")
        self.assertTrue(any(getattr(r, "needs_attention", False) for r in rows))


class TestImportDialogDefaultUnchecked(unittest.TestCase):
    def test_halfcloned_is_unchecked_by_default(self):
        from gcm.app.scanner import RepoInfo
        from gcm.ui.local_repos_dialog import LocalReposDialog

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            half = root / "half"
            (half / ".git").mkdir(parents=True)
            good = root / "good"
            (good / ".git").mkdir(parents=True)
            (good / "a.txt").write_text("x", encoding="utf-8")

            from gcm.app.scanner import _make_info
            info_half = _make_info(root, half, 0)
            info_good = _make_info(root, good, 0)

            dlg = LocalReposDialog(None, [], db=None)
            dlg._rows = [info_half, info_good]
            dlg._filtered = [info_half, info_good]
            dlg._rebuild_table()
            try:
                from PyQt6.QtCore import Qt
                self.assertEqual(dlg.table.item(0, 0).checkState(), Qt.CheckState.Unchecked,
                                 "疑似半成品必须默认不勾选")
                self.assertEqual(dlg.table.item(1, 0).checkState(), Qt.CheckState.Checked,
                                 "健康仓库仍默认勾选")
                self.assertIn("半成品", dlg.table.item(0, 1).text())
            finally:
                dlg.close()


class TestRowMenuHalfclone(unittest.TestCase):
    """右键菜单：疑似半成品行才出现删除入口，且必须二次确认。"""

    def _panel(self, spec):
        from gcm.models import RepoSpec
        from gcm.ui.progress_table import ProgressTable

        class _Owner:
            def __init__(self):
                self.row_specs = {0: spec}
                self.target_edit = mock.Mock()
                self.target_edit.text.return_value = ""
                self.data_dir = Path(tempfile.gettempdir())
                self.statusBar = lambda: mock.Mock()
                self._emit_log = mock.Mock()
                self.settings = mock.Mock()

        owner = _Owner()
        panel = ProgressTable(owner)
        panel._test_owner_ref = owner  # 测试专用：owner 是弱引用，需外部强引用
        return panel

    def test_halfcloned_detection(self):
        from gcm.models import RepoSpec

        with tempfile.TemporaryDirectory() as td:
            half = Path(td) / "half"
            (half / ".git").mkdir(parents=True)
            spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b",
                            local_path=str(half))
            panel = self._panel(spec)
            self.assertTrue(panel._row_is_halfcloned(0))
            (half / "a.txt").write_text("x", encoding="utf-8")
            self.assertFalse(panel._row_is_halfcloned(0), "有工作树内容就不算半成品")

    def test_menu_offers_cleanup_only_for_halfclone(self):
        from gcm.models import RepoSpec

        with tempfile.TemporaryDirectory() as td:
            half = Path(td) / "half"
            (half / ".git").mkdir(parents=True)
            spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b",
                            local_path=str(half))
            panel = self._panel(spec)
            # 需要真实表格行：菜单构建要读第 0/2 列（没有项就提前 return）
            from PyQt6.QtCore import Qt
            from PyQt6.QtWidgets import QTableWidgetItem

            panel.table.setColumnCount(5)
            panel.table.setRowCount(1)
            panel.table.setItem(0, 0, QTableWidgetItem("a/b"))
            status = QTableWidgetItem("失败")
            status.setData(Qt.ItemDataRole.UserRole, "failed")
            panel.table.setItem(0, 2, status)

            menu = panel._build_progress_menu(0)
            titles = [a.text() for a in menu.actions()]
            self.assertIn("删除半成品目录…", titles)

            # 有工作树内容后：不再给删除入口（防止误删真仓库）
            (half / "a.txt").write_text("x", encoding="utf-8")
            menu2 = panel._build_progress_menu(0)
            self.assertNotIn("删除半成品目录…", [a.text() for a in menu2.actions()])

    def test_delete_requires_confirmation_and_deletes_dir(self):
        from gcm.models import RepoSpec
        from PyQt6.QtWidgets import QMessageBox

        with tempfile.TemporaryDirectory() as td:
            half = Path(td) / "half"
            (half / ".git").mkdir(parents=True)
            spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b",
                            local_path=str(half))
            panel = self._panel(spec)

            # 用户点 No → 不删
            with mock.patch("gcm.ui.main_window.QMessageBox") as mb:
                mb.question.return_value = mb.StandardButton.No
                panel._delete_halfcloned_row(0)
            self.assertTrue(half.exists(), "点 No 却删了目录")

            # 用户点 Yes → 删（复用 _safe_rmtree_partial 的保守语义）
            with mock.patch("gcm.ui.main_window.QMessageBox") as mb:
                mb.question.return_value = mb.StandardButton.Yes
                panel._delete_halfcloned_row(0)
            self.assertFalse(half.exists(), "点 Yes 后目录应被删除")


if __name__ == "__main__":
    unittest.main()
