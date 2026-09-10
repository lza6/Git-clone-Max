# -*- coding: utf-8 -*-
"""G03-7 历史按钮委托测试：QStyledItemDelegate 在末列绘制可点击「查看」按钮。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtWidgets import QApplication, QStyleOptionButton
_app = QApplication.instance() or QApplication([])


class TestHistoryDelegate(unittest.TestCase):
    def test_delegate_creates_button_rect(self):
        from gcm.ui.manage_model import HistoryButtonDelegate
        dlg = HistoryButtonDelegate()
        opt = QStyleOptionButton()
        rect = dlg.button_rect(QRect(0, 0, 70, 30))
        self.assertTrue(rect.width() > 0)
        self.assertTrue(rect.height() > 0)
        self.assertTrue(rect.center().x() > 0)

    def test_delegate_editor_event_emits_row(self):
        from gcm.ui.manage_model import HistoryButtonDelegate
        dlg = HistoryButtonDelegate()
        clicks = []
        dlg.clicked.connect(clicks.append)
        # 模拟点击：rect 内 → 触发
        from PyQt6.QtCore import QEvent, QPointF
        from PyQt6.QtGui import QMouseEvent
        opt = QStyleOptionButton()
        idx = None  # editorEvent 需要有效 index；用 model 造一个
        from gcm.ui.manage_model import ManageModel
        m = ManageModel([{"folder_name": "a__b", "owner": "a", "repo": "b",
                          "local_path": "/x", "last_sync_at": "", "head_sha": "",
                          "id": 1, "host": "github.com", "url": "u"}])
        model_index = m.index(0, 5)
        r = QRect(0, 0, 70, 30)
        opt = QStyleOptionButton()
        opt.rect = r
        ev = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(r.center()),
                         Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                         Qt.KeyboardModifier.NoModifier)
        handled = dlg.editorEvent(ev, m, opt, model_index)
        self.assertTrue(handled, "点击应被委托消费")
        self.assertEqual(clicks, [0], "应发出第 0 行点击")

    def test_delegate_size_hint(self):
        from gcm.ui.manage_model import HistoryButtonDelegate
        from gcm.ui.manage_model import ManageModel
        dlg = HistoryButtonDelegate()
        m = ManageModel([{"folder_name": "a__b", "owner": "a", "repo": "b",
                          "local_path": "/x", "last_sync_at": "", "head_sha": "",
                          "id": 1, "host": "github.com", "url": "u"}])
        opt = QStyleOptionButton()
        size = dlg.sizeHint(opt, m.index(0, 5))
        self.assertTrue(size.width() > 0 and size.height() > 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)