"""吞吐 sparkline（Rev8 C6-3）：齿条旁的 24h 迷你趋势。

设计约束：
- **纯内存 ring buffer**（`ThrottleRing`，容量 288 = 24h × 5 分钟粒度），无 DB、
  无网络、无第三方依赖；进程退出即丢（齿条本来就是"运行态"，历史审计走事件落盘）。
- 绘制只在 `paintEvent`，颜色读 `theme.current_palette()`（G75 视觉分层铁则）。
- `downsample()` 是纯函数（测试不依赖时钟）。

数据源：`main_window` 每分钟把"本分钟完成数"推一次（`push_per_minute` 由
既有 result 槽聚合，零引擎契约变更）。
"""
from __future__ import annotations

from collections import deque

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

BUCKET_SECONDS = 300          # 5 分钟一桶
CAPACITY = 288                # 288 × 5min = 24h


class ThrottleRing:
    """定长 ring buffer（纯数据结构，可独立测试）。"""

    def __init__(self, capacity: int = CAPACITY):
        self._buf: deque[float] = deque(maxlen=max(1, int(capacity)))

    def push(self, v: float) -> None:
        self._buf.append(float(v))

    @property
    def values(self) -> list[float]:
        return list(self._buf)

    @property
    def size(self) -> int:
        return len(self._buf)


def downsample(values: list[float], buckets: int) -> list[float]:
    """把 values 均分成 buckets 桶取均值（桶可为空 → 0）。纯函数。"""
    n = len(values)
    if buckets <= 0 or n == 0:
        return []
    if n <= buckets:
        return list(values)
    out: list[float] = []
    step = n / buckets
    for i in range(buckets):
        a = int(i * step)
        b = max(a + 1, int((i + 1) * step))
        chunk = values[a:b]
        out.append(sum(chunk) / len(chunk) if chunk else 0.0)
    return out


class Sparkline(QWidget):
    """迷你折线图（约 120×28）。push() 追加一个点，paintEvent 画线。"""

    def __init__(self, parent=None, capacity: int = CAPACITY):
        super().__init__(parent)
        self.ring = ThrottleRing(capacity)
        self.setFixedSize(120, 28)
        self.setToolTip("最近吞吐趋势（5 分钟/点）")

    def push(self, v: float) -> None:
        self.ring.push(v)
        self.update()  # 触发重绘（事件循环空闲时执行）

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt 命名
        try:
            from .theme import current_palette
            pal = current_palette()
            line = QColor(pal.get("accent", "#4cc2ff"))
            dim = QColor(pal.get("border", "#444"))
        except Exception:  # noqa: BLE001 - 主题不可用退化为中性色
            line, dim = QColor("#4cc2ff"), QColor("#444")
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            w, h = self.width(), self.height()
            p.setPen(QPen(dim, 1))
            p.drawLine(QPointF(0, h - 1), QPointF(w, h - 1))  # 基线
            vals = self.ring.values
            if len(vals) < 2:
                return
            path = QPainterPath()
            peak = max(vals) or 1.0
            n = len(vals)
            for i, v in enumerate(vals):
                x = w * i / (n - 1)
                y = (h - 3) - (h - 6) * (v / peak)
                if i == 0:
                    path.moveTo(x, y)
                else:
                    path.lineTo(x, y)
            p.setPen(QPen(line, 1.4))
            p.drawPath(path)
        finally:
            p.end()
