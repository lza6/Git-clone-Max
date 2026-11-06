"""表格骨架（Rev14 R14-08）：耗时路径的"正在准备"占位，纯 QPainter 绘制。

**为什么不用 QWidget 拼**：渲染 3 行占位如果真建控件，会给每行付出一个
QWidget + 布局开销，而骨架恰恰出现在**界面最忙的时刻**（批次刚启动、
几百个仓库正要入表）。这里只画三个圆角矩形，`findChildren` 数量一个不增 ——
守卫 `tests/test_rev14_skeleton` 就断言这一点。

**为什么需要它**：点「开始」到第一条结果回来之间，进度表是**完全空白**的
（空态 overlay 已被行数清零）。慢机械盘 / 杀软实时扫描下这个窗口有 1–3 秒，
用户看到的是"点了没反应"，于是再点一次 —— 这是实测过的重复提交来源。

**为什么不做 shimmer 流光**：`motion=False`（减少动效）时必须整块消失，
而 shimmer 是持续动画，关不掉就只能删代码；静态占位块在任何动效设置下
都传达同一个意思（"在准备，请稍等"）。
"""
from __future__ import annotations

from PyQt6.QtCore import QObject, QRectF, Qt
from PyQt6.QtWidgets import QWidget

from ..i18n import tr

#: 占位行数。3 行是实测值：再多会让人以为"已经有数据了"，再少看起来像一条分割线。
ROWS = 3


class SkeletonRows(QWidget):
    """覆盖在表格上的占位块（父级负责 `setGeometry` 与 `raise_`）。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 不吃鼠标事件：骨架只是视觉占位，点击应落到下面的表格/窗口上
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setVisible(False)
        self.setAccessibleName(tr("正在准备任务列表"))

    def paintEvent(self, _event) -> None:      # noqa: N802 —— Qt 命名
        from PyQt6.QtGui import QColor, QPainter

        from .palettes import TOKENS, _shade
        from .theme import current_palette
        pal = current_palette()
        # 用 panel2 的轻微提亮：比行底色亮一点、比文字暗得多，读作"占位"而不是"数据"
        color = QColor(_shade(pal["panel2"], 118))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        row_h = int(TOKENS["row"]["default"])
        radius = int(TOKENS["radius"]["control"])
        rect = self.rect()
        y = rect.top() + 6
        # 三段宽度递减：暗示"这里会有内容"，同时避免看起来像完整的一行数据
        widths = (0.62, 0.78, 0.45)
        # Rev14 修正：原来是 `min(ROWS, len(widths))` —— 两者恒等于 3，
        # 改 `ROWS` 一点效果都没有（审查实测）。这里让 ROWS 真正成为行数。
        for i in range(ROWS):
            if y + row_h - 8 > rect.bottom():
                break
            w = int((rect.width() - 24) * widths[i % len(widths)])
            bar = QRectF(rect.left() + 12, y, max(40, w), row_h - 10)
            painter.drawRoundedRect(bar, radius, radius)
            y += row_h
        painter.end()


class BusyPreview(QObject):
    """把骨架与空态 overlay 的互斥关系收在一处（Rev14 R14-08）。

    **为什么独立成类**：这层协调放进 `progress_table.py` 会让那个文件越过
    800 行硬上限（`test_g79_repo_hygiene` 的集合相等断言会红）。两者本来就是
    同一件事的两面 —— "现在该显示骨架还是显示空态"，放在一起才读得懂。
    """

    def __init__(self, table: QWidget, empty_overlay=None) -> None:
        super().__init__(table)          # 挂成表格的子对象，随表格一起销毁
        self._table = table
        self._empty = empty_overlay
        self._skeleton = attach(table)
        self._busy = False
        # Rev14 修正：骨架只在 `set_busy(True)` 那一刻取一次 `table.rect()`，
        # 准备期里拖动/最大化窗口它就不跟随了（实测：表格 938×135 时骨架仍是
        # 1338×235 → 越界被裁；放大后右侧 200px 表格裸露）。这里跟随 resize。
        table.installEventFilter(self)

    def eventFilter(self, obj, event) -> bool:      # noqa: N802 —— Qt 命名
        from PyQt6.QtCore import QEvent
        if obj is self._table and self._busy and event.type() == QEvent.Type.Resize:
            self._skeleton.setGeometry(self._table.rect())
        return False

    @property
    def busy(self) -> bool:
        return self._busy

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        if self._busy:
            self._skeleton.setGeometry(self._table.rect())
            self._skeleton.setVisible(True)
            self._skeleton.raise_()
            if self._empty is not None:
                self._empty.setVisible(False)
        else:
            self._skeleton.setVisible(False)

    def suppresses_empty(self) -> bool:
        """准备期不显示空态文案 —— 用户明明刚贴了一堆地址。"""
        return self._busy


def attach(parent: QWidget) -> SkeletonRows:
    """在 `parent` 上挂一个隐藏的骨架块（调用方负责显示时机）。"""
    sk = SkeletonRows(parent)
    sk.setGeometry(parent.rect())
    return sk


__all__ = ["BusyPreview", "ROWS", "SkeletonRows", "attach"]
