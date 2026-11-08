"""Rev11-C R-01：批次结论条就绪态接线守卫（悬空 4 轮后收口）。

历史：`set_ready` 实现完整但零生产调用点（三份文档反复登记未接）；
`main_window.py` 还有一条指向不存在方法 `_conclusion_from_overview` 的假注释。
本文件钉死：接线必须存在 + 假注释不得复活 + 三态状态机贯通。
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestReadyWiringStatic(unittest.TestCase):
    """AST 守卫（不建控件，零 Qt 依赖）。"""

    def test_set_ready_called_in_start_all(self):
        src = (ROOT / "gcm" / "ui" / "main_window.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "start_all":
                calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
                attrs = {c.func.attr for c in calls
                         if isinstance(c.func, ast.Attribute)}
                names = {c.func.id for c in calls
                         if isinstance(c.func, ast.Name)}
                self.assertIn("set_ready", attrs,
                              "start_all 必须调 set_ready（R-01 就绪清单）")
                self.assertIn("plan_batch", attrs | names,
                              "就绪数据必须来自 plan_batch（与 CLI/MCP 同源）")
                return
        self.fail("找不到 start_all")

    def test_fake_comment_not_back(self):
        src = (ROOT / "gcm" / "ui" / "main_window.py").read_text(encoding="utf-8")
        self.assertNotIn("_conclusion_from_overview", src,
                         "该方法不存在——注释要么删要么改成真实来源")


class TestReadyStateBehavior(unittest.TestCase):
    """行为验证（复刻 test_ui_compact_visibility 的真窗模式）。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def setUp(self):
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        self._tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(self._tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        store.save(s)
        self.win = MainWindow(data_dir=self._tmp)
        self.win.show()

    def tearDown(self):
        try:
            self.win.close()
        finally:
            self.win.deleteLater()

    def test_ready_state_renders_counts(self):
        c = self.win.batch_conclusion
        c.set_ready(clone=3, update=2, protected=1, blocked=1)
        self.assertEqual(c.state(), "idle")
        self.assertIn("7", c.metric_lbl.text())
        self.assertIn("3", c.detail_lbl.text())
        self.assertIn("2", c.detail_lbl.text())
        self.assertIn("1", c.detail_lbl.text())

    def test_transition_ready_to_running_to_finished(self):
        c = self.win.batch_conclusion
        c.set_ready(clone=1, update=0)
        self.assertEqual(c.state(), "idle")
        c.begin(1)
        self.assertEqual(c.state(), "running")
        c.note_result("success")
        c.finish()
        self.assertEqual(c.state(), "finished")


if __name__ == "__main__":
    unittest.main()


class TestButtonWeights(unittest.TestCase):
    """Rev11-C P-01：权重映射与 QSS 规则必须成对存在（防止「设了 objectName 但没样式」）。"""

    def test_every_weight_has_qss_rule(self):
        from gcm.ui.button_weights import ACTION_WEIGHTS
        from gcm.ui.qss_build import qss_for_theme
        qss = qss_for_theme("dark")
        for weight in set(ACTION_WEIGHTS.values()):
            self.assertIn(f"QPushButton#{weight}", qss,
                          f"权重 {weight} 在 QSS 里没有对应规则 → 按钮会没有样式")

    def test_apply_weights_is_idempotent_and_skips_missing(self):
        from gcm.ui.button_weights import apply_weights

        class _Btn:
            def __init__(self):
                self.name = ""
            def setObjectName(self, v):
                self.name = v

        class _Owner:
            pass

        owner = _Owner()
        owner.btn_refresh = _Btn()
        n = apply_weights(owner)
        self.assertEqual(owner.btn_refresh.name, "secondary")
        self.assertEqual(n, 1, "只对存在的属性赋值，缺失的静默跳过")
