"""空态统一（Rev11 V-22，承接 G75 T-UI-08 partial）—— 空态文案的唯一工厂。

**为什么独立成模块**：G75 后空态各自为政 —— 下载页用 `#emptyHint` 直书、
失败面板/日志区各写各的。本工厂统一三件事：
1. `objectName = "emptyHint"`（h2 字号，在整屏 13px 里能立刻被看见）；
2. 文案走 `tr()`（i18n 不漏）；
3. 「过滤无结果」与「真为空」两种空态文案口径唯一。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QWidget

from ..i18n import tr


def empty_copy(title: str, hint: str = "", filtered: bool = False) -> tuple[str, str]:
    """空态文案的**唯一来源**：返回 `(文案, tooltip)`。

    为什么单独抽一个纯函数：`build_empty_state` 造的是新 QLabel，而管理页是在
    **同一个标签上换文案**（三态切换）。两处各写一份文案迟早漂移 —— 让两边都从
    这里取，`filtered` 参数也就有了真实的生产消费点（审查发现它此前只在测试里传）。
    """
    text = title if not hint else f"{title}\n{hint}"
    tooltip = tr("清空搜索框可查看全部条目") if filtered else ""
    return text, tooltip


def build_empty_state(title: str, hint: str = "", filtered: bool = False,
                      parent: QWidget | None = None) -> QLabel:
    """构造一个标准空态标签。

    - `title`：主文案（如「还没有登记任何仓库」）；
    - `hint`：次文案（如「去设置页添加」），拼在第二行；
    - `filtered`：True = 过滤导致的空（文案补「清空搜索框」口径）。
    """
    text, tooltip = empty_copy(title, hint, filtered)
    lbl = QLabel(text)
    lbl.setObjectName("emptyHint")
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    lbl.setWordWrap(True)
    lbl.setTextFormat(Qt.TextFormat.PlainText)
    lbl.setAccessibleDescription(tr("空状态提示"))
    if tooltip:
        lbl.setToolTip(tooltip)
    if parent is not None:
        lbl.setParent(parent)
    return lbl


class DownloadEmptyState:
    """下载中心空态 overlay 的判定（G36-5 / G70 / Rev14 R14-08 三态收敛）。

    **为什么搬到这里**：这段判定原本在 `progress_table._update_empty_download`
    里，加上骨架互斥之后把那个文件顶过了 800 行硬上限（`test_g79_repo_hygiene`
    的集合相等断言会红）。空态判定本来就属于空态模块 —— 它只读表格的行数、
    过滤词与骨架状态，不碰表格的其它职责。

    三态：
    - 表格无行 → 「粘贴地址开始下载」；
    - 有行但被过滤全部隐藏 → 「没有匹配的仓库（清空搜索框查看全部）」；
    - 准备期（骨架显示中）→ 什么都不显示（用户刚贴了一堆地址，"没有仓库"是错的）。
    """

    def __init__(self, table: QWidget, overlay: QLabel, default_text: str,
                 filter_text_getter=None) -> None:
        self._table = table
        self._overlay = overlay
        self._default_text = default_text
        self._filter_text = filter_text_getter
        self.busy_preview = None      # 由 progress_table 在骨架建好后挂上

    def update(self, visible_rows: int | None = None) -> None:
        bp = self.busy_preview
        if bp is not None and bp.suppresses_empty():
            self._overlay.setVisible(False)
            return
        if visible_rows is None:
            visible_rows = 0 if self._table.rowCount() == 0 else 1
        show = visible_rows == 0
        filtering = bool(self._filter_text() if self._filter_text else False)
        self._overlay.setText(
            tr("没有匹配的仓库\n（清空搜索框查看全部）")
            if (show and filtering and self._table.rowCount() > 0)
            else self._default_text)
        self._overlay.setVisible(show)
        if show:
            self._overlay.setGeometry(self._table.rect())
            self._overlay.raise_()


__all__ = ["DownloadEmptyState", "build_empty_state", "empty_copy"]
