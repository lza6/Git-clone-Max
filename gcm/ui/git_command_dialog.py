"""git 命令查看对话框（Rev10 G-02）。

入口：失败汇总对话框「查看 git 命令…」按钮 —— 用户问「这条仓库的 git
到底执行了什么、说了什么」时的唯五回看入口（此前日志 200 行即丢）。
数据源：`app/git_command_log` 环形缓冲（进程内、500 条、已打码）。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..app import git_command_log
from ..i18n import tr
from .layout import fit_dialog_to_screen
from .theme import apply_parent_theme


def _item(text: str) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text or ""))
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it


class GitCommandDialog(QDialog):
    """最近 git 命令回看（可按仓库过滤）。"""

    def __init__(self, owner: Optional[QWidget] = None,
                 folder_name: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        apply_parent_theme(self)
        self._folder = str(folder_name or "")
        self.setWindowTitle(tr("git 命令记录"))
        fit_dialog_to_screen(self, 820, 620, 560, 380)

        lay = QVBoxLayout(self)
        scope = (tr("仓库：{name}").format(name=self._folder)
                 if self._folder else tr("全部仓库（最近 500 条）"))
        lay.addWidget(QLabel(scope))
        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels([
            tr("时间"), tr("仓库"), tr("命令"), tr("退出码"), tr("耗时 ms")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 140)
        self.table.setColumnWidth(1, 160)
        self.table.setColumnWidth(2, 220)
        self.table.setColumnWidth(3, 70)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        self.table.itemDoubleClicked.connect(self._show_detail)
        lay.addWidget(self.table, 1)
        hint = QLabel(tr("双击一行查看完整输出（最近 50 行，已脱敏）"))
        lay.addWidget(hint)
        row = QHBoxLayout()
        self.btn_refresh = QPushButton(tr("刷新"))
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_close = QPushButton(tr("关闭"))
        self.btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_refresh)
        row.addStretch()
        row.addWidget(self.btn_close)
        lay.addLayout(row)
        self.refresh()

    def refresh(self) -> int:
        recs = (git_command_log.for_repo(self._folder, 200)
                if self._folder else git_command_log.recent(200))
        self.table.setRowCount(0)
        for r in recs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, _item(r.ended_at))
            self.table.setItem(row, 1, _item(r.folder_name))
            self.table.setItem(row, 2, _item(r.argv))
            it_rc = _item(r.returncode)
            if r.returncode != 0:
                # 失败行标红前景（读 theme 当前色板）
                from PyQt6.QtGui import QColor

                from .theme import current_palette
                it_rc.setForeground(QColor(current_palette().get("error", "#e5534b")))
            self.table.setItem(row, 3, it_rc)
            self.table.setItem(row, 4, _item(r.duration_ms))
        return len(recs)

    def _show_detail(self, item: QTableWidgetItem) -> None:
        row = item.row()
        recs = (git_command_log.for_repo(self._folder, 200)
                if self._folder else git_command_log.recent(200))
        if row >= len(recs):
            return
        r = recs[row]
        from PyQt6.QtWidgets import QDialogButtonBox, QTextEdit
        dlg = QDialog(self)
        apply_parent_theme(dlg)
        dlg.setWindowTitle(tr("{argv}（退出码 {rc}）").format(
            argv=r.argv, rc=r.returncode))
        dlg.resize(760, 520)
        lay = QVBoxLayout(dlg)
        view = QTextEdit(dlg)
        view.setReadOnly(True)
        view.setPlainText("\n".join(r.tail) or tr("（无输出）"))
        lay.addWidget(view)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, dlg)
        bb.rejected.connect(dlg.reject)
        bb.accepted.connect(dlg.accept)
        lay.addWidget(bb)
        dlg.exec()
