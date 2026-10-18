"""G53-1 多维评分卡雷达图（纯 Qt 绘制，无第三方图表依赖）。

数据来自 `gcm/app/alerts.py` 的 6 维评分（0-100），由统计中心聚合后传入。
设计取舍：
- 不引入 matplotlib/pyqtgraph：本项目只打包 QtCore/QtGui/QtWidgets，加图表库会
  显著增大 exe，且这里只需要一个简单多边形。
- 颜色走 `theme.current_palette()`，三套主题（deep/light/nord）都能看清。
- 无数据时画空态文案，而不是留一块白底。
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Optional

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PyQt6.QtWidgets import QSizePolicy, QWidget

from ..i18n import tr

# 维度显示名（与 alerts.DIM_LABELS 保持一致；此处复制是为了避免 UI 依赖 app 逻辑）
DEFAULT_LABELS: dict[str, str] = {
    "success_rate": "成功率",
    "freshness": "陈旧度",
    "conflicts": "冲突健康",
    "size_score": "仓库大小",
    "tags_score": "标签完整",
    "recency": "最近同步",
}

_RING_LEVELS = (25, 50, 75, 100)
_MIN_RADIUS = 30


class RadarChart(QWidget):
    """6 维评分雷达图；`set_values()` 后自动重绘。"""

    def __init__(self, parent: Optional[QWidget] = None,
                 labels: Optional[Mapping[str, str]] = None):
        super().__init__(parent)
        self._labels: dict[str, str] = {
            k: tr(v) for k, v in (labels or DEFAULT_LABELS).items()}
        self._values: dict[str, float] = {}
        self._has_data = False
        self.setMinimumSize(300, 230)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,
                           QSizePolicy.Policy.Expanding)
        self.setToolTip(tr("6 维评分（0-100）：成功率 / 陈旧度 / 冲突健康 / 仓库大小 / 标签完整 / 最近同步"))
        self.setAccessibleName(tr("仓库健康雷达图"))
        self.setAccessibleDescription(
            tr("六维评分可视化：成功率、陈旧度、冲突健康、仓库大小、标签完整、最近同步"))

    # ------------------------------------------------------------ 数据
    def set_values(self, values: Optional[Mapping[str, float]]) -> None:
        """设置各维度分值（缺失按 0 处理；超界裁剪到 0-100）。"""
        self._has_data = bool(values)
        out: dict[str, float] = {}
        for key in self._labels:
            try:
                v = float((values or {}).get(key, 0) or 0)
            except (TypeError, ValueError):
                v = 0.0
            out[key] = max(0.0, min(100.0, v))
        self._values = out
        self.update()

    def values(self) -> dict[str, float]:
        return dict(self._values)

    # ------------------------------------------------------------ 绘制
    def _palette(self) -> dict:
        try:
            from .theme import current_palette
            return current_palette()
        except Exception:
            return {"accent": "#58a6ff", "border": "#30363d", "text": "#e6edf3",
                    "text_dim": "#8b949e", "panel": "#161b22"}

    @staticmethod
    def _point(cx: float, cy: float, radius: float, angle: float) -> QPointF:
        return QPointF(cx + radius * math.cos(angle), cy + radius * math.sin(angle))

    def paintEvent(self, event) -> None:  # noqa: N802 —— Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pal = self._palette()
        w, h = self.width(), self.height()
        cx, cy = w / 2.0, h / 2.0
        radius = min(w, h) / 2.0 - 42.0
        if radius < _MIN_RADIUS:
            return
        keys = list(self._labels)
        n = len(keys)
        if n < 3:
            return
        angles = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]

        # 网格：同心多边形 + 轴线
        grid_pen = QPen(QColor(str(pal.get("border", "#30363d"))))
        grid_pen.setWidth(1)
        p.setPen(grid_pen)
        for level in _RING_LEVELS:
            r = radius * level / 100.0
            poly = QPolygonF([self._point(cx, cy, r, a) for a in angles])
            p.drawPolygon(poly)
        for a in angles:
            p.drawLine(QPointF(cx, cy), self._point(cx, cy, radius, a))

        # 数据多边形（没有传入数据时才显示空态；六维全 0 是真实数据）
        if self._has_data:
            accent = QColor(str(pal.get("accent", "#58a6ff")))
            fill = QColor(accent)
            fill.setAlpha(70)
            poly = QPolygonF([
                self._point(cx, cy, radius * self._values.get(k, 0) / 100.0, a)
                for k, a in zip(keys, angles, strict=True)
            ])
            p.setPen(QPen(accent, 2))
            p.setBrush(QBrush(fill))
            p.drawPolygon(poly)
            p.setBrush(QBrush(accent))
            for k, a in zip(keys, angles, strict=True):
                r = radius * self._values.get(k, 0) / 100.0
                p.drawEllipse(self._point(cx, cy, r, a), 3, 3)
        else:
            p.setPen(QPen(QColor(str(pal.get("text_dim", "#8b949e")))))
            p.setFont(QFont(self.font().family(), 9))
            p.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, tr("暂无评分数据"))

        # 维度标签
        p.setPen(QPen(QColor(str(pal.get("text", "#e6edf3")))))
        font = QFont(self.font().family(), 9)
        p.setFont(font)
        for k, a in zip(keys, angles, strict=True):
            pt = self._point(cx, cy, radius + 20, a)
            text = f"{self._labels[k]} {self._values.get(k, 0):.0f}"
            metrics = p.fontMetrics()
            tw = metrics.horizontalAdvance(text)
            th = metrics.height()
            # 按象限调整对齐，避免标签压到图形
            if abs(math.cos(a)) < 0.3:
                x = pt.x() - tw / 2
                y = pt.y() + (th if math.sin(a) < 0 else 0)
            elif math.cos(a) > 0:
                x, y = pt.x(), pt.y() + th / 3
            else:
                x, y = pt.x() - tw, pt.y() + th / 3
            p.drawText(QPointF(x, y), text)
        p.end()
