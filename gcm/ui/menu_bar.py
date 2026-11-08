"""主窗菜单栏（Rev12 U-10/U-11，尼尔森原则④「一致性与标准」+ 原则⑩「帮助随时可得」）。

**为什么现在才有**：v9.x 全库 `menuBar` 零命中 —— 40+ 功能散落在页签、右键菜单与
设置页三层，`Ctrl+K` 命令面板是唯一统一入口（但**可发现性为零**：没人会猜到一个
没写在任何界面上的快捷键）。桌面 Windows 应用没有菜单栏，违反平台惯例（原则④），
也让「帮助」无法随时可达（原则⑩）—— 帮助对话框此前只能从设置页深处打开。

**为什么做得这么小**：主窗 `main_window.py` 已 2980 行（超 800 硬上限、集合相等守卫
锁定）。菜单栏**全部内容**在此模块构建，主窗只留一句 `build_menu_bar(self)`。
菜单项一律**转发到主窗已有的公开方法**，不新写业务逻辑 —— 菜单是索引，不是实现。

**为什么用 `_act()` 而不是手写 QAction**：目标方法缺失时立即置灰并给 tooltip，
而不是静默生成一个点了没反应的菜单项（那比灰着更伤可用性，原则①）。

**帮助菜单的纪律**：`F1` 与「帮助 → 使用说明」必须指向**同一个**槽，
否则两条路会各自漂移（帮助页已经因漏掉 `Ctrl+1/2/3` 漂移过一次，见 Rev6 U-22）。
"""
from __future__ import annotations

from typing import Any

from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import QMainWindow

from ..i18n import tr

#: 所有快捷键的唯一清单（帮助页与菜单共用 —— 单一事实源，防漂移）
SHORTCUTS: tuple[tuple[str, str], ...] = (
    ("Ctrl+= / Ctrl+- / Ctrl+0", tr("放大 / 缩小 / 复位字号")),
    ("Ctrl+1 / Ctrl+2 / Ctrl+3", tr("切换下载中心 / 仓库管理 / 设置")),
    ("Ctrl+K", tr("命令面板（搜索并执行任意功能）")),
    ("Ctrl+Alt+S", tr("开始 / 取消全部")),
    ("Ctrl+Alt+U", tr("一键更新全部")),
    ("Ctrl+Alt+M", tr("显示主窗口")),
    ("F6", tr("在各页签之间循环，并把焦点交给新页的第一个可操作控件")),
    ("F1", tr("打开使用说明")),
    ("Ctrl+Z", tr("撤销上一步可撤销操作")),
)


def _act(owner: Any, text: str, slot: str, shortcut: str = "") -> QAction:
    """建一个转发到 `owner.<slot>` 的菜单项。

    **不用 lambda 捕获 owner**（会形成 `menu→action→lambda→owner` 引用环，
    这正是本项目历史原生崩溃的根因，见 CLAUDE.md §6）。绑定方法由 PyQt 弱引用保存。
    """
    action = QAction(tr(text), owner)
    target = getattr(owner, slot, None)
    if callable(target):
        action.triggered.connect(target)
    else:
        # 目标缺失时置灰而不是静默失效 —— 菜单项点了没反应比灰着更糟
        action.setEnabled(False)
        action.setToolTip(tr("该功能在当前版本不可用"))
    if shortcut:
        action.setShortcut(QKeySequence(shortcut))
    return action


def build_menu_bar(owner: QMainWindow) -> Any:
    """给主窗装上菜单栏。返回 `QMenuBar`（测试可断言菜单项集合）。"""
    bar = owner.menuBar()
    bar.setNativeMenuBar(False)   # 跨平台一致（macOS 上也不进全局菜单）

    # ---------------------------------------------------------------- 文件
    m_file = bar.addMenu(tr("文件(&F)"))
    m_file.addAction(_act(owner, tr("开始并行下载 / 更新"), "start_all"))
    m_file.addAction(_act(owner, tr("取消全部"), "cancel_all"))
    m_file.addSeparator()
    m_file.addAction(_act(owner, tr("保存清单…"), "_save_task_list"))
    m_file.addAction(_act(owner, tr("载入清单…"), "_load_task_list"))
    m_file.addSeparator()
    m_file.addAction(_act(owner, tr("打开克隆目录"), "open_target"))
    m_file.addAction(_act(owner, tr("打开数据目录"), "open_data_dir"))
    m_file.addSeparator()
    m_file.addAction(_act(owner, tr("退出"), "close"))

    # ---------------------------------------------------------------- 编辑
    m_edit = bar.addMenu(tr("编辑(&E)"))
    m_edit.addAction(_act(owner, tr("撤销"), "menu_undo", "Ctrl+Z"))
    m_edit.addAction(_act(owner, tr("操作历史…"), "open_oplog_dialog"))
    m_edit.addSeparator()
    m_edit.addAction(_act(owner, tr("清空输入区"), "confirm_clear_input"))

    # ---------------------------------------------------------------- 视图
    m_view = bar.addMenu(tr("视图(&V)"))
    for idx, name in enumerate((tr("下载中心"), tr("仓库管理"), tr("设置"))):
        act = QAction(tr(name), owner)
        act.setProperty("tab_index", idx)
        act.triggered.connect(owner.menu_goto_tab)   # 绑定方法；槽内读 sender 属性
        m_view.addAction(act)
    m_view.addSeparator()
    m_view.addAction(_act(owner, tr("放大字号"), "zoom_in", "Ctrl+="))
    m_view.addAction(_act(owner, tr("缩小字号"), "zoom_out", "Ctrl+-"))
    m_view.addAction(_act(owner, tr("复位字号"), "zoom_reset", "Ctrl+0"))

    # ---------------------------------------------------------------- 工具
    m_tools = bar.addMenu(tr("工具(&T)"))
    m_tools.addAction(_act(owner, tr("命令面板…"), "open_command_palette", "Ctrl+K"))
    m_tools.addAction(_act(owner, tr("失败汇总…"), "_open_failure_dialog"))
    m_tools.addAction(_act(owner, tr("重试队列…"), "_open_retry_dialog"))
    m_tools.addAction(_act(owner, tr("后台任务…"), "show_background_jobs"))
    m_tools.addSeparator()
    m_tools.addAction(_act(owner, tr("统计中心…"), "show_statistics"))
    m_tools.addAction(_act(owner, tr("导出诊断包…"), "_export_diag_bundle"))
    m_tools.addAction(_act(owner, tr("环境自检"), "_run_selftest"))

    # ---------------------------------------------------------------- 帮助
    m_help = bar.addMenu(tr("帮助(&H)"))
    m_help.addAction(_act(owner, tr("使用说明"), "open_help", "F1"))
    m_help.addAction(_act(owner, tr("开始使用清单"), "_show_checklist"))
    m_help.addSeparator()
    m_help.addAction(_act(owner, tr("检查更新…"), "check_update_now"))
    m_help.addAction(_act(owner, tr("关于"), "show_about"))
    return bar


__all__ = ["SHORTCUTS", "build_menu_bar"]
