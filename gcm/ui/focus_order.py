"""键盘导航（Rev14 R14-03）：显式 tab 链 + `F6` 页签循环。

**为什么需要**：全库此前 `setTabOrder` **0 处**、`keyPressEvent` **0 处** ——
键盘用户只能靠 8 个运行时快捷键，`Tab` 走的是 Qt 按**创建顺序**给的默认链。
创建顺序是"先搭哪个控件"，不是"用户看到哪个在前面"，于是 `Tab` 会从页底的
「诊断包」跳到页顶的「保存清单」。WCAG 2.1 AA 的 2.1.1（键盘可达）与
2.4.3（焦点顺序）都要求顺序**跟随视觉顺序**。

**为什么不给每个控件写 `keyPressEvent`**：Qt 的 `Tab` / `Shift+Tab` / `Space` /
`Enter` 已被默认处理，重写只会破坏平台习惯。这里只做两件 Qt 不会自动做对的事：
① 声明每页的 tab 链；② 提供 `F6` 在三页签之间循环（Windows 上 `F6` 是
"在窗格间移动焦点"的惯例键）。

**设计约束**：
- 链里用**属性名**而不是控件对象 —— 模块导入时不依赖 `MainWindow` 已构建，
  也不持有控件引用（否则又是一个 QObject 引用环，G69-18 踩过）。
- 缺失的属性直接跳过（`getattr` 取 None）：控件被删/改名不会让整个模块崩，
  守卫 `tests/test_rev14_keyboard` 会断言链里每个名字都真实存在。
"""
from __future__ import annotations

from typing import Any

#: 下载中心：视觉顺序 = 任务清单行 → 快捷填充 → 输入区 → 选项 → 主操作 → 进度 → 工具行。
#: 滚动容器（`download_top_scroll` / `quick_scroll`）本身可聚焦，但不进链：
#: 它们在链里会让 `Tab` 多停一次却没有任何可操作内容。
DOWNLOAD_CHAIN: tuple[str, ...] = (
    "btn_save_list", "combo_load_list", "btn_load_list", "btn_star_import",
    "btn_edit_quick",
    "repo_input", "btn_readme_import",
    "target_edit", "mode_combo", "depth_spin",
    "btn_start", "btn_cancel", "btn_clear",
    "progress_filter", "table", "btn_pause_batch",
    "btn_open", "btn_failures", "btn_retry_queue", "btn_diag_bundle",
    "btn_more_tools",
)

#: 仓库管理：工具条 → 表格 → 行批量操作。
MANAGE_CHAIN: tuple[str, ...] = (
    "btn_update_all", "btn_refresh", "btn_import_local",
    "manage_table",
    "btn_batch_tag", "btn_export_selected", "btn_delete", "btn_cancel_manage",
)

#: 设置与日志：导航 → 搜索 → 内容区。内容区内部的控件交给 Qt 默认顺序
#: （60+ 个设置项逐个声明既脆弱又没收益：它们在同一个滚动容器里创建顺序
#: 与视觉顺序本来就一致）。
SETTINGS_CHAIN: tuple[str, ...] = (
    "settings_nav", "settings_nav_combo", "settings_search", "settings_scroll",
    "log_filter_edit", "log_view",
)

CHAINS: dict[int, tuple[str, ...]] = {
    0: DOWNLOAD_CHAIN,
    1: MANAGE_CHAIN,
    2: SETTINGS_CHAIN,
}


def chain_widgets(owner: Any, tab_index: int) -> list[Any]:
    """按声明顺序取出真实存在的控件（缺失的跳过）。"""
    out: list[Any] = []
    for name in CHAINS.get(int(tab_index), ()):
        w = getattr(owner, name, None)
        if w is not None:
            out.append(w)
    return out


def apply_tab_order(owner: Any, tab_index: int) -> int:
    """把一页的可聚焦控件按视觉顺序串成显式 tab 链，返回实际串联的控件数。

    `QWidget.setTabOrder` 是静态方法，逐对声明即可；Qt 会把这些对合并成链。
    不包 `try/except`：控件都在 `_build_ui` 之后取，调用时机（切页签 / 安装时）
    没有销毁路径；真被销毁说明有更严重的问题，静默跳过只会掩盖它
    （异常预算棘轮也只降不升）。
    """
    from PyQt6.QtWidgets import QWidget
    widgets = chain_widgets(owner, tab_index)
    for first, second in zip(widgets, widgets[1:], strict=False):
        QWidget.setTabOrder(first, second)
    return len(widgets)


def install(owner: Any) -> None:
    """装 `F6` 页签循环，并把当前页签的链立刻应用一次。

    `F6` 用**绑定方法**连接：`lambda` 捕获 `self` 会重新接上引用环
    （G69-18：`del w` 之后要能按引用计数释放）。
    """
    from PyQt6.QtGui import QKeySequence, QShortcut
    sc = QShortcut(QKeySequence("F6"), owner)
    sc.activated.connect(owner.cycle_tab_focus)
    owner._focus_shortcut = sc
    apply_tab_order(owner, owner.tabs.currentIndex())
