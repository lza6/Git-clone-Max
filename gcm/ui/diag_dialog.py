"""诊断/体检报告对话框（G54-3 环境体检展示 + G54-5 复制诊断文本）。

独立模块的原因：`main_window.py` 已 2000+ 行，项目惯例是新 UI 逻辑落独立文件
（既有 `failure_panel` / `onboarding` / `progress_table` / `settings_panel` 先例）。
"""
from __future__ import annotations

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..i18n import tr


class DiagReportDialog(QDialog):
    """展示诊断/体检分级报告，并提供「复制诊断文本」。

    为什么要有「复制」：这份报告是**报障**时唯一该贴给别人的东西 ——
    它在 `diag.copy_paste_report()` 里已过 `redact()` 打码（版本/git/系统/目录/
    错误摘要），可安全外发；而原始日志可能含本机路径与凭据。
    """

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
        self._btn_copy = QPushButton(tr("复制诊断文本"))
        btns.addButton(self._btn_copy, QDialogButtonBox.ButtonRole.ActionRole)
        self._btn_copy.clicked.connect(self._on_copy)
        btns.addButton(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _on_copy(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self._copy_text)
            self._btn_copy.setText(tr("已复制 ✓"))
