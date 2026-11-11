"""Rev15 R15-07：管理页窄屏列优先级（1024 隐健康/大小，1100 恢复）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtTest import QTest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from gcm.db.settings import SettingsStore  # noqa: E402
from gcm.ui.layout import COMPACT_WIDTH  # noqa: E402
from gcm.ui.main_window import MainWindow  # noqa: E402


class TestManageColumnPriority(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gcm_r15_manage_")
        self.dir = Path(self._tmp.name)
        store = SettingsStore(self.dir / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        store.save(s)
        self.win = MainWindow(data_dir=self.dir)
        _app.processEvents()

    def tearDown(self) -> None:
        self.win.busy = False
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()
        self._tmp.cleanup()

    def _hidden(self, col: int) -> bool:
        return self.win.manage_table.isColumnHidden(col)

    def test_1024_hides_optional_columns(self) -> None:
        self.win._apply_layout_for_width(1024)
        self.assertTrue(self._hidden(6), "健康列在 1024 应隐藏")
        self.assertTrue(self._hidden(7), "大小列在 1024 应隐藏")

    def test_1100_restores_optional_columns(self) -> None:
        self.win._apply_layout_for_width(COMPACT_WIDTH)
        self.assertFalse(self._hidden(6))
        self.assertFalse(self._hidden(7))

    def test_xs_still_hides_optional_columns(self) -> None:
        self.win._apply_layout_for_width(900)
        self.assertTrue(self._hidden(6))
        self.assertTrue(self._hidden(7))

    def test_narrow_budget_does_not_overwrite_user_width(self) -> None:
        """1024 的隐列/下限不能被防抖写盘当成用户列宽（审查实测的 P1）。"""
        panel = self.win.manage_panel
        self.win.settings.ui_state = {"manage_columns": {"3": 300}}
        self.win.settings_store.save(self.win.settings)
        self.win._apply_layout_for_width(1024)
        # 等真实 400ms 防抖过去：隐藏列/程序化下限都不能写盘。
        QTest.qWait(500)
        saved = SettingsStore(self.dir / "settings.json").load().ui_state.get(
            "manage_columns") or {}
        self.assertEqual(int(saved.get("3", 0)), 300,
                         f"窄窗防抖把用户列宽写成下限：{saved}")
        # 再显式触发一次保存，确认写侧也会跳过隐藏/程序化列。
        panel._save_column_widths()
        saved = SettingsStore(self.dir / "settings.json").load().ui_state.get(
            "manage_columns") or {}
        self.assertEqual(int(saved.get("3", 0)), 300,
                         f"用户列宽被窄窗预算覆盖：{saved}")
        self.win._apply_layout_for_width(1100)
        self.assertEqual(panel.manage_table.columnWidth(3), 300)


if __name__ == "__main__":
    unittest.main()
