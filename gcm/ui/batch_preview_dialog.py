"""执行前预览差异表（Rev10 P-02）。

B5 交付了 `app/batch_plan`（CLI/MCP 预览），但 GUI 侧只有「开始」前的
无预览直跑。本对话框把同一份 `plan_batch` 的逐行计划以表格呈现 ——
哪几条会克隆 / 更新 / 删除重建（replace）/ 被占 / 重复 / 无效，各自原因。

设计约束：
- 只读：本对话框绝不写盘、不联网（`plan_batch` 是纯函数）；
- `replace` 行必须醒目（它会删目录）—— 高亮 + 列内文字直说；
- 与 CLI/MCP 用**同一实现**（`plan_batch`），不写第二份判定。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..app.batch_plan import plan_batch
from ..i18n import tr
from .layout import fit_dialog_to_screen
from .theme import apply_parent_theme, current_palette

#: 动作 → 用户可读标签
_ACTION_LABELS = {
    "clone": tr("将克隆"),
    "update": tr("将更新"),
    "replace": tr("删除重建"),
    "blocked": tr("目录被占"),
    "duplicate": tr("重复"),
    "invalid": tr("无效"),
}

#: 需要注意的动作（这些行给警告色）
_WARN_ACTIONS = {"replace", "blocked", "invalid"}


def _item(text: str) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text or ""))
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it


class BatchPreviewDialog(QDialog):
    """「这批会发生什么」差异表（纯本地预检）。"""

    def __init__(self, text: str, root: str,
                 parent: Optional[QDialog] = None):
        super().__init__(parent)
        apply_parent_theme(self)
        self.plan = plan_batch(str(text or ""), str(root or ""))
        self.setWindowTitle(tr("执行前预览（不联网、不写盘）"))
        fit_dialog_to_screen(self, 860, 640, 600, 400)

        lay = QVBoxLayout(self)
        c = self.plan.counts
        lay.addWidget(QLabel(
            tr("输入 {n} 条：将克隆 {clone} · 将更新 {update} · 删除重建 {replace}"
               " · 目录被占 {blocked} · 重复 {dup} · 无效 {invalid}")
            .format(n=len(self.plan.rows), clone=c.get("clone", 0),
                    update=c.get("update", 0), replace=c.get("replace", 0),
                    blocked=c.get("blocked", 0), dup=c.get("duplicate", 0),
                    invalid=c.get("invalid", 0))))
        lay.addWidget(QLabel(tr("目标目录：{root}").format(root=self.plan.root)))

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(
            [tr("动作"), tr("地址"), tr("目标目录"), tr("原因")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(1, 320)
        self.table.setColumnWidth(2, 200)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        lay.addWidget(self.table, 1)
        lay.addWidget(QLabel(self.plan.to_dict()["limits"]))

        row = QHBoxLayout()
        self.btn_close = QPushButton(tr("关闭"))
        self.btn_close.clicked.connect(self.accept)
        row.addStretch()
        row.addWidget(self.btn_close)
        lay.addLayout(row)
        self.refresh()

    def refresh(self) -> int:
        warn_color = QColor(current_palette().get("error", "#e5534b"))
        self.table.setRowCount(0)
        for r in self.plan.rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            it_act = _item(_ACTION_LABELS.get(r.action, r.action))
            if r.action in _WARN_ACTIONS:
                it_act.setForeground(warn_color)
            self.table.setItem(row, 0, it_act)
            self.table.setItem(row, 1, _item(r.url))
            self.table.setItem(row, 2, _item(r.target or r.folder))
            self.table.setItem(row, 3, _item(r.reason))
        return len(self.plan.rows)
