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
        """_apply_layout_for_width 对无 manage_table 的替身不抛异常。"""
        import gcm.ui.main_window as mw
        owner = type("O", (), {})()
        mw.MainWindow._apply_layout_for_width(owner, 600)
        mw.MainWindow._apply_layout_for_width(owner, 1200)


if __name__ == "__main__":
    unittest.main()
