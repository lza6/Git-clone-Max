"""G45-6 进度表面板（QWidget 子类）：进度表 + 空态 overlay + 过滤框 +
进度/结果回填。持有 owner（MainWindow）引用访问窗口服务；对外方法名与
MainWindow 保持一致（MainWindow 保留同名转发，行为零变化）。
"""
from __future__ import annotations

import os
import weakref
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication as _QApp,
)
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from ..models import RepoSpec, SyncResult, SyncStatus
from ..util.openfolder import open_folder
from . import main_window as _mw  # 兼容测试对 main_window.QMessageBox 等模块级 patch
from .halfclone_actions import (
    delete_halfcloned_row,
    looks_halfcloned,
    repair_halfcloned_row,
    row_repo_dir,
)
from .progress_delegate import DETAIL_ROLE, ProgressDelegate
from .progress_menu import build_progress_menu
from .result_labels import (
    format_duration as _format_duration,  # Rev11 V-16（本文件曾超 800 行，口径抽到 result_labels）
)
from .sort_mode import apply_sort_mode  # Rev11 V-21（状态机规格：计划书 §5.3）
from .theme import LogLevel, current_palette


class ProgressTable(QWidget):
    """下载中心进度表面板。"""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        # owner 允许为 None（测试会构造无宿主的面板单独验证数据逻辑）
        self._owner_ref = (weakref.ref(owner) if owner is not None
                           else (lambda: None))
        # 高频进度详情节流：速率/对象数批量刷新（32 并发时不阻塞主线程）
        self._detail_batch: dict = {}
        self._detail_timer = QTimer(self)
        self._detail_timer.setInterval(300)
        self._detail_timer.timeout.connect(self._flush_detail_batch)
        self._progress_filter_text = ""
        self._bulk_loading = False
        # G70：进度值内存字典（engine index → percent），delegate 直接读取
        self._progress_values: dict[int, int] = {}
        # G70：进度更新合批（250ms）—— 32 并发时不再每个信号都触发一次视图更新
        self._progress_batch: dict = {}
        self._progress_timer = QTimer(self)
        self._progress_timer.setSingleShot(True)
        self._progress_timer.setInterval(250)
        self._progress_timer.timeout.connect(self._flush_progress_batch)
        self._build_ui()

    @property
    def owner(self):
        """G69-18：**弱引用**持有宿主，避免 Panel↔MainWindow 引用环。

        原实现 `self.owner = owner` 是强引用：MainWindow 持有 Panel、Panel 又持有
        MainWindow → 环 → 只能靠循环 GC 回收 → 回收时机不可控（可能在 Qt 分发
        事件中途）→ 原生崩溃。弱引用后窗口由引用计数立即释放。
        """
        return self._owner_ref()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setSpacing(10)
        # G46-4 / G75：批次总览条抽成独立组件（progress_table 已破 800 行红线）。
        # 职责：**本批次**完成度；进程运行态读数在 status_strip（两者回答不同问题）。
        from .batch_overview import BatchOverview
        self.batch_overview = BatchOverview(self)
        self.batch_total_lbl = self.batch_overview.label   # 兼容既有引用/测试
        self.batch_bar = self.batch_overview.bar
        v.addWidget(self.batch_overview)
        # 进度表（Rev11 V-16：末尾加「耗时」列 → 6 列；既有 0-4 列索引全部不变）
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [tr(h) for h in ("仓库", "进度", "状态", "本次", "详情", "耗时")])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3):
            self.table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents)
        # G74：详情列吃掉剩余宽度并右省略 —— 原来的 ResizeToContents 会让
        # 一句长错误把表格撑出视口（右侧被裁且很难发现横向滚动条）。
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch)
        # Rev11 V-16：耗时列 ResizeToContents + 等宽右对齐（数据列走 mono，§2.4）
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # Rev14 R14-03 修正：QAbstractItemView 默认 `tabKeyNavigation=True` 会把 Tab
        # 吃掉在单元格之间跳 —— 于是「Tab 顺序跟随视觉顺序」在表格这里根本不成立
        # （500 行 × 6 列要按 3000 次才走得到工具行）。表内用方向键，Tab 交给焦点链。
        self.table.setTabKeyNavigation(False)
        self.table.setMinimumHeight(120)
        self.table.setAccessibleName(tr("下载进度表"))
        self.table.setAccessibleDescription(
            tr("每行一个仓库：仓库名、进度、状态、本次动作、详情、耗时"))
        # G70：进度列改用 delegate 自绘（不再为每行创建 QProgressBar）
        self._progress_delegate = ProgressDelegate(
            self.table, values=self._progress_values)
        self.table.setItemDelegateForColumn(1, self._progress_delegate)
        # G75：状态列改 pill 自绘（形状编码 + 语义色）。只影响绘制，
        # 单元格文本与 UserRole 里的原始状态值保持不变（排序/右键/统计不受影响）。
        from .palettes import TOKENS
        from .status_pill import StatusPillDelegate
        self._status_delegate = StatusPillDelegate(self.table)
        self.table.setItemDelegateForColumn(2, self._status_delegate)
        # Rev11（V-11）：左缘刻度尺 —— 第 0 列的装饰 delegate，按行状态着色的
        # 3px 竖带 + 每 10 行一个刻度缺口。几百行滚动时用余光感知进度分布。
        from .edge_ruler import EdgeRulerDelegate
        self._ruler_delegate = EdgeRulerDelegate(self.table)
        self.table.setItemDelegateForColumn(0, self._ruler_delegate)
        self.table.verticalHeader().setDefaultSectionSize(
            int(TOKENS["row"]["default"]))
        # G75：仪表齿条用的"当前活跃任务"集合（按 engine index 记，排序/过滤无关）
        self._strip_running: set[int] = set()
        # G02-2 表头点击排序（状态列优先级：运行>等待>成功>冲突>失败>取消>跳过）
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._on_sort_changed)
        # Rev11（V-21 / D-6，规格见 计划书 §5.3）：「自动排序」切换按钮 ——
        # 选中 = 自动排序（现状行为，默认）；未选中 = 手动模式（可拖拽）。
        # 五条不变量：S1 UserRole 唯一真相 / S2 仅手动可拖 / S3 切自动即弃手动序 /
        # S4 运行中禁拖 / S5 禁止视觉位置推断。状态机在 `sort_mode.py`。
        self.btn_auto_sort = QPushButton(tr("自动排序"))
        self.btn_auto_sort.setCheckable(True)
        self.btn_auto_sort.setChecked(True)
        self.btn_auto_sort.setToolTip(tr(
            "选中：点表头自动排序（默认）。\n"
            "取消：进入手动模式，可拖拽行调整顺序；批次运行中拖拽被禁用。"))
        self.btn_auto_sort.toggled.connect(self._on_auto_sort_toggled)
        self.table.setDragEnabled(False)       # S2：默认（自动排序）下不可拖
        from PyQt6.QtWidgets import QAbstractItemView
        self.table.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        # G35-1 进度表右键菜单：重试此仓库 / 复制错误详情 / 打开所在目录
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._progress_table_menu)
        v.addWidget(self.table, 3)
        # G36-5 下载中心空态引导：overlay 标签（绝对定位在表格上，有数据时隐藏）
        # Rev11（V-22）：文案口径走 empty_state 工厂（唯一来源）
        from .empty_state import DownloadEmptyState, build_empty_state
        self._empty_default_text = tr("粘贴仓库地址开始下载\n（支持多行 / 拖入 txt 文件）")
        self._empty_download = build_empty_state(
            title=self._empty_default_text, parent=self.table)
        self._empty_download.setVisible(False)
        self._empty_download.raise_()
        # Rev14 R14-08：批次刚启动到第一条结果之间的空窗用骨架占位（纯绘制，不建控件）
        from .skeleton import BusyPreview
        self._busy_preview = BusyPreview(self.table, self._empty_download)
        # 三态判定（无数据 / 筛选无结果 / 准备期）收敛到 empty_state —— 本文件贴 800 行红线
        self._empty_state = DownloadEmptyState(
            self.table, self._empty_download, self._empty_default_text,
            filter_text_getter=self._progress_filter_text_now)
        self._empty_state.busy_preview = self._busy_preview
        # G02-1 搜索行（Rev11：抽到 _build_tools_row，_build_ui 守 100 行预算）
        v.addWidget(self._build_tools_row())

    def _build_tools_row(self) -> QWidget:
        """搜索过滤框（防抖 200ms）+ 排序模式切换（V-21）+「暂停选中」（G02-3）。

        Rev14 F1：改成返回**一个 widget**（原来返回 layout）。理由是最矮的窗口要把
        整行收起来给动作区让位 —— layout 没法 `setVisible`，只能一个个隐藏控件，
        而「搜索：」这个内联 QLabel 没有句柄会留下来变成孤儿文字。
        """
        wrap = QWidget()
        row_tools = QHBoxLayout(wrap)
        row_tools.setContentsMargins(0, 0, 0, 0)
        row_tools.addWidget(QLabel(tr("搜索：")))
        self.progress_filter = QLineEdit()
        self.progress_filter.setPlaceholderText(tr("按仓库名过滤…"))
        self.progress_filter.setClearButtonEnabled(True)
        self.progress_filter.setAccessibleName(tr("按仓库名过滤进度"))
        self.progress_filter.textChanged.connect(self._schedule_progress_filter)
        row_tools.addWidget(self.progress_filter, 1)
        # Rev11（V-21）：排序模式切换（进度表右下，与搜索框同行）
        row_tools.addWidget(self.btn_auto_sort)
        btn_pause_selected = QPushButton(tr("暂停选中"))
        btn_pause_selected.clicked.connect(self.pause_selected)
        row_tools.addWidget(btn_pause_selected)
        row_tools.addStretch()
        self._tools_row = wrap          # 供折叠态整行让位（Rev14 F1）
        return wrap




    def _prepare_table(self, n):
        # 排序开启下先关掉再重建，避免插入行时被自动重排打乱 index
        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(n)
        self.table.setSortingEnabled(was_sorting)
        # G70：新批次清空进度值，避免旧值串到新行
        self._progress_values.clear()
        self._progress_batch.clear()
        # G75：新批次清空活跃集合（齿条活跃数从 0 重新计）
        try:
            self._strip_running.clear()
        except AttributeError:
            self._strip_running = set()
        # G46-4 批次总览重置（组件内部计数）
        self.batch_overview.reset(n)
        # G70：行 item 尚未建好，不能在这里按名字过滤（全部判空 → 新行永久隐藏）。
        # 过滤统一推迟到 end_bulk()（行已建好之后）；这里只处理 0 行空态。
        if n == 0:
            self._update_empty_download(visible_rows=0)
        elif not self._progress_filter_text.strip():
            self._update_empty_download(visible_rows=n)

    def set_busy_preview(self, busy: bool) -> None:
        """批次准备期显示/隐藏骨架（Rev14 R14-08）。只影响视觉，不建子控件。"""
        bp = getattr(self, "_busy_preview", None)
        if bp is None:
            return
        bp.set_busy(busy)
        if not busy:
            self._update_empty_download()

    def _progress_filter_text_now(self) -> str:
        """当前过滤词（供 `empty_state.DownloadEmptyState` 读取）。"""
        return str(getattr(self, "_progress_filter_text", "") or "").strip()

    def _update_empty_download(self, visible_rows: int | None = None):
        """三态判定（G36-5 / G70 / R14-08）→ 转发 `empty_state.DownloadEmptyState`。"""
        st = getattr(self, "_empty_state", None)
        if st is not None:
            st.update(visible_rows)

    def _add_table_row(self, index, spec: RepoSpec):
        # 关闭排序再插入行，保证 index 与行号对齐（引擎回调按 index 定位）
        bulk = bool(getattr(self, "_bulk_loading", False))
        was_sorting = False if bulk else self.table.isSortingEnabled()
        if was_sorting:
            self.table.setSortingEnabled(False)
        # Rev11：六格构建抽到 progress_rows.py（本文件曾破 800 行红线）
        from .progress_rows import install_row
        install_row(self.table, index, spec)
        if was_sorting:
            self.table.setSortingEnabled(True)

    def begin_bulk(self) -> None:
        """G70：批量插行前关闭排序，避免每插一行都触发一次重排。"""
        self._bulk_loading = True
        self._bulk_sorting_was = self.table.isSortingEnabled()
        if self._bulk_sorting_was:
            self.table.setSortingEnabled(False)

    # --------------------------------------------------------- Rev11 V-21 排序模式
    def _strip_running_now(self) -> bool:
        strip = self._strip()
        return bool(strip is not None and strip.is_running())

    def _on_auto_sort_toggled(self, checked: bool) -> None:
        """「自动排序」切换（S3：切到自动 = 丢弃手动序，不可恢复）。

        状态机在 `sort_mode.py`（本文件 800 行红线；规格见 计划书 §5.3）。
        """
        apply_sort_mode(self.table, self.btn_auto_sort, self._strip_running_now())

    def set_drag_enabled_for_running(self, running: bool) -> None:
        """S4：运行态切换拖拽开关（自动排序模式下始终不可拖）。"""
        apply_sort_mode(self.table, self.btn_auto_sort, running)

    def end_bulk(self) -> None:
        """G70：批量插行结束，恢复排序并按当前关键字重新过滤。"""
        self._bulk_loading = False
        if getattr(self, "_bulk_sorting_was", False):
            self.table.setSortingEnabled(True)
        self._bulk_sorting_was = False
        if self._progress_filter_text.strip():
            self._filter_progress_rows(self._progress_filter_text)

    # --------------------------------------------------------- G02-1/2 搜索与排序

    def _schedule_progress_filter(self, text=""):
        """防抖：停止上一个定时器，200ms 后应用过滤。"""
        self._progress_filter_text = text
        if not hasattr(self, "_pf_timer"):
            from PyQt6.QtCore import QTimer
            self._pf_timer = QTimer(self)
            self._pf_timer.setSingleShot(True)
            self._pf_timer.setInterval(200)
            # G70 修复：原实现用 lambda 捕获**首次** text，之后所有输入都按第一段
            # 文本过滤；且 lambda 捕获 self 形成引用环。改绑定方法读最新状态。
            self._pf_timer.timeout.connect(self._apply_pending_progress_filter)
        self._pf_timer.start()

    def _apply_pending_progress_filter(self):
        """定时器到期：按当前输入框文本应用过滤。"""
        self._filter_progress_rows(getattr(self, "_progress_filter_text", ""))

    def _filter_progress_rows(self, text=""):
        """按仓库名校验过滤进度表行；返回可见行数（供测试）。"""
        self._progress_filter_text = text or ""
        q = (text or "").strip().lower()
        hidden = 0
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            name = it.text() if it else ""
            if q and q not in name.lower():
                self.table.hideRow(r)
                hidden += 1
            else:
                self.table.showRow(r)
        visible = self.table.rowCount() - hidden
        # G70：过滤到 0 行时也要给出空态提示
        self._update_empty_download(visible_rows=visible)
        return visible

    def _on_sort_changed(self, section, order):
        """状态列排序：用 UserRole 存的优先级；其余列默认字典序。"""
        try:
            order_map = {"running": 0, "pending": 1, "success": 2,
                         "conflict": 3, "failed": 4, "cancelled": 5, "skipped": 6}
            if section == 2:
                for r in range(self.table.rowCount()):
                    it = self.table.item(r, 2)
                    if it is not None:
                        # 显示文本含符号后缀（G36-2），按 UserRole 原始状态值映射优先级
                        base = it.data(Qt.ItemDataRole.UserRole) or it.text()
                        pri = order_map.get(base, 99)
                        it.setData(Qt.ItemDataRole.UserRole + 1, pri)
        except Exception:
            pass

    # --------------------------------------------------------- G02-3 单仓库暂停

    def pause_selected(self):
        """暂停进度表中选中的行（仅取消该 worker，其余继续）。

        G21-1：行号只是视觉位置（排序/过滤后与 engine index 脱钩），
        必须从行数据读回真实 index 再暂停。
        """
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            return
        paused = 0
        for r in rows:
            it = self.table.item(r, 0)
            idx = it.data(Qt.ItemDataRole.UserRole + 1) if it is not None else None
            if idx is None:
                idx = r  # 兼容：无标记行退化为旧行为
            if (hasattr(self.owner, "engine") and self.owner.engine is not None
                    and self.owner.engine.pause_task(int(idx))):
                paused += 1
        self.owner._emit_log(_mw._fmt_dt(), LogLevel.WARN,
                       tr("已请求暂停 {n} 个任务（其余继续）…").format(n=paused))
        self.owner.statusBar().showMessage(tr("正在暂停 {n} 个任务…").format(n=paused))

    # --------------------------------------------------------- G35-1 进度表右键菜单

    def _progress_table_menu(self, pos):
        """进度表右键菜单：重试此仓库 / 复制错误详情 / 打开所在目录。

        FAILED/CANCELLED 行可用「重试此仓库」；其余动作对所有行可用。
        """
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        # G46-12：统一构建（重试项置灰+tooltip），不再就地拼菜单
        menu = self._build_progress_menu(row)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _retry_table_row(self, row: int):
        """单仓库重试：把该行 spec 经 _launch 重新调度（其余任务不受影响）。"""
        if self.owner.busy:
            _mw.QMessageBox.information(self.owner, tr("提示"), tr("有任务正在运行，请等本轮结束后重试。"))
            return
        idx = self._row_engine_index(row)
        spec = self.owner.row_specs.get(idx)
        if spec is None:
            return
        target = self.owner.target_edit.text().strip() or str(self.owner.data_dir / "clones")
        self.owner._launch([spec], target_root=Path(target),
                     shallow=False, depth=self.owner.depth_spin.value(), clear_input=False)
        self.owner._emit_log(_mw._fmt_dt(), LogLevel.WARN, tr("已重试仓库：{r}").format(r=spec.display))

    def _copy_row_detail(self, row: int):
        """复制该行错误详情（message + detail）到剪贴板。"""
        msg_item = self.table.item(row, 4)
        detail = ""
        if msg_item is not None:
            detail = msg_item.toolTip() or msg_item.text()
        _QApp.clipboard().setText(detail)
        self.owner.statusBar().showMessage(tr("错误详情已复制"))

    def _open_row_dir(self, row: int):
        """打开该行仓库所在目录（统一走 util/openfolder）。"""
        p = row_repo_dir(self, row)
        if p is None or not p.is_dir():
            _mw.QMessageBox.warning(self.owner, tr("目录不存在"), str(p))
            return
        if not open_folder(p):
            _mw.QMessageBox.warning(self.owner, tr("无法打开目录"), str(p))

    def _row_is_halfcloned(self, row: int) -> bool:
        return looks_halfcloned(self._row_dir(row))

    def _repair_halfcloned_row(self, row: int) -> None:
        """Rev8 P1-1：检出修复（逻辑在 halfclone_actions，本面板只转发）。"""
        repair_halfcloned_row(self, row)

    def _row_dir(self, row: int):
        return row_repo_dir(self, row)

    def _delete_halfcloned_row(self, row: int) -> None:
        """删除半成品目录（二次确认；判定/删除在 halfclone_actions，面板只转发）。"""
        delete_halfcloned_row(self, row)

    def _row_engine_index(self, row: int) -> int:
        """从表格行读回 engine index（UserRole+1；排序/过滤后仍精确）。"""
        it = self.table.item(row, 0)
        idx = it.data(Qt.ItemDataRole.UserRole + 1) if it is not None else None
        return int(idx) if idx is not None else row

    def _row_for_engine_index(self, engine_index: int) -> int | None:
        """按 UserRole+1 里藏的 engine index 反查当前行号（G67-3）。

        排序开启时 `setItem` 会立刻触发重排 —— 行的**视觉位置**不再等于 engine
        index。逐行扫描取回真实行号，避免后续 `table.item(index, col)` 拿到 None。
        """
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            if it is not None and it.data(Qt.ItemDataRole.UserRole + 1) == engine_index:
                return r
        return None

    def _row_for(self, index: int) -> int | None:
        """engine index → 当前真实行号（G69-5）。

        **为什么要这一步**：表格默认开启排序（`setSortingEnabled(True)`），
        而 `_add_table_row` 插入时只是临时关掉、插完立刻恢复 —— 恢复的瞬间
        `sortItems` 就会执行，行的**视觉位置不再等于 engine index**。
        而进度/结果槽原来直接用 `index` 当行号，于是状态被写到别的仓库那一行。

        实测（真实 `start_all` 路径）：3 个仓库时行序 `[(0,zeta),(1,mid),(2,alpha)]`
        而 engine index 是 `{0:zeta,1:alpha,2:mid}` → 引擎报 `index=1`(alpha) 成功，
        实际写到「行1 = mid」，alpha 那行仍显示「等待中」。300 行批量时 300/300 全部错位。
        连带后果：「重试此仓库」会去重试**另一个**仓库（`_row_engine_index` 读回错行的
        UserRole）、暂停选中同理、状态列排序结果无意义。

        先按行内藏的 engine index 反查；查不到（行尚未建出）再退回 index 本身。
        """
        r = self._row_for_engine_index(index)
        if r is not None:
            return r
        if 0 <= index < self.table.rowCount():
            return index
        return None

    def _strip(self):
        """G75：取仪表齿条（宿主没有则返回 None，便于单测直接构造 Panel）。"""
        try:
            return getattr(self.owner, "status_strip", None)
        except Exception:  # pragma: no cover - 宿主已销毁的极端情形
            return None

    @pyqtSlot(int, str)
    def _on_worker_progress(self, index, percent):
        if percent is None or percent == "":
            return
        try:
            value = int(percent)
        except (TypeError, ValueError):
            return
        # 0 是合法进度（重跑仓库从 0 开始），不能按 falsy 丢弃
        self._progress_batch[int(index)] = max(0, min(100, value))
        # G75：首次见到该任务的进度 → 计入"活跃"（排序/过滤无关，按 engine index 记）
        if int(index) not in self._strip_running:
            self._strip_running.add(int(index))
            strip = self._strip()
            if strip is not None:
                strip.note_active(len(self._strip_running))
        # 单次定时器：每个信号重置 250ms 窗口，空闲时不再周期唤醒
        self._progress_timer.start()

    def _flush_progress_batch(self):
        """G70：批量写进度（只更新内存字典 + 重绘视口，不逐行 setData）。

        engine index 与行号的映射由 delegate 在绘制时经列 0 的 UserRole+1 反查，
        因此排序/过滤后仍指向正确仓库。
        """
        batch, self._progress_batch = self._progress_batch, {}
        if not batch:
            return
        for idx, pct in batch.items():
            try:
                self._progress_values[int(idx)] = int(pct)
            except (TypeError, ValueError):
                continue
        try:
            self.table.viewport().update()
        except Exception:
            pass

    # 高频进度详情（速率/对象数）节流：合并为 300ms 批量刷新，32 并发不刷屏主线程

    @pyqtSlot(int, str)
    def _on_worker_progress_detail(self, index, text):
        if not (0 <= index < self.table.rowCount()):
            return
        self._detail_batch[index] = text
        if not self._detail_timer.isActive():
            self._detail_timer.start()

    def _flush_detail_batch(self):
        if not getattr(self, "_detail_batch", None):
            return
        batch, self._detail_batch = self._detail_batch, {}
        for idx, text in batch.items():
            row = self._row_for(idx)          # G69-5：排序态下 index ≠ 行号
            if row is None:
                continue
            item = self.table.item(row, 1)
            if item is not None:
                item.setData(DETAIL_ROLE, str(text))

    @pyqtSlot(int, SyncResult)
    def _on_worker_result(self, index, res: SyncResult):
        # G67-3 修复：重投批次的 index 可能**超出当前行数**（表按上一批预填，而重投
        # 批 spec 更多 —— 例如用户中途清空了部分行）→ `table.item(index, col)` 返回
        # None → `st.setText` 抛 AttributeError，异常从槽里冒出（结果丢失且日志无用）。
        # 越界时按 res.spec 补建该行，保证结果仍落到表里。
        if index >= 0 and index >= self.table.rowCount():
            was_sorting = self.table.isSortingEnabled()
            if was_sorting:
                # 先关排序：开着排序时 setItem 会立即重排，补出来的行会被挤到别处
                self.table.setSortingEnabled(False)
            try:
                self.table.setRowCount(index + 1)
                self._add_table_row(index, res.spec)
            except Exception as e:  # noqa: BLE001 —— 补行失败不应让回调崩溃
                try:
                    from ..app.applog import warn as _warn
                    _warn(f"进度行补建失败（index={index}）：{e}")
                except Exception:
                    pass
            finally:
                if was_sorting:
                    self.table.setSortingEnabled(True)
            # G70：补行后按当前过滤词重新可见性（否则新行可能状态错误）
            if self._progress_filter_text.strip():
                self._filter_progress_rows(self._progress_filter_text)
        # G69-5：**始终**按行内 engine index 反查真实行 —— 排序开启时行号 ≠ index，
        # 直接用 index 会把状态/动作/详情写到别的仓库那一行（实测 300/300 错位）。
        row = self._row_for(index)
        if row is None or not (0 <= row < self.table.rowCount()):
            return  # 补行失败 → 无可写目标，直接放弃（异常路径已留痕）
        bar = self.table.item(row, 1)
        if bar is not None:
            # G70：终态优先 —— 清掉可能还在批里的旧进度，避免随后被覆盖
            try:
                self._progress_batch.pop(int(index), None)
                self._progress_values[int(index)] = 100
            except (TypeError, ValueError):
                pass
        pal = current_palette()
        # G75：文案走 result_labels（唯一来源，含 tr()），颜色走 status_pill（唯一来源）
        from .result_labels import action_label as _action_text
        from .result_labels import status_label as _status_text
        from .status_pill import color_for as _color_for

        raw_status = str(getattr(res.status, "value", res.status))
        label = _status_text(raw_status)
        color = (_color_for(raw_status, pal) if raw_status in
                 ("success", "failed", "cancelled", "conflict", "skipped",
                  "running", "pending") else pal["text"])
        # G22-4：托盘计数（成功/失败分流；冲突/失败归 bad，其余终态归 ok）
        try:
            if getattr(self.owner, "tray", None) is not None:
                if res.status in (SyncStatus.FAILED, SyncStatus.CONFLICT):
                    self.owner.tray.update_counts(bad_delta=1)
                else:
                    self.owner.tray.update_counts(ok_delta=1)
        except Exception:
            pass
        st = self.table.item(row, 2)
        if st is None:
            return  # 行存在但单元格缺失（表被外部重建）—— 不崩，交由下一次刷新
        st.setText(label)
        st.setForeground(QColor(color))
        # 状态原始值存 UserRole：排序/右键/统计按原始值判断（符号仅视觉）
        st.setData(Qt.ItemDataRole.UserRole, res.status.value)
        # G75：终态 → 齿条累加成功/失败，活跃回落
        self._strip_running.discard(int(index))
        strip = self._strip()
        if strip is not None:
            strip.note_result(getattr(res.status, "value", str(res.status)))
            strip.note_active(len(self._strip_running))
        # G35-8 状态格 tooltip：message + detail 拼接（与详情列 tooltip 互补）
        st.setToolTip(f"{res.message or ''}" + (f"\n{res.detail or ''}" if res.detail else ""))
        act = self.table.item(row, 3)
        action_label = _action_text(res.action, res.commits)
        if act is not None:
            act.setText(action_label)
            act.setForeground(QColor(color))
        msg = self.table.item(row, 4)
        if msg is not None:
            msg.setText(res.message)
            msg.setToolTip(res.detail or "")
        # Rev11（V-16）：耗时列 —— duration_ms 由 worker 计时（SyncResult 既有字段）。
        # 口径：毫秒 → 「x.xs」/「m:ss」；0/缺省显示 —（不显示假 0）。
        dur = self.table.item(row, 5)
        if dur is not None:
            dur.setText(_format_duration(getattr(res, "duration_ms", 0)))
        # G36-6 完成动效：成功绿/失败红背景色 1.2s 消隐
        # （设置开关；offscreen 自动关；G46-8 prefers_reduced_motion 也关）
        if bool(getattr(self.owner.settings, "animations", True)) and \
                not self._on_worker_result_motion_guard() and \
                os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            try:
                self._animate_result_row(row, res.status)
            except Exception:
                pass
        # 同步成功 → 地址入 URL 历史（G02-4）
        try:
            if res.status == SyncStatus.SUCCESS:
                self.owner.url_history.add(res.spec.url_https)
        except Exception:
            pass
        # 记录到 DB 由 worker 内部完成
        self.owner._emit_log(_mw._fmt_dt(), LogLevel.INFO if res.status == SyncStatus.SUCCESS else LogLevel.WARN,
                        f"[{index}] {label}：{res.message}（{action_label}）")
        # G46-4 批次总览累加
        self.batch_overview.on_result(res.status)

    def _animate_result_row(self, index: int, status: SyncStatus):
        """G36-6 完成动效：成功绿/失败红背景色 1.2s 淡出到主题底色。

        Rev11：实现抽到 `row_anim.py`（本文件曾破 800 行红线），本方法只做转发 ——
        `test_g36_ui.py:162` 直接调它，保留同名签名行为零变化。
        """
        from .row_anim import animate_result_row
        animate_result_row(self, index, status, SyncStatus)

    # ------------------------------------------------------------ 槽

    def _on_engine_line(self, index, text, level):
        self.owner._emit_log(_mw._fmt_dt(), LogLevel(level or "info"),
                       f"[{index}] {text}" if index is not None else text)

    @pyqtSlot()
    def _on_engine_finished(self):
        if not self.owner.busy:
            return
        # G75：整批结束 → 齿条停表并冻结用时
        strip = self._strip()
        if strip is not None:
            strip.end()
        done = 0
        ok = fail = conflict = skip = 0
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 2)
            if not it:
                continue
            # 用 UserRole 存的原始状态值统计（显示文本含符号后缀，不再做字符串匹配）
            t = it.data(Qt.ItemDataRole.UserRole) or it.text()
            if t in ("success", "failed", "cancelled", "conflict", "skipped"):
                done += 1
                if t == "success":
                    ok += 1
                elif t == "conflict":
                    conflict += 1
                elif t == "failed":
                    fail += 1
                elif t == "skipped":
                    # Rev14 F2：断点续传跳过的仓库现在会到终态，必须单独计数 ——
                    # 否则「成功 0 · 冲突 0 · 失败 0」看起来像什么都没发生。
                    skip += 1
        self.owner.busy = False
        self._reset_buttons()
        try:
            if getattr(self.owner, "tray", None) is not None:
                self.owner.tray.update_counts(reset=True)  # 空闲态 tooltip（G22-4）
        except Exception:
            pass
        # Rev14 F2/F1：播报（状态栏/日志/托盘/Webhook）抽到 `batch_finish.announce_finished`
        # —— 本文件已贴 800 行红线，而"整批结果的播报"本来也不是进度表的职责。
        from .batch_finish import announce_finished
        announce_finished(self.owner, ok=ok, conflict=conflict, fail=fail,
                          skip=skip, table=self.table)
        # 下载完成自动清空输入框
        if getattr(self.owner, "_clear_after_finish", False):
            self.owner.repo_input.clear()
            self.owner._emit_log(_mw._fmt_dt(), LogLevel.SYSTEM, tr("下载完成，已自动清空输入框。"))
        self.owner._load_db_into_grid()
        # G35-2 完成提示音（设置开关默认开；QApplication.beep 无 UI 影响）
        try:
            if bool(getattr(self.owner.settings, "finish_sound", True)):
                from PyQt6.QtWidgets import QApplication as _QApp
                _QApp.beep()
        except Exception:
            pass
        # 多根目录继任：还有下一组则继续（QTimer 调度避免嵌套重入）
        if getattr(self.owner, "_multi_root_relay", False) and self.owner._multi_root_update:
            QTimer.singleShot(0, self.owner._update_next_root)
        else:
            self.owner._multi_root_relay = False

    def _reset_buttons(self):
        self.owner.btn_start.setEnabled(True)
        self.owner.btn_cancel.setEnabled(False)
        # C2-6：批次结束后「暂停」按钮失效并把文案复位（否则会停在「继续」）
        try:
            self.owner.btn_pause_batch.setEnabled(False)
            self.owner.btn_pause_batch.setText(_mw.tr("暂停"))
        except Exception as e:  # noqa: BLE001 —— 复位是收尾动作，缺按钮不该拖垮收尾
            from ..app.applog import warn as _warn
            _warn(f"批次结束后复位「暂停」按钮失败：{e}")
        self.owner.btn_update_all.setEnabled(True)
        self.owner.btn_cancel_manage.setEnabled(False)
        self.owner.repo_input.setEnabled(True)
        self.owner.mode_combo.setEnabled(True)
        self.owner.depth_spin.setEnabled(self.owner.mode_combo.currentIndex() in (1, 2))

    def _any_cancel(self) -> bool:
        if hasattr(self.owner, "engine") and self.owner.engine is not None:
            return self.owner.engine.is_cancelled()
        return any(f() for f in getattr(self, "flags", {}).values())

    # --------------------------------------------------------- G36-8 全局热键

    def _hotkey_start_cancel(self):
        """Ctrl+Alt+S：空闲→开始全部；运行中→取消全部。"""
        if self.owner.busy:
            self.owner.cancel_all()
        else:
            self.owner.start_all()

    def _hotkey_show_window(self):
        """Ctrl+Alt+M：显示并激活主窗口（托盘最小化后可唤回）。"""
        try:
            self.owner.show()
            self.owner.raise_()
            self.owner.activateWindow()
        except Exception:
            pass

    # ------------------------------------------------------------ 管理页

    # ------------------------------------------------------------ G46-4 批次总览
    # ------------------------------------------------------------ G46-12 进度右键菜单统一
    def _build_progress_menu(self, row: int):
        """进度表右键菜单（构建移入 `progress_menu.py`，行为不变）。"""
        return build_progress_menu(self, row)

    def _on_worker_result_motion_guard(self) -> bool:
        """G46-8：prefers_reduced_motion 开启时禁用完成动效。"""
        try:
            if bool(getattr(self.owner.settings, "prefers_reduced_motion", False)):
                return True
        except Exception:
            pass
        return False
