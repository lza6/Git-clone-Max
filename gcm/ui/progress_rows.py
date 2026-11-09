"""进度表行构建（Rev11 从 progress_table.py 抽出以守 800 行红线）。

`_add_table_row` 的六格构建：仓库名/进度/状态/本次/详情/耗时。
数据角色约定（全部由调用方 ProgressTable 消费，本模块只写）：
- col 0 UserRole = folder_name（过滤）、UserRole+1 = engine index（G21-1 定位）；
- col 1 DETAIL_ROLE = 速率文本（ProgressDelegate 读）；
- col 2 UserRole = 原始状态值（排序/统计）；
- col 5 = 耗时（mono + 右对齐，§2.4 原则三）。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem

from ..i18n import tr
from .progress_delegate import DETAIL_ROLE
from .result_labels import format_duration as _format_duration
from .theme import current_palette
from .typography import type_font


def build_row_items(spec) -> list[QTableWidgetItem]:
    """一个 spec → 六个单元格 item（顺序 = 列 0..5）。只构建，不插入。"""
    pal = current_palette()
    name_item = QTableWidgetItem(spec.folder_name)
    name_item.setToolTip(spec.url_https)
    name_item.setData(Qt.ItemDataRole.UserRole, spec.folder_name)  # 供过滤
    name_item.setData(Qt.ItemDataRole.UserRole + 1, int(spec.engine_index))  # G21-1
    prog_item = QTableWidgetItem("")
    prog_item.setData(DETAIL_ROLE, "")
    prog_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    status_item = QTableWidgetItem(tr("等待中"))
    status_item.setData(Qt.ItemDataRole.UserRole, "pending")  # 供排序
    status_item.setForeground(QColor(pal["text_dim"]))
    act_item = QTableWidgetItem("—")
    act_item.setForeground(QColor(pal["text_dim"]))
    msg_item = QTableWidgetItem("—")
    msg_item.setForeground(QColor(pal["text_dim"]))
    # V-16：耗时列；数据列走 mono + 右对齐
    dur_item = QTableWidgetItem(_format_duration(0))
    dur_item.setForeground(QColor(pal["text_dim"]))
    dur_item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                              | Qt.AlignmentFlag.AlignVCenter)
    dur_item.setFont(type_font("mono"))
    return [name_item, prog_item, status_item, act_item, msg_item, dur_item]


def install_row(table: QTableWidget, index: int, spec) -> None:
    """把六个 item 写入表第 index 行（engine index = 行号的约定在调用方保证）。"""
    spec.engine_index = index
    for col, item in enumerate(build_row_items(spec)):
        table.setItem(index, col, item)


__all__ = ["build_row_items", "install_row"]
