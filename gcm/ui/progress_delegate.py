"""G70 进度列自绘 delegate（从 progress_table.py 拆出，保持单文件 <800 行）。

设计要点：
- 500 行批次下不再为每行创建 QProgressBar（原实现 500 个 QWidget）；
- 进度值在 `ProgressTable._progress_values` 内存字典里（engine index → percent），
  绘制时经列 0 的 `UserRole+1` 反查，排序/过滤后仍指向正确仓库；
- 批量刷新只改字典 + 重绘视口，不逐行 `setData`。
"""
from __future__ import annotations

from PyQt6.QtCore import QRect, QSize, Qt
from PyQt6.QtGui import QBrush, QColor, QFontMetrics, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from .theme import current_palette

# 进度详情（速率/对象数）数据角色
DETAIL_ROLE = Qt.ItemDataRole.UserRole + 3


class ProgressDelegate(QStyledItemDelegate):
    """用 delegate 自绘进度条，移除每行一个 QProgressBar 的控件开销。"""

    def __init__(self, parent=None, values: dict | None = None):
        super().__init__(parent)
        self._values = values if values is not None else {}

    def _percent_for(self, index) -> int:
        try:
            engine_index = index.siblingAtColumn(0).data(
                Qt.ItemDataRole.UserRole + 1)
            return int(self._values.get(int(engine_index), -1))
        except (TypeError, ValueError):
            return -1

    def paint(self, painter, option: QStyleOptionViewItem, index) -> None:  # noqa: N802
        pal = current_palette()
        pct = self._percent_for(index)
        detail = str(index.data(DETAIL_ROLE) or "")
        rect = option.rect.adjusted(4, 6, -4, -6)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
        painter.setPen(QPen(QColor(pal["border"]), 1))
        painter.setBrush(QBrush(QColor(pal["panel2"])))
        painter.drawRoundedRect(rect, 4, 4)
        if pct >= 0:
            width = int(rect.width() * min(100, pct) / 100)
            if width > 0:
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QBrush(QColor(pal["accent"])))
                painter.drawRoundedRect(
                    QRect(rect.x(), rect.y(), max(4, width), rect.height()), 4, 4)
            text = f"{pct}%  {detail}".strip()
        else:
            text = detail or "…"
        painter.setPen(QColor(pal["text"]))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        detail = str(index.data(DETAIL_ROLE) or "")
        fm = QFontMetrics(option.font)
        return QSize(max(150, fm.horizontalAdvance(detail) + 48), 24)
