"""管理页表格列宽策略（Rev15 R15-00 从 manage_panel 迁出）。

`manage_panel.py` 已 800 行整，任何新增 UI 都会撞硬上限；列宽策略与列显隐是
独立的一层（持久化 / 窄窗下限 / L 档加宽），放在这里既腾头寸也便于单测。
`ManagePanel` 继承本 mixin，公开属性 `COLUMN_SPECS` 保持可用。
"""
from __future__ import annotations

from PyQt6.QtCore import QTimer

from .layout import coerce_int


class ManageColumnMixin:
    """宿主须提供 `manage_table`、`owner`、`_col_width_timer`。"""

    #: 定宽列 → (默认宽, 窄窗下限, L 档宽)。三档都写在表里，免得散落魔法数字。
    COLUMN_SPECS: dict[int, tuple[int, int, int]] = {
        3: (150, 140, 150),  # 上次同步（窄窗也要完整日期时间）
        4: (78, 64, 78),     # HEAD
        5: (70, 70, 96),     # 历史（每行一个「查看」按钮，L 档给更大点击区）
        6: (64, 48, 64),     # 健康
        7: (84, 52, 84),     # 大小
    }

    def _ui_state_dict(self) -> dict:
        """当前 settings.ui_state（拿不到就给空 dict，调用方只读）。"""
        st = getattr(self.owner, "settings", None)
        raw = getattr(st, "ui_state", None) if st is not None else None
        return raw if isinstance(raw, dict) else {}

    def _set_column_widths(self, widths: dict[int, int]) -> None:
        """程序化设置列宽：停掉防抖，并延迟到下一轮事件循环才解除抑制。

        `setColumnHidden()` 也会发 `sectionResized(…, 0)`；如果只在同步段加
        `_col_width_restoring`，400ms 后定时器仍会把「窄窗下限 / 隐藏列 0」
        写进 `settings.ui_state`，覆盖用户拖出来的宽度。
        """
        timer = getattr(self, "_col_width_timer", None)
        if timer is not None:
            timer.stop()
        self._col_width_restoring = True
        self._col_programmatic = {int(c): int(w) for c, w in widths.items()}
        try:
            for col, w in widths.items():
                self.manage_table.setColumnWidth(int(col), int(w))
        finally:
            QTimer.singleShot(0, self._end_column_restore)

    def _end_column_restore(self) -> None:
        timer = getattr(self, "_col_width_timer", None)
        if timer is not None:
            timer.stop()
        self._col_width_restoring = False

    def _restore_column_widths(self) -> None:
        """恢复用户上次拖出来的列宽（非法/越界值夹回区间，不抛异常）。"""
        saved = self._ui_state_dict().get("manage_columns")
        if not isinstance(saved, dict):
            return
        widths: dict[int, int] = {}
        for col, (default_w, floor_w, _wide_w) in self.COLUMN_SPECS.items():
            w = coerce_int(saved.get(str(col), saved.get(col, 0)), 0)
            widths[col] = max(floor_w, min(400, w)) if w > 0 else default_w
        self._set_column_widths(widths)

    def _on_section_resized(self, *args) -> None:
        """列宽变化 → 防抖 400ms 落盘（拖分隔线每像素都发信号，不能直写）。

        `args[0]` 是 logicalIndex：用户真的拖了某列时把它从「程序化值」集合移除，
        之后才允许把该列写盘。
        """
        if getattr(self, "_col_width_restoring", False):
            return
        idx = coerce_int(args[0], -1) if args else -1
        if idx >= 0:
            getattr(self, "_col_programmatic", {}).pop(idx, None)
        self._col_width_timer.start()

    def _save_column_widths(self) -> None:
        """把**用户拖出来的**列宽写进 `settings.ui_state`（写侧；读侧见 `_restore`）。

        三类值不写：隐藏列、`stretchLastSection` 自动拉伸的最后一列、仍是
        程序化下限的列。否则窄窗一次 `setColumnHidden + apply_column_budget`
        就会把用户保存的 300px 覆盖成 140px。
        """
        st = getattr(self.owner, "settings", None)
        store = getattr(self.owner, "settings_store", None)
        if st is None or store is None:
            return
        prev = self._ui_state_dict().get("manage_columns")
        prev = prev if isinstance(prev, dict) else {}
        header = self.manage_table.horizontalHeader()
        last_visible = -1
        if header.stretchLastSection():
            for c in range(self.manage_table.model().columnCount() - 1, -1, -1):
                if not self.manage_table.isColumnHidden(c):
                    last_visible = c
                    break
        prog = getattr(self, "_col_programmatic", {})
        widths: dict[str, int] = {}
        for col in self.COLUMN_SPECS:
            cur = int(self.manage_table.columnWidth(col))
            keep_old = (self.manage_table.isColumnHidden(col)
                        or col == last_visible
                        or prog.get(col) == cur)
            if keep_old:
                old = coerce_int(prev.get(str(col), prev.get(col, 0)), 0)
                if old > 0:
                    widths[str(col)] = old
            else:
                widths[str(col)] = cur
        cur_state = self._ui_state_dict()
        cur_state["manage_columns"] = widths
        # Rev16 C1：走 `update()`（锁内读-改-写）。此前 `store.save(st)` 用旧快照
        # 整份覆盖 —— 两个实例同时开着时，拖一下列宽就把另一窗口刚改的并发数
        # 回滚掉；凭据类字段也绝不能被陈旧快照写盘。
        st.ui_state = cur_state
        store.update(ui_state=cur_state)

    def apply_column_budget(self, narrow: bool, wide: bool = False) -> None:
        """按窗口档位重排定宽列（Rev14 R14-06）。

        - 窄窗（`narrow`）：压到各自**下限**，把宽度还给仓库名/路径；
        - L 档（`wide`）：用偏宽的档位（「历史」列 96，点击目标更大）；
        - 其余：用用户保存的宽度（没有保存值就是默认值）。
        """
        saved = self._ui_state_dict().get("manage_columns")
        widths: dict[int, int] = {}
        for col, (default_w, floor_w, wide_w) in self.COLUMN_SPECS.items():
            if narrow:
                widths[col] = floor_w
                continue
            want = default_w
            if isinstance(saved, dict):
                raw = coerce_int(saved.get(str(col), saved.get(col, 0)), 0)
                # 0 = 旧版本/隐藏列写下的坏值；按默认宽度，而不是夹到下限。
                if raw > 0:
                    want = max(floor_w, min(400, raw))
            if wide:
                want = max(want, wide_w)
            widths[col] = want
        # Rev16 D1：布局重排（resize/最大化/主题切换触发）会 `_set_column_widths`
        # → `timer.stop()`。若用户刚拖完列宽（400ms 防抖还没到），待落盘宽度
        # 会被一起吞掉。重排前先把防抖定时器里挂着的宽度同步落盘。
        timer = getattr(self, "_col_width_timer", None)
        if timer is not None and timer.isActive():
            self._save_column_widths()
        self._set_column_widths(widths)


__all__ = ["ManageColumnMixin"]
