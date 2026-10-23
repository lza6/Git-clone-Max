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
from .typography import type_font

# 进度详情（速率/对象数）数据角色
DETAIL_ROLE = Qt.ItemDataRole.UserRole + 3

# 刻度：在 25% / 50% / 75% 处画短竖线（仪表读数感；也让人不读数字就能估进度）
TICK_STEPS = 4


def tick_positions(rect: QRect, steps: int = TICK_STEPS) -> list[int]:
    """返回刻度线的 x 坐标（不含 0% 与 100% 端点；steps<=1 时为空）。

    抽成纯函数是为了能单测：`QPaintEvent` 里的像素肉眼难验，坐标可以。
    """
    try:
        n = int(steps)
    except (TypeError, ValueError):
        return []
    if n <= 1 or rect.width() <= 0:
        return []
    return [rect.x() + rect.width() * i // n for i in range(1, n)]



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
        # G75：25/50/75% 刻度。填充色只有"进度感"，刻度才给出"还剩多少"的参照，
        # 且不需要用户去读数字。半透明 dim 色画在填充之上，不遮挡百分比文本。
        tick_color = QColor(pal["text_dim"])
        tick_color.setAlphaF(0.45)
        painter.setPen(QPen(tick_color, 1))
        for x in tick_positions(rect):
            painter.drawLine(x, rect.top() + 3, x, rect.bottom() - 3)
        # G75：百分比/速率用等宽族（数字等宽才对齐），字体来自排版系统
        painter.setFont(type_font("mono"))
        painter.setPen(QColor(pal["text"]))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        detail = str(index.data(DETAIL_ROLE) or "")
        fm = QFontMetrics(option.font)
        return QSize(max(150, fm.horizontalAdvance(detail) + 48), 24)
