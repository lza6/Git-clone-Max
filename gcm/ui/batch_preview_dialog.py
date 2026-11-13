"""执行前预览差异表（Rev10 P-02）+ 可勾选执行（Rev17 T3）。

B5 交付了 `app/batch_plan`（CLI/MCP 预览），但 GUI 侧只有「开始」前的
无预览直跑。本对话框把同一份 `plan_batch` 的逐行计划以表格呈现 ——
哪几条会克隆 / 更新 / 删除重建（replace）/ 被占 / 重复 / 无效，各自原因。

Rev17 T3 新增勾选列：确认后返回 `selected_rows()`，调用方把未勾选行从
specs 剔除再 `launch()`。

设计约束：
- 只读：本对话框绝不写盘、不联网（`plan_batch` 是纯函数）；
- `replace` 行必须醒目（它会删目录）—— 高亮 + 列内文字直说；
  且**默认不勾选**（破坏性动作默认排除，与 R15-06 关窗默认 No 同一人因原则）；
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

#: 可执行的动作（blocked/duplicate/invalid 不进勾选语义——它们本来就不动盘）
_EXECUTABLE_ACTIONS = {"clone", "update", "replace"}


def _item(text: str) -> QTableWidgetItem:
    it = QTableWidgetItem(str(text or ""))
    it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
    return it


class BatchPreviewDialog(QDialog):
    """「这批会发生什么」差异表（纯本地预检）+ 勾选要执行的行。"""

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

        self.table = QTableWidget(0, 5, self)
        self.table.setHorizontalHeaderLabels(
            [tr("执行"), tr("动作"), tr("地址"), tr("目标目录"), tr("原因")])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 46)
        self.table.setColumnWidth(1, 90)
        self.table.setColumnWidth(2, 300)
        self.table.setColumnWidth(3, 190)
        self.table.setSelectionBehavior(self.table.SelectionBehavior.SelectRows)
        lay.addWidget(self.table, 1)
        lay.addWidget(QLabel(self.plan.to_dict()["limits"]))

        row = QHBoxLayout()
        self.btn_confirm = QPushButton(tr("执行勾选行"))
        self.btn_confirm.setObjectName("btn_preview_confirm")
        self.btn_confirm.clicked.connect(self.accept)
        self.btn_close = QPushButton(tr("关闭"))
        self.btn_close.clicked.connect(self.reject)
        row.addStretch()
        row.addWidget(self.btn_confirm)
        row.addWidget(self.btn_close)
        lay.addLayout(row)
        self.refresh()
        self._sync_confirm_enabled()

    def refresh(self) -> int:
        warn_color = QColor(current_palette().get("error", "#e5534b"))
        self.table.setRowCount(0)
        for r in self.plan.rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            # 勾选列：可执行动作默认全选；replace 默认**不选**；其余行不可勾
            chk = QTableWidgetItem()
            if r.action in _EXECUTABLE_ACTIONS:
                chk.setFlags(Qt.ItemFlag.ItemIsUserCheckable
                             | Qt.ItemFlag.ItemIsEnabled)
                checked = r.action != "replace"
                chk.setCheckState(Qt.CheckState.Checked if checked
                                  else Qt.CheckState.Unchecked)
            else:
                chk.setFlags(Qt.ItemFlag.ItemIsEnabled)  # 无勾选框
                chk.setCheckState(Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, chk)
            it_act = _item(_ACTION_LABELS.get(r.action, r.action))
            if r.action in _WARN_ACTIONS:
                it_act.setForeground(warn_color)
            self.table.setItem(row, 1, it_act)
            self.table.setItem(row, 2, _item(r.url))
            self.table.setItem(row, 3, _item(r.target or r.folder))
            self.table.setItem(row, 4, _item(r.reason))
        self.table.itemChanged.connect(self._sync_confirm_enabled)
        return len(self.plan.rows)

    def _sync_confirm_enabled(self, *_args) -> None:
        """全不选时禁用「执行勾选行」—— 点了等于什么都没选，提示而非空跑。"""
        self.btn_confirm.setEnabled(self.selected_count() > 0)

    def selected_count(self) -> int:
        n = 0
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if (it
                    and bool(it.flags() & Qt.ItemFlag.ItemIsUserCheckable)
                    and it.checkState() == Qt.CheckState.Checked):
                n += 1
        return n

    def selected_rows(self) -> list[dict]:
        """勾选的行（dict 形态与 PlanRow.to_dict 一致）。未勾选的行由调用方剔除。"""
        out: list[dict] = []
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            if (it and (it.flags() & Qt.ItemFlag.ItemIsUserCheckable)
                    and it.checkState() == Qt.CheckState.Checked):
                r = self.plan.rows[i]
                out.append(r.to_dict())
        return out

    def unchecked_executable_urls(self) -> set[str]:
        """可执行但未勾选的 url 集合（调用方用于剔除 specs 与播报）。"""
        out: set[str] = set()
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 0)
            r = self.plan.rows[i]
            if r.action not in _EXECUTABLE_ACTIONS:
                continue
            if not (it and (it.flags() & Qt.ItemFlag.ItemIsUserCheckable)
                    and it.checkState() == Qt.CheckState.Checked):
                out.add(r.url)
        return out
