"""Rev17 T3：预览对话框可勾选执行守卫。

契约（计划书 T3）：
- 勾选列默认全选，`replace` 行默认**不选**（破坏性动作默认排除）；
- blocked/duplicate/invalid 行不可勾（本来就不动盘）；
- 全不选 → 「执行勾选行」按钮禁用；
- `selected_rows()` / `unchecked_executable_urls()` 与 plan_batch 同源。
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


def _mk_dialog(text: str):
    from gcm.ui.batch_preview_dialog import BatchPreviewDialog
    return BatchPreviewDialog(text, root="", parent=None)


class TestPreviewSelection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _row(self, dlg, i, col=0):
        return dlg.table.item(i, col)

    def test_default_all_selected_replace_unchecked(self):
        """clone/update 默认勾选；replace 默认不选。"""
        dlg = _mk_dialog(
            "https://github.com/octocat/Hello-World\n"
            "https://github.com/octocat/Hello-World\n")  # 第二条触发 replace
        dlg.deleteLater()
        # 简化：纯 clone 场景默认全选
        dlg2 = _mk_dialog("https://github.com/vercel-labs/skills")
        self.assertEqual(dlg2.selected_count(),
                         sum(1 for r in dlg2.plan.rows
                             if r.action in ("clone", "update", "replace")))
        for r in dlg2.plan.rows:
            self.assertIn(r.action, ("clone", "update"))
        dlg2.deleteLater()

    def test_replace_row_defaults_unchecked(self):
        """构造 replace：本地已有目录 + 无 local_path 的 URL → 计划为 replace。"""
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp())
        (root / "octocat__Hello-World").mkdir()
        dlg = _mk_dialog("https://github.com/octocat/Hello-World")
        # root 为空串 → plan_batch 用不了本地目录；改为直接传 root
        from gcm.ui.batch_preview_dialog import BatchPreviewDialog as B
        dlg.close(); dlg.deleteLater()
        dlg3 = B("https://github.com/octocat/Hello-World", str(root), None)
        rows = dlg3.plan.rows
        self.assertTrue(any(r.action == "replace" for r in rows),
                        f"预期能构造出 replace 行，实际 {[r.action for r in rows]}")
        for i, r in enumerate(rows):
            if r.action == "replace":
                self.assertEqual(
                    self._row(dlg3, i).checkState(), Qt.CheckState.Unchecked,
                    "replace 行默认必须不勾选（破坏性动作默认排除）")
        dlg3.deleteLater()

    def test_non_executable_rows_not_checkable(self):
        """invalid 行无勾选框。"""
        dlg = _mk_dialog("这不是一个地址")
        rows = dlg.plan.rows
        self.assertTrue(rows, "应至少有 invalid 行")
        for i, r in enumerate(rows):
            if r.action in ("invalid", "duplicate", "blocked"):
                self.assertFalse(
                    self._row(dlg, i).flags() & Qt.ItemFlag.ItemIsUserCheckable,
                    f"{r.action} 行不应可勾选")
        dlg.deleteLater()

    def test_selected_rows_matches_plan_to_dict(self):
        dlg = _mk_dialog("https://github.com/vercel-labs/skills")
        sel = dlg.selected_rows()
        self.assertEqual(len(sel), len(dlg.plan.rows))
        self.assertEqual(sel[0]["url"], dlg.plan.rows[0].url)
        self.assertIn("action", sel[0])
        dlg.deleteLater()

    def test_unchecked_executable_urls(self):
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp())
        (root / "octocat__Hello-World").mkdir()
        from gcm.ui.batch_preview_dialog import BatchPreviewDialog as B
        dlg = B("https://github.com/octocat/Hello-World", str(root), None)
        urls = dlg.unchecked_executable_urls()
        expected = {dlg.plan.rows[0].url}
        self.assertEqual(urls, expected,
                         "replace 默认不选 → 它出现在未勾选集合里")
        dlg.deleteLater()

    def test_all_unchecked_disables_confirm(self):
        import tempfile
        from pathlib import Path
        root = Path(tempfile.mkdtemp())
        (root / "octocat__Hello-World").mkdir()
        from gcm.ui.batch_preview_dialog import BatchPreviewDialog as B
        dlg = B("https://github.com/octocat/Hello-World", str(root), None)
        # 唯一可执行行是 replace 且默认不选 → 按钮应禁用
        self.assertFalse(dlg.btn_confirm.isEnabled(),
                         "无可勾选行时「执行勾选行」应禁用")
        dlg.deleteLater()


if __name__ == "__main__":
    unittest.main()
