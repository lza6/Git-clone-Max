"""Rev15：无障碍审计的内部控件过滤必须有牙（不能把真实输入框也过滤掉）。

背景：`scripts/a11y_audit.py` 曾把 `QSpinBox` 内部的 `QLineEdit`、清除按钮、
菜单栏溢出按钮算成「缺名输入框」，基线被抬到 28；真正缺名的 ManagePanel
搜索框反而淹没在噪音里。过滤本身也必须反向自检：如果它把所有 QLineEdit
都当成内部控件，棘轮会变成永远绿。
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestQtInternalWidgetFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_bare_lineedit_is_not_internal(self) -> None:
        from PyQt6.QtWidgets import QLineEdit

        from gcm.ui.a11y import is_qt_internal_widget
        self.assertFalse(is_qt_internal_widget(QLineEdit()))

    def test_spinbox_internal_lineedit_is_internal(self) -> None:
        from PyQt6.QtWidgets import QSpinBox

        from gcm.ui.a11y import is_qt_internal_widget
        spin = QSpinBox()
        edit = spin.lineEdit()
        self.assertIsNotNone(edit)
        self.assertTrue(is_qt_internal_widget(edit))

    def test_lineedit_clear_button_is_internal(self) -> None:
        from PyQt6.QtWidgets import QLineEdit, QToolButton

        from gcm.ui.a11y import is_qt_internal_widget
        edit = QLineEdit()
        clear = QToolButton(edit)
        self.assertTrue(is_qt_internal_widget(clear))

    def test_menubar_ext_button_is_internal(self) -> None:
        from PyQt6.QtWidgets import QToolButton

        from gcm.ui.a11y import is_qt_internal_widget
        btn = QToolButton()
        btn.setObjectName("qt_menubar_ext_button")
        self.assertTrue(is_qt_internal_widget(btn))


if __name__ == "__main__":
    unittest.main()
