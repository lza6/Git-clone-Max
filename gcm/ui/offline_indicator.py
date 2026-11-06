"""离线指示（Rev14 R14-10）：最近几次失败全是网络类 → 齿条右端亮一个「断网」标记。

**这不是 Web 的 Service Worker**。本项目是桌面单机工具，没有可缓存的前端资源，
"离线"在这里只有一个含义：**网络整体不可用**（而不是某个仓库地址写错）。
用户真正需要知道的是"现在点开始也没用，等网络恢复"，而不是逐个仓库去看错误。

三条硬约束：

1. **形状编码，不用字符**。微软雅黑缺 `✓ ✕ ⚠ ⊘` 字形，字符会渲染成 tofu 方块
   （G74 踩过的真事故）。这里画「圆 + 斜杠」，与 `status_pill` 的 7 种状态形状
   互不相同。
2. **不弹窗、不阻塞**。它是状态指示，不是模态提示 —— 弹窗会打断用户正在粘贴的
   地址列表，而"网络恢复"这件事不需要用户确认。
3. **恢复即消**。任何一次成功都把 streak 清零，避免一次断网之后永远显示离线。

判定用**唯一错误分类器** `app.errors.classify_error`：不在这里维护关键字表
（否则"哪个错误算网络"就有第二份真相）。
"""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtWidgets import QWidget

from ..app.errors import CAT_NETWORK
from ..i18n import tr

#: 连续多少次网络类失败才判定「离线」。3 次是有意的：单次超时很常见（弱网、
#: 大仓库），1–2 次就报警会让标记长期亮着，反而失去信息量。
STREAK_WINDOW = 3


class FailureStreak:
    """最近的失败类别窗口（纯逻辑，可无 GUI 单测）。"""

    def __init__(self, window: int = STREAK_WINDOW) -> None:
        self._window = max(1, int(window))
        self._recent: list[str] = []

    def note_failure(self, category: str) -> bool:
        """记录一次失败，返回**当前**是否应显示离线。"""
        self._recent.append(str(category or "other"))
        if len(self._recent) > self._window:
            self._recent = self._recent[-self._window:]
        return self.is_offline

    def note_success(self) -> None:
        """任何一次成功都清零 —— 网络恢复了就该立刻恢复常态。"""
        self._recent.clear()

    @property
    def is_offline(self) -> bool:
        return (len(self._recent) >= self._window
                and all(c == CAT_NETWORK for c in self._recent))

    @property
    def recent(self) -> tuple[str, ...]:
        return tuple(self._recent)


class OfflineIndicator(QWidget):
    """齿条右端的断网标记：一个带斜杠的圆，离线才显示。"""

    SIZE = 14

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setVisible(False)
        self.setAccessibleName(tr("网络不可用"))
        self.setAccessibleDescription(tr("最近三次失败全部是网络类错误，检查网络或代理后重试"))
        self.setToolTip(tr("最近三次失败全部是网络类错误：检查网络或代理后重试"))

    def sizeHint(self) -> QSize:      # noqa: N802 —— Qt 命名
        return QSize(self.SIZE, self.SIZE)

    def set_offline(self, offline: bool) -> None:
        """切换显示状态（幂等；重复设置同值不重绘）。"""
        if bool(offline) != self.isVisible():
            self.setVisible(bool(offline))
            if offline:
                self.update()

    def paintEvent(self, _event) -> None:      # noqa: N802 —— Qt 命名
        from PyQt6.QtGui import QColor, QPainter, QPen

        from .theme import current_palette
        pal = current_palette()
        color = pal.get("warning") or pal["error"]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(color), 2)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = self.rect().adjusted(2, 2, -2, -2)
        painter.drawEllipse(rect)
        painter.drawLine(rect.left(), rect.bottom(), rect.right(), rect.top())
        painter.end()


__all__ = ["FailureStreak", "OfflineIndicator", "STREAK_WINDOW"]
