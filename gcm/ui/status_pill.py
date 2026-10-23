"""状态"形状编码"（G75）：与字体无关的状态图形 + 表格 pill 绘制。

**为什么不用字符符号**：G74 的实测教训是 `✓ ✕ ⚠ ⊘` 在微软雅黑里缺字形，
离屏渲染与部分 Windows 机器上会变成 tofu 方块，当时只能退回"纯文字着色"。
G75 改用 **QPainter 几何图形**：字形缺不缺都不影响，同时满足色盲可辨
（形状是主编码，颜色是二次编码）。

口径：
- `shape_for()` 是**唯一**的状态→形状映射来源，五类终态 + 运行 + 待处理共 7 种；
- `color_for()` 从当前色板取语义色（不再自己写颜色表）；
- `StatusPillDelegate` 只**绘制**，不改单元格文本/数据 —— 排序、右键、统计仍按
  `UserRole` 里的原始状态值工作（守卫：`test_g74_ui_layout` 断言状态文本保持纯文本）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QBrush, QColor, QPainter, QPen
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查（from __future__ import annotations）
    from PyQt6.QtCore import QSize

# 状态值（与 models.SyncStatus.value 对齐）→ 形状键
_SHAPE_BY_STATUS: dict[str, str] = {
    "success": "disc",
    "failed": "cross",
    "conflict": "split",
    "skipped": "ring",
    "cancelled": "dash",
    "running": "wedge",
    "pending": "hollow_square",
}

# 状态值 → 色板键
_COLOR_KEY_BY_STATUS: dict[str, str] = {
    "success": "success",
    "failed": "error",
    "conflict": "warning",
    "skipped": "text_dim",
    "cancelled": "warning",
    "running": "accent",
    "pending": "text_dim",
}

DEFAULT_SHAPE = "hollow_square"

# 形状 → 便于测试断言的"可区分"标识（同形状 = 色盲不可辨）
SHAPES: tuple[str, ...] = ("disc", "cross", "split", "ring", "dash", "wedge",
                           "hollow_square")


def shape_for(status: str) -> str:
    """状态值 → 形状键（未知状态回退空心方框）。"""
    return _SHAPE_BY_STATUS.get(str(status or "").strip().lower(), DEFAULT_SHAPE)


def color_key_for(status: str) -> str:
    """状态值 → 色板键（未知回退 text_dim）。"""
    return _COLOR_KEY_BY_STATUS.get(str(status or "").strip().lower(), "text_dim")


def color_for(status: str, palette: dict) -> str:
    """状态值 → 当前色板里的颜色（十六进制串）。"""
    key = color_key_for(status)
    return str(palette.get(key) or palette.get("text") or "#888888")


def draw_shape(painter: QPainter, rect: QRect, shape: str, color: QColor) -> None:
    """在 rect 内画形状（不依赖字体字形）。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(QPen(color, 1.4))
    filled = QBrush(color)
    r = rect.adjusted(1, 1, -1, -1)
    cy = r.center().y()
    radius = max(2, min(r.width(), r.height()) // 2)
    if shape == "disc":
        painter.setBrush(filled)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(r.center(), radius, radius)
    elif shape == "ring":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(r.center(), radius, radius)
    elif shape == "split":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(r.center(), radius, radius)
        painter.setBrush(filled)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawChord(r, 90 * 16, 180 * 16)
    elif shape == "cross":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(r.left(), r.top(), r.right(), r.bottom())
        painter.drawLine(r.left(), r.bottom(), r.right(), r.top())
    elif shape == "dash":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawLine(r.left(), cy, r.right(), cy)
    elif shape == "wedge":
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(r, 45 * 16, 270 * 16)
    else:  # hollow_square
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(r, 2, 2)
    painter.restore()


class StatusPillDelegate(QStyledItemDelegate):
    """状态列绘制：圆角 pill（语义底色 + 描边）+ 形状 + 文本。

    只影响绘制，不修改 item 文本/前景色/UserRole —— 因此既有的
    "状态文本保持纯文本、排序按原始值" 两条守卫都不受影响。
    """

    def __init__(self, parent=None, status_of: Optional[object] = None):
        super().__init__(parent)
        # status_of(row_index) -> 原始状态值（默认从 UserRole 读）
        self._status_of = status_of

    @staticmethod
    def _status(index) -> str:
        return str(index.data(Qt.ItemDataRole.UserRole) or "")

    def paint(self, painter: QPainter, option: QStyleOptionViewItem,
              index) -> None:  # noqa: N802
        from .theme import current_palette

        pal = current_palette()
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not text:
            super().paint(painter, option, index)
            return
        status = self._status(index)
        color = QColor(color_for(status, pal))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())

        pill = option.rect.adjusted(6, 4, -6, -4)
        tint = QColor(color)
        tint.setAlpha(38)
        painter.setBrush(QBrush(tint))
        painter.setPen(QPen(color, 1))
        painter.drawRoundedRect(pill, 8, 8)

        icon = QRect(pill.left() + 6, pill.top() + (pill.height() - 12) // 2, 12, 12)
        draw_shape(painter, icon, shape_for(status), color)

        # G75：pill 文字用排版系统的正文族（宽度可预测，pill 才不会抖）
        from .typography import type_font
        font = type_font("body")
        font.setBold(status in ("failed", "conflict"))
        painter.setFont(font)
        painter.setPen(QPen(color))
        text_rect = pill.adjusted(icon.width() + 12, 0, -6, 0)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter
                         | Qt.AlignmentFlag.AlignLeft, text)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index) -> QSize:  # noqa: N802
        size = super().sizeHint(option, index)
        size.setWidth(max(size.width(), 96))
        size.setHeight(max(size.height(), 26))
        return size
