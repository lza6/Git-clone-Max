"""Rev15 R15-08：触摸屏主操作 44px；桌面鼠标场景不强制拉高。"""
from __future__ import annotations

import types
import unittest
from unittest import mock


class _VH:
    def __init__(self) -> None:
        self.value = 26

    def defaultSectionSize(self) -> int:
        return self.value

    def setDefaultSectionSize(self, value: int) -> None:
        self.value = int(value)


class _Table:
    def __init__(self) -> None:
        self.vh = _VH()

    def verticalHeader(self):
        return self.vh


class _Btn:
    def __init__(self) -> None:
        self.h = 0

    def setMinimumHeight(self, value: int) -> None:
        self.h = int(value)


class TestTouchDensity(unittest.TestCase):
    def _owner(self):
        table = _Table()
        owner = types.SimpleNamespace(
            progress_panel=types.SimpleNamespace(table=table),
            btn_start=_Btn(), btn_cancel=_Btn(), btn_pause_batch=_Btn(),
            btn_clear=_Btn(), btn_open=_Btn(), btn_failures=_Btn(),
            btn_retry_queue=_Btn(), btn_diag_bundle=_Btn(),
        )
        return owner, table

    def test_touch_screen_sets_44px(self) -> None:
        from gcm.ui.main_window import MainWindow
        owner, table = self._owner()
        with mock.patch("gcm.ui.layout.has_touchscreen", return_value=True):
            MainWindow._apply_wide_layout(owner, False)
        self.assertGreaterEqual(owner.btn_start.h, 44)
        self.assertGreaterEqual(owner.btn_open.h, 44)
        self.assertEqual(table.vh.value, 34)

    def test_desktop_keeps_38px(self) -> None:
        from gcm.ui.main_window import MainWindow
        owner, table = self._owner()
        with mock.patch("gcm.ui.layout.has_touchscreen", return_value=False):
            MainWindow._apply_wide_layout(owner, True)
        self.assertEqual(owner.btn_start.h, 38)
        self.assertEqual(table.vh.value, 28)


if __name__ == "__main__":
    unittest.main()
