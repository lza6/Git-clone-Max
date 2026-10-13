"""G57 响应式与无障碍测试：窗口语义档 / DPI 字号联动。"""
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from gcm.ui.theme import clamp_scale_for_dpi


class TestDpiScale(unittest.TestCase):
    def test_low_dpi_keeps_scale(self):
        self.assertEqual(clamp_scale_for_dpi(0.8, 100.0), 0.8)

    def test_high_dpi_floor_to_1(self):
        self.assertEqual(clamp_scale_for_dpi(0.8, 150.0), 1.0)
        self.assertEqual(clamp_scale_for_dpi(0.9, 200.0), 1.0)

    def test_high_dpi_keeps_larger(self):
        self.assertEqual(clamp_scale_for_dpi(1.2, 200.0), 1.2)

    def test_unknown_dpi_keeps(self):
        self.assertEqual(clamp_scale_for_dpi(0.8, 0.0), 0.8)

    def test_bad_input(self):
        self.assertEqual(clamp_scale_for_dpi("x", 100.0), 1.0)


class TestWindowLayoutBand(unittest.TestCase):
    def test_layout_applies_without_window(self):
        """_apply_layout_for_width 对无 manage_table 的替身不抛异常（容错守卫）。

        该替身**没有** manage_table 属性，真实契约是 `hasattr` 守卫生效后静默返回，
        而不是抛 AttributeError（真实场景：窗口尚未构建完表格时 resizeEvent 触发）。
        """
        import gcm.ui.main_window as mw
        owner = type("O", (), {})()
        self.assertFalse(hasattr(owner, "manage_table"))
        # 不得抛异常，且不得给替身凭空补上 manage_table
        self.assertIsNone(mw.MainWindow._apply_layout_for_width(owner, 600))
        self.assertIsNone(mw.MainWindow._apply_layout_for_width(owner, 1200))
        self.assertFalse(hasattr(owner, "manage_table"))

    def test_narrow_hides_secondary_columns(self):
        """窄窗（<768）必须隐藏次要列；宽窗必须全部恢复显示。"""
        import gcm.ui.main_window as mw
        from PyQt6.QtWidgets import QTableWidget
        table = QTableWidget(0, 4)
        table.setHorizontalHeaderLabels(["地址", "大小", "健康", "标签"])
        owner = type("O", (), {})()
        owner.manage_table = table

        mw.MainWindow._apply_layout_for_width(owner, 600)
        self.assertTrue(table.isColumnHidden(1), "窄窗应隐藏『大小』")
        self.assertTrue(table.isColumnHidden(2), "窄窗应隐藏『健康』")
        self.assertTrue(table.isColumnHidden(3), "窄窗应隐藏『标签』")
        self.assertFalse(table.isColumnHidden(0), "主列『地址』不得隐藏")

        mw.MainWindow._apply_layout_for_width(owner, 1200)
        for col in range(4):
            self.assertFalse(table.isColumnHidden(col),
                             f"宽窗应恢复显示第 {col} 列")


if __name__ == "__main__":
    unittest.main()
