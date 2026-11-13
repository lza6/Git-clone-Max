"""设置页工作区（Rev15 R15-03/04）：设置滚动区 + 日志区的可调分割与紧凑导航。

为什么独立成模块：`settings_panel.py` 已 745 行、`main_window.py` 是已登记技术债；
设置页布局属于组合层，不该再塞回任一文件。这里只碰布局与导航，不碰任何设置字段。
"""
from __future__ import annotations

import weakref
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from .empty_state import build_empty_state
from .log_panel import build_log_box

if TYPE_CHECKING:  # 仅类型标注
    from .main_window import MainWindow

#: 默认 72/28；主任务（设置）优先，日志是排障辅助。
DEFAULT_TOP_RATIO = 0.72
MIN_TOP = 240
MIN_BOTTOM = 120
COMPACT_NAV_WIDTH = 1000


def _num(value) -> float:
    """把比例/像素值转成非负 float；坏值回 0（不走异常控制流）。"""
    if isinstance(value, bool):
        return float(int(value))
    if isinstance(value, (int, float)):
        return max(0.0, float(value))
    if isinstance(value, str):
        text = value.strip()
        if text.replace(".", "", 1).isdigit():
            return max(0.0, float(text))
    return 0.0


class SettingsWorkspace(QSplitter):
    """设置区 / 日志区的垂直分割器：首次显示套默认比例，拖动后防抖落盘。"""

    def __init__(self, owner: MainWindow, top: QWidget, log: QWidget,
                 parent: QWidget | None = None) -> None:
        super().__init__(Qt.Orientation.Vertical, parent)
        self._owner_ref = weakref.ref(owner)
        self._initialized = False
        self._restoring = False
        self.addWidget(top)
        self.addWidget(log)
        # 不用 3:1 stretch：stretch 会在 resize 时覆盖用户拖出的比例。
        # 首次 show 用 apply_saved_sizes() 设置尺寸，QSplitter 自行按比例缩放。
        self.setChildrenCollapsible(False)
        self.setCollapsible(0, False)
        self.setCollapsible(1, True)
        self.setHandleWidth(6)
        self.splitterMoved.connect(self._schedule_save)
        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(300)
        self._save_timer.timeout.connect(self._save_sizes)

    def showEvent(self, event) -> None:      # noqa: N802 —— Qt 命名
        super().showEvent(event)
        if not self._initialized:
            self._initialized = True
            self.apply_saved_sizes()

    def apply_saved_sizes(self) -> None:
        """首次显示：优先用户保存比例，否则 72/28；高度未知时用 720 作基线。"""
        owner = self._owner_ref()
        h = max(240, int(self.height() or 720))
        ratio = DEFAULT_TOP_RATIO
        if owner is not None:
            ui_state = getattr(getattr(owner, "settings", None), "ui_state", None)
            raw = ui_state.get("settings_split") if isinstance(ui_state, dict) else None
            if isinstance(raw, (list, tuple)) and len(raw) == 2:
                a = _num(raw[0])
                b = _num(raw[1])
                if a + b > 0:
                    # 新格式是比例（0.72/0.28），旧格式是绝对像素；两者都还原成比例，
                    # 否则换屏/最大化后窗口高度的增量会全部落到日志区。
                    ratio = a / (a + b)
        top = max(MIN_TOP, min(h - MIN_BOTTOM, int(round(h * ratio))))
        bottom = max(MIN_BOTTOM, h - top)
        if top + bottom > h:
            top = max(MIN_TOP, h - MIN_BOTTOM)
            bottom = max(0, h - top)
        self._restoring = True
        try:
            self.setSizes([top, bottom])
        finally:
            self._restoring = False

    def _schedule_save(self, *_args) -> None:
        if not self._restoring:
            self._save_timer.start()

    def _save_sizes(self) -> None:
        owner = self._owner_ref()
        if owner is None:
            return
        st = getattr(owner, "settings", None)
        store = getattr(owner, "settings_store", None)
        sizes = self.sizes()
        if st is None or store is None or len(sizes) != 2 or sum(sizes) <= 0:
            return
        cur = getattr(st, "ui_state", None)
        cur = dict(cur) if isinstance(cur, dict) else {}
        total = int(sizes[0]) + int(sizes[1])
        top_ratio = round(int(sizes[0]) / total, 4)
        # 存比例而不是绝对像素：换屏/最大化/DPI 变化后仍保持 72/28。
        cur["settings_split"] = [top_ratio, round(1.0 - top_ratio, 4)]
        st.ui_state = cur
        # Rev16 C1：update() 锁内读-改-写；save(旧快照) 会把另一实例刚改的
        # 字段整份回滚（列宽保存是最频繁的写路径，最先迁过来）。
        store.update(ui_state=cur)

    def set_compact_nav(self, compact: bool) -> None:
        """窄窗（<1000）用下拉替代左侧列表，避免设置导航整块消失。"""
        owner = self._owner_ref()
        if owner is None:
            return
        nav = getattr(owner, "settings_nav", None)
        combo = getattr(owner, "settings_nav_combo", None)
        row = getattr(owner, "settings_nav_combo_row", None)
        if nav is not None:
            nav.setVisible(not compact)
        if combo is not None:
            combo.setVisible(compact)
        if row is not None:
            row.setVisible(compact)


def _group_titles(owner: MainWindow) -> list[str]:
    groups = (list(getattr(owner, "settings_groups", []))
              + list(getattr(owner, "settings_groups_extra", [])))
    out: list[str] = []
    for grp in groups:
        full = grp.title()
        out.append(full.split("（")[0].strip() or full)
    return out


def _fill_nav(owner: MainWindow, nav: QListWidget, titles: list[str],
              full_titles: list[str]) -> None:
    for i, short in enumerate(titles, 1):
        nav.addItem(f"{i:02d}  {short}")
        item = nav.item(nav.count() - 1)
        if item is not None and i <= len(full_titles):
            item.setToolTip(full_titles[i - 1])
    nav.currentRowChanged.connect(owner._scroll_to_settings_group)


def _fill_combo(owner: MainWindow, combo: QComboBox, titles: list[str]) -> None:
    for i, short in enumerate(titles, 1):
        combo.addItem(f"{i:02d}  {short}", i - 1)
    combo.setAccessibleName(tr("设置分组跳转"))
    combo.setToolTip(tr("窗口较窄时用下拉选择设置分组"))
    combo.activated[int].connect(owner._scroll_to_settings_group)


def build_settings_workspace(owner: MainWindow, settings_scroll) -> SettingsWorkspace:
    """构建设置页工作区，返回可持久化比例的 QSplitter。"""
    # 设置内容禁止横向滚动；同时限制易撑宽的控件，避免右侧被裁。
    settings_scroll.setHorizontalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    for grp in (list(getattr(owner, "settings_groups", []))
                + list(getattr(owner, "settings_groups_extra", []))):
        for sp in grp.findChildren(QSpinBox):
            sp.setMaximumWidth(200)
        for cb in grp.findChildren(QComboBox):
            if cb.minimumWidth() < 320:
                cb.setMaximumWidth(420)

    titles = _group_titles(owner)
    full_titles = [grp.title() for grp in (
        list(getattr(owner, "settings_groups", []))
        + list(getattr(owner, "settings_groups_extra", [])))]
    nav = QListWidget()
    nav.setObjectName("settingsNav")
    nav.setMaximumWidth(220)
    nav.setMinimumWidth(150)
    nav.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    nav.setTextElideMode(Qt.TextElideMode.ElideRight)
    _fill_nav(owner, nav, titles, full_titles)

    combo = QComboBox()
    combo.setObjectName("settingsNavCombo")
    combo.setMinimumWidth(170)
    combo.setMaximumWidth(320)
    _fill_combo(owner, combo, titles)
    combo_row = QWidget()
    combo_layout = QHBoxLayout(combo_row)
    combo_layout.setContentsMargins(0, 0, 0, 0)
    combo_layout.setSpacing(8)
    combo_layout.addWidget(QLabel(tr("跳转设置分组：")))
    combo_layout.addWidget(combo, 1)

    row = QWidget()
    row_layout = QHBoxLayout(row)
    row_layout.setContentsMargins(0, 0, 0, 0)
    row_layout.setSpacing(10)
    row_layout.addWidget(nav)
    row_layout.addWidget(settings_scroll, 1)

    top = QWidget()
    # 设置区不允许被拖到 0：它是主任务；只有日志区可折叠。
    top.setMinimumHeight(MIN_TOP)
    top_layout = QVBoxLayout(top)
    top_layout.setContentsMargins(0, 0, 0, 0)
    top_layout.setSpacing(8)
    empty = build_empty_state(tr("没有匹配的设置项"), tr("换个关键词试试"), parent=top)
    empty.setVisible(False)
    top_layout.addWidget(empty)
    top_layout.addWidget(combo_row)
    top_layout.addWidget(row, 1)

    log = build_log_box(owner)
    owner.settings_nav = nav
    owner.settings_nav_combo = combo
    owner.settings_nav_combo_row = combo_row
    owner.settings_empty_lbl = empty
    workspace = SettingsWorkspace(owner, top, log)
    owner.settings_workspace = workspace
    return workspace


__all__ = ["COMPACT_NAV_WIDTH", "SettingsWorkspace", "build_settings_workspace"]
