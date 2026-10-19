"""诊断/体检报告对话框（G54-3 环境体检展示 + G54-5 复制诊断文本 + G71-6 导出诊断包）。

独立模块的原因：`main_window.py` 已 2000+ 行，项目惯例是新 UI 逻辑落独立文件
（既有 `failure_panel` / `onboarding` / `progress_table` / `settings_panel` 先例）。

G71-6 新增「导出诊断包」：把报告 + EventBus 最近事件 + app.log/notify.log 尾部
打成 zip（全部已打码），报障时直接发这一个文件，不必再让用户复制多段文本。
"""
from __future__ import annotations

import time
from pathlib import Path

from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from ..i18n import tr


class DiagReportDialog(QDialog):
    """展示诊断/体检分级报告，并提供「复制诊断文本」与「导出诊断包」。

    为什么要有「复制」：这份报告是**报障**时唯一该贴给别人的东西 ——
    它在 `diag.copy_paste_report()` 里已过 `redact()` 打码（版本/git/系统/目录/
    错误摘要），可安全外发；而原始日志可能含本机路径与凭据。
    """

    def __init__(self, title: str, body: str, copy_text: str = "", parent=None,
                 data_dir: str | Path | None = None, event_bus=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        from .layout import fit_dialog_to_screen
        fit_dialog_to_screen(self, 680, 460, 480, 340)
        self._copy_text = copy_text or body
        self._body = body
        self._data_dir = Path(data_dir) if data_dir else None
        self._event_bus = event_bus

        lay = QVBoxLayout(self)
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setPlainText(body)
        lay.addWidget(view)

        btns = QDialogButtonBox()
        self._btn_copy = QPushButton(tr("复制诊断文本"))
        btns.addButton(self._btn_copy, QDialogButtonBox.ButtonRole.ActionRole)
        self._btn_copy.clicked.connect(self._on_copy)
        self._btn_export = QPushButton(tr("导出诊断包…"))
        self._btn_export.setToolTip(
            tr("导出 zip：报告 + 最近事件 + 日志尾部（全部已打码）"))
        self._btn_export.clicked.connect(self._on_export)
        btns.addButton(self._btn_export, QDialogButtonBox.ButtonRole.ActionRole)
        btns.addButton(QDialogButtonBox.StandardButton.Close)
        btns.rejected.connect(self.reject)
        lay.addWidget(btns)

    def _on_copy(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText(self._copy_text)
            self._btn_copy.setText(tr("已复制 ✓"))

    def _on_export(self) -> None:
        """选择保存位置并生成诊断 zip；失败给出可读提示。"""
        from ..app.diag_bundle import build_diag_bundle, default_bundle_name
        default = str(Path.home() / "Desktop" / default_bundle_name())
        path, _ = QFileDialog.getSaveFileName(
            self, tr("导出诊断包"), default, tr("Zip 压缩包 (*.zip)"))
        if not path:
            return
        if not str(path).lower().endswith(".zip"):
            path = str(path) + ".zip"
        try:
            out = build_diag_bundle(
                path,
                data_dir=self._data_dir or Path.cwd(),
                report_text=self._body,
                event_bus=self._event_bus,
                extra_manifest={"exported_at": time.strftime("%Y-%m-%d %H:%M:%S")},
            )
        except Exception as exc:  # noqa: BLE001 —— 导出失败必须让用户看到原因
            QMessageBox.warning(self, tr("导出失败"), str(exc))
            return
        self._btn_export.setText(tr("已导出 ✓"))
        QMessageBox.information(self, tr("诊断包已导出"), str(out))
