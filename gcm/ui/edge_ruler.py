"""左缘刻度尺（Rev11 V-11）—— 表格最左 3px 竖带，按行状态着色。

**设计意图**（§2.8 记忆点 2）：几百行滚动时，用余光感知进度分布
（「前面全是绿、中间几处红」）。它不是装饰：
- 状态色是**低饱和**（alpha 130），不与 pill 抢语义（色彩预算 §2.3）；
- 每 10 行一个 1px 刻度缺口，滚动时提供位置参照；
- 灰度下仍可辨（亮度差而非色相差 —— 状态色按明度分级绘制）。

**实现口径**：挂在第 0 列（仓库列）左侧的 delegate 叠画，不改单元格文本/数据
（与 StatusPillDelegate 同一约束：排序/右键/统计仍按 UserRole 工作）。
状态值读法与 status_pill 一致（UserRole 原始值）。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QStyledItemDelegate, QStyleOptionViewItem

# 每隔多少行画一个刻度缺口
TICK_EVERY = 10
# 竖带宽度（逻辑像素）
RULER_WIDTH = 3


def status_color(status: str, palette: dict) -> Optional[QColor]:
    """行状态 → 低饱和竖带色（未知/空 → None，不画）。"""
    from .status_pill import color_key_for
    key = color_key_for(str(status or "").strip().lower())
    if key == "text_dim":  # pending/skipped/unknown：不画（留白也是信息）
        return None
    c = QColor(str(palette.get(key) or ""))
    if not c.isValid():
        return None
    c.setAlpha(130)
    return c


class EdgeRulerDelegate(QStyledItemDelegate):
    """第 0 列装饰层：先画 3px 状态带，再交给默认绘制（文本不受影响）。"""

    def __init__(self, parent=None, status_of=None):
        super().__init__(parent)
        # status_of(row_index) -> 原始状态值；默认读 UserRole
        self._status_of = status_of

    @staticmethod
    def _status(index) -> str:
        return str(index.siblingAtColumn(2).data(Qt.ItemDataRole.UserRole) or "")

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index) -> None:  # noqa: N802
        from .theme import current_palette
        pal = current_palette()
        color = status_color(self._status(index), pal)
        if color is not None:
            painter.save()
            band = QRect(option.rect.left(), option.rect.top(),
                         RULER_WIDTH, option.rect.height())
            painter.fillRect(band, color)
            painter.restore()
        # 每 10 行一个刻度缺口（1px 横线，画在右缘；用 dim 色，与状态带错开位置）
        # QModelIndex.row() 不抛（非法索引返回 -1）—— 无需 try
        row = index.row()
        if row > 0 and row % TICK_EVERY == 0:
            painter.save()
            tick = QColor(str(pal.get("text_dim") or "#9c978e"))
            tick.setAlpha(90)
            painter.setPen(QPen(tick, 1))
            y = option.rect.top()
            painter.drawLine(option.rect.left(), y,
                             option.rect.left() + RULER_WIDTH + 3, y)
            painter.restore()
        super().paint(painter, option, index)


__all__ = ["EdgeRulerDelegate", "RULER_WIDTH", "TICK_EVERY", "status_color"]
