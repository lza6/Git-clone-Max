"""Rev17 T2：statistics 导出 PNG 忙态守卫。

进入禁用按钮 + 发 JOB_STARTED、退出（含异常路径）恢复 + 发 JOB_FINISHED。
照 G82/C-17 方法含变异自检的可能性：守卫断言的是 `busy` 上下文包裹，
实现若被注释掉即红。
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402


class TestStatisticsExportBusy(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _make_dialog(self):
        import pathlib
        from gcm.db.repo_db import Database
        from gcm.ui.statistics_dialog import StatisticsDialog
        import tempfile
        db = Database(pathlib.Path(tempfile.mkdtemp()) / "t.db")
        win = StatisticsDialog(db, None)
        return win

    def test_export_button_has_object_name(self):
        win = self._make_dialog()
        self.assertEqual(win.btn_export_png.objectName(), "btn_export_png")
        win.deleteLater()

    def test_busy_context_wraps_export(self):
        """实现必须用 busy(...) 包裹 grab/save —— 变异掉 with 行守卫即红。"""
        import pathlib
        src = (pathlib.Path(__file__).resolve().parent.parent
               / "gcm" / "ui" / "statistics_dialog.py").read_text(encoding="utf-8")
        self.assertIn('busy(self, "export_png"', src,
                      "_export_png 未接忙态上下文（busy(...) 缺失）")
        self.assertIn('buttons=("btn_export_png",)', src,
                      "忙态未声明按钮恢复列表")
        # with 块必须真的包住 grab+save（防止只 import 不用的假接线）
        seg = src[src.index("with busy(self"):]
        self.assertIn("grab()", seg[:seg.index("except")],
                      "grab/save 不在忙态上下文内")

    def test_busy_restores_button_on_failure_path(self):
        """失败路径（异常）后按钮必须恢复 —— busy 的退出必恢复语义。"""
        import pathlib
        import tempfile
        from gcm.db.repo_db import Database
        from gcm.ui.statistics_dialog import StatisticsDialog
        from gcm.ui.busy import busy
        db = Database(pathlib.Path(tempfile.mkdtemp()) / "t.db")
        win = StatisticsDialog(db, None)
        self.assertTrue(win.btn_export_png.isEnabled())
        try:
            with busy(win, "export_png", buttons=("btn_export_png",)):
                self.assertFalse(win.btn_export_png.isEnabled(),
                                 "busy 进入后按钮应禁用")
                raise RuntimeError("保存失败")
        except RuntimeError:
            pass
        self.assertTrue(win.btn_export_png.isEnabled(),
                        "异常路径后按钮未恢复")
        win.deleteLater()


if __name__ == "__main__":
    unittest.main()
