"""排序模式状态机（Rev11 V-21 / D-6，规格 = 计划书 §5.3）。

**历史教训（必须先读）**：`progress_table._row_for` 的 docstring 记录了 G69-5 ——
排序开启时视觉行号 ≠ engine index，300 行批量 300/300 错位，重试会打到别的仓库。
本模块在既有两个顺序来源（引擎插入序、列排序）之上加第三个（用户拖拽），
因此把全部模式逻辑收在这里，progress_table 只做转发。

**五条不变量**（tests/test_rev11_w4.py 逐条守卫）：
- S1 `UserRole` 是唯一真相 —— 行内 engine index 永远跟行走；
- S2 拖拽只在手动模式生效（自动排序下 `dragEnabled` 恒 False）；
- S3 切到自动排序即丢弃手动顺序（不清 DB，只丢视图序）；
- S4 批次运行中禁止拖拽（引擎在写行，拖拽 = G69-5 手动复刻）；
- S5 禁止视觉位置推断（任何槽定位都走 `_row_for`，不新增行号假设）。
"""
from __future__ import annotations


def apply_sort_mode(table, btn_auto_sort, strip_running: bool) -> None:
    """按切换按钮状态落模式（S2/S3/S4 的唯一落点）。

    - 选中（自动排序）：开 sortingEnabled、关 drag —— 与升级前行为一致；
    - 未选中（手动）：关 sortingEnabled，drag 仅在非运行态开启（S4）。
    """
    if btn_auto_sort.isChecked():
        table.setSortingEnabled(True)
        table.setDragEnabled(False)                      # S2
    else:
        table.setSortingEnabled(False)
        table.setDragEnabled(not strip_running)          # S2 + S4


def drag_allowed(table, btn_auto_sort, strip_running: bool) -> bool:
    """当前是否允许拖拽（S2+S4 的可测纯判定）。"""
    return (not btn_auto_sort.isChecked()) and (not strip_running)


__all__ = ["apply_sort_mode", "drag_allowed"]
