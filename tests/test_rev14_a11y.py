"""Rev14 无障碍守卫：主操作 100% 有名字 + 输入控件按标签推导 + 棘轮计数。

**口径是 `effective_name`（`accessibleName` → `text()` → tooltip）**，不是裸
`accessibleName`：Qt 对按钮/复选/单选默认用 `text()` 当 accessible name，
只数 `accessibleName` 会把"按钮上有字"误报成缺失，基线虚高之后真缺口被淹没。
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

from gcm.ui.a11y import (  # noqa: E402
    MAIN_ACTIONS,
    _clean,
    apply_main_action_names,
    autoname_inputs,
    effective_name,
)


class TestClean(unittest.TestCase):
    def test_strips_accelerator_ellipsis_and_colon(self):
        self.assertEqual(_clean("&载入…"), "载入")
        self.assertEqual(_clean("并发数（1–32）："), "并发数（1–32）")
        self.assertEqual(_clean("  多个   空格  "), "多个 空格")
        self.assertEqual(_clean(None), "")


class TestMainActionNames(unittest.TestCase):
    """14 个主操作必须都有名字与描述 —— 读屏读不出用途等于功能不可用。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])
        from gcm.db.settings import SettingsStore
        from gcm.ui.a11y import is_qt_internal_widget
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        store.save(s)
        cls.win = MainWindow(data_dir=tmp)

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        cls.win.deleteLater()
        cls._app.processEvents()

    def test_every_main_action_is_named(self):
        missing = [n for n in MAIN_ACTIONS if not effective_name(getattr(self.win, n, None))]
        self.assertEqual(missing, [], f"主操作缺名：{missing}")

    def test_every_main_action_has_description(self):
        missing = [n for n, (_, _d) in MAIN_ACTIONS.items()
                   if getattr(self.win, n, None) is not None
                   and not str(getattr(self.win, n).accessibleDescription() or "").strip()]
        self.assertEqual(missing, [], f"主操作缺 accessibleDescription：{missing}")

    def test_names_are_unique(self):
        names = [effective_name(getattr(self.win, n)) for n in MAIN_ACTIONS
                 if getattr(self.win, n, None) is not None]
        dupes = {n for n in names if names.count(n) > 1}
        self.assertEqual(dupes, set(), f"两个控件重名，读屏无法区分：{dupes}")

    def test_apply_is_idempotent_and_keeps_manual_names(self):
        """重复调用不覆盖人工命名（人工命名永远优先于推导）。"""
        self.win.btn_start.setAccessibleName("人工命名")
        try:
            apply_main_action_names(self.win)
            self.assertEqual(self.win.btn_start.accessibleName(), "人工命名")
        finally:
            self.win.btn_start.setAccessibleName("")

    def test_inputs_get_names_from_labels(self):
        """没有可见文本的输入控件必须推导出名字（读屏否则只报「编辑框」）。"""
        spin = self.win.spin_concurrency
        self.assertTrue(str(spin.accessibleName() or "").strip(),
                        "数字框必须从相邻标签推导出名字")
        self.assertIn("并发", spin.accessibleName())

    def test_autoname_never_overwrites(self):
        edit = self.win.progress_filter
        edit.setAccessibleName("人工搜索框")
        try:
            autoname_inputs(self.win)
            self.assertEqual(edit.accessibleName(), "人工搜索框")
        finally:
            edit.setAccessibleName("")


class TestA11yRatchet(unittest.TestCase):
    """计数只降不升（与 `scripts/a11y_audit.py` 共用同一份基线）。"""

    def test_no_regression_against_baseline(self):
        from PyQt6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QLineEdit,
            QPlainTextEdit,
            QPushButton,
            QSpinBox,
            QToolButton,
        )
        app = QApplication.instance() or QApplication([])
        from gcm.db.settings import SettingsStore
        from gcm.ui.a11y import is_qt_internal_widget
        from gcm.ui.main_window import MainWindow

        base_path = ROOT / "scripts" / "a11y_baseline.json"
        self.assertTrue(base_path.exists(), "缺基线文件：先跑 a11y_audit.py --update")
        base = json.loads(base_path.read_text(encoding="utf-8"))

        tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        store.save(s)
        win = MainWindow(data_dir=tmp)
        try:
            total_missing = 0
            for cls in (QPushButton, QToolButton, QLineEdit, QCheckBox,
                        QComboBox, QSpinBox, QPlainTextEdit):
                items = [w for w in win.findChildren(cls)
                         if not is_qt_internal_widget(w)]
                missing = sum(1 for w in items if not effective_name(w))
                total_missing += missing
                allowed = (base.get("by_class", {}).get(cls.__name__) or {}).get(
                    "missing", 0)
                self.assertLessEqual(
                    missing, allowed,
                    f"{cls.__name__} 缺名从 {allowed} 涨到 {missing}："
                    "新控件请补 accessibleName 或走 autoname_inputs")
            self.assertLessEqual(total_missing, base.get("missing_total", 0),
                                 "缺名控件总数上涨（棘轮只降不升）")
            self.assertEqual(base.get("main_actions_missing"), [],
                             "基线里主操作必须全有名字")
        finally:
            win.close()
            win.deleteLater()
            app.processEvents()


if __name__ == "__main__":
    unittest.main()
