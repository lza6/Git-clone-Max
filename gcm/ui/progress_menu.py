"""进度表右键菜单的统一构建（G80-B4-1b 抽离）。

为什么移出来：`progress_table.py` 已经超过 800 行硬上限（G79 守卫按集合相等断言），
继续往里加「半成品清理」这类新菜单会越线。把菜单构建（纯 UI 拼装，无业务逻辑）
放到独立模块，主面板只保留一句调用 —— 与 G75 拆 `batch_overview` / `result_labels`
同一范式：把 presentation 部分挪走，行为不变。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMenu


def build_progress_menu(panel, row: int):
    """构造进度表右键菜单（不 exec，便于离屏测试）。

    重试项始终显示：不可用时置灰 + tooltip 说明（G46-12 统一语义）。
    """
    name_item = panel.table.item(row, 0)
    status_item = panel.table.item(row, 2)
    menu = QMenu(panel)
    if name_item is None or status_item is None:
        return menu
    status_key = status_item.data(Qt.ItemDataRole.UserRole) or ""
    act_retry = menu.addAction("⟳ 重试此仓库")
    retryable = status_key in ("failed", "cancelled")
    act_retry.setEnabled(retryable)
    act_retry.setToolTip("仅失败/已取消行可重试" if not retryable else "重新调度该仓库")
    act_retry.triggered.connect(
        lambda _=False, r=row: panel._retry_table_row(r))
    menu.addSeparator()
    act_copy = menu.addAction("复制错误详情")
    act_copy.triggered.connect(
        lambda _=False, r=row: panel._copy_row_detail(r))
    act_open = menu.addAction("打开所在目录")
    act_open.triggered.connect(
        lambda _=False, r=row: panel._open_row_dir(r))
    # G80-B4-1b：只对「已取回但未检出」的行提供清理入口（二次确认）。
    # 菜单里**只显示**，不主动执行 —— 用户得自己点，点完还要再确认一次。
    if panel._row_is_halfcloned(row):
        act_clean = menu.addAction("删除半成品目录…")
        act_clean.setToolTip("该仓库已取回但未检出：删除目录后重新克隆")
        act_clean.triggered.connect(
            lambda _=False, r=row: panel._delete_halfcloned_row(r))
    return menu

