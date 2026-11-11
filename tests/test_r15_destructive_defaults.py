"""Rev15：破坏性确认必须默认 No（关窗 / 删除库记录）。

为什么不是“点两次就行”：Windows 上 `QMessageBox.question` 的默认按钮是第一个
（Yes）。用户按住回车、或键盘焦点落在默认按钮时，会直接执行不可逆动作。
本项目已有 6 处默认 No；本轮补齐关窗与两处删除入口。

测试直接捕获 `QMessageBox.question` 的第 5 个参数（defaultButton），不弹真框。
"""
from __future__ import annotations

import os
import types
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


class TestDestructiveDefaults(unittest.TestCase):
    def _capture_question(self):
        from PyQt6.QtWidgets import QMessageBox

        captured: dict = {}

        def fake_question(*args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return QMessageBox.StandardButton.No

        return captured, fake_question

    def test_close_event_defaults_to_no(self) -> None:
        from PyQt6.QtWidgets import QMessageBox

        from gcm.ui.main_window import MainWindow

        captured, fake_question = self._capture_question()
        owner = types.SimpleNamespace(busy=True)
        event = types.SimpleNamespace(ignored=False)
        event.ignore = lambda: setattr(event, "ignored", True)

        with mock.patch.object(QMessageBox, "question", side_effect=fake_question):
            MainWindow.closeEvent(owner, event)

        self.assertTrue(event.ignored, "默认 No 时关窗必须被忽略")
        self.assertGreaterEqual(len(captured["args"]), 5)
        self.assertEqual(captured["args"][4], QMessageBox.StandardButton.No)

    def test_manage_delete_defaults_to_no(self) -> None:
        from PyQt6.QtWidgets import QMessageBox

        from gcm.ui.manage_panel import ManagePanel

        captured, fake_question = self._capture_question()

        class _Index:
            def row(self) -> int:
                return 0

        class _Table:
            def selectedIndexes(self):
                return [_Index()]

        panel = types.SimpleNamespace(
            owner=types.SimpleNamespace(),
            manage_table=_Table(),
            manage_model=None,
        )

        with mock.patch.object(QMessageBox, "question", side_effect=fake_question):
            ManagePanel.delete_selected(panel)

        self.assertGreaterEqual(len(captured["args"]), 5)
        self.assertEqual(captured["args"][4], QMessageBox.StandardButton.No)


if __name__ == "__main__":
    unittest.main()
