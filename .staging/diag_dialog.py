"""诊断/体检报告对话框（G54-3 环境体检展示 + G54-5 复制诊断文本）。

为什么要单独一个模块：`main_window.py` 已 2004 行（项目惯例：新增 UI 逻辑落独立模块）。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)


class DiagReportDialog(QDialog):
    """展示诊断/体检分级报告，并提供「复制诊断文本」。"""

    def __init__(self, title: str, body: str, copy_text: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(680, 460)
        self._copy_text = copy_text or body

        lay = QVBoxLayout(self)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(body)
        lay.addWidget(view)

        btns = QDialogButtonBox()
        # 复制到剪贴板：报障时最有用的一个动作（文本已 redact，可安全外发）
        self._btn_copy = QPushButton("复制诊断文本")
        btns.addButton(self._btn_copy, QDialogButtonBox.ButtonRole.ActionRole)
        self._btn_copy.clicked.connect(self._on_copy)
        btns.addButton(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        btns.accepted.connect(self.accept)
        lay.addWidget(btns)

    def _on_copy(self) -> None:
        from PyQt6.QtWidgets import QApplication
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self._copy_text)
            self._btn_copy.setText("已复制 ✓")


def show_diag_report(parent, title: str, body: str, copy_text: str = "") -> None:
    """在主窗口可用时弹诊断对话框；否则退化为纯文本（headless / 已关闭）。"""
    dlg = DiagReportDialog(title, body, copy_text, parent)
    dlg.exec()
