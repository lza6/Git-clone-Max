"""G45-6 进度表面板（QWidget 子类）：进度表 + 空态 overlay + 过滤框 +
进度/结果回填。持有 owner（MainWindow）引用访问窗口服务；对外方法名与
MainWindow 保持一致（MainWindow 保留同名转发，行为零变化）。
"""
from __future__ import annotations

import os
import sys
import weakref
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..models import RepoSpec, SyncResult, SyncStatus
from . import main_window as _mw  # 兼容测试对 main_window.QMessageBox 等模块级 patch
from .theme import PALETTE, LogLevel


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
        # G46-4 批次总览条：已完成 x/总 y · 成功率 %（迷你进度条）
        overview = QHBoxLayout()
        overview.setSpacing(8)
        self.batch_total_lbl = QLabel("批次：—")
        self.batch_total_lbl.setObjectName("muted")
        self.batch_bar = QProgressBar()
        self.batch_bar.setRange(0, 100)
        self.batch_bar.setValue(0)
        self.batch_bar.setFixedWidth(180)
        self.batch_bar.setTextVisible(False)
        overview.addWidget(self.batch_total_lbl)
        overview.addWidget(self.batch_bar, 1)
        overview.addStretch()
        v.addLayout(overview)
        # 进度表
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["仓库", "进度", "状态", "本次", "详情"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(200)
        # G02-2 表头点击排序（状态列优先级：运行>等待>成功>冲突>失败>取消>跳过）
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._on_sort_changed)
        # G35-1 进度表右键菜单：重试此仓库 / 复制错误详情 / 打开所在目录
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._progress_table_menu)
        v.addWidget(self.table, 3)
        # G36-5 下载中心空态引导：overlay 标签（绝对定位在表格上，有数据时隐藏）
        self._empty_download = QLabel("⬇ 粘贴仓库地址开始下载\n（支持多行 / 拖入 txt 文件）")
        self._empty_download.setObjectName("muted")
        self._empty_download.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_download.setWordWrap(True)
        self._empty_download.setVisible(False)
        self._empty_download.setParent(self.table)
        self._empty_download.raise_()

        # G02-1 搜索过滤框（防抖 200ms）+ 进度表右键「暂停此项」（G02-3）
        row_tools = QHBoxLayout()
        row_tools.addWidget(QLabel("搜索："))
        self.progress_filter = QLineEdit()
        self.progress_filter.setPlaceholderText("按仓库名过滤…")
        self.progress_filter.setClearButtonEnabled(True)
        self.progress_filter.textChanged.connect(self._schedule_progress_filter)
        row_tools.addWidget(self.progress_filter, 1)
        btn_pause_selected = QPushButton("⏸ 暂停选中")
        btn_pause_selected.clicked.connect(self.pause_selected)
        row_tools.addWidget(btn_pause_selected)
        row_tools.addStretch()
        v.addLayout(row_tools)




    def _prepare_table(self, n):
        # 排序开启下先关掉再重建，避免插入行时被自动重排打乱 index
        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(n)
        self.table.setSortingEnabled(was_sorting)
        # G46-4 批次总览重置
        self._reset_batch_overview(n)
        # 过滤状态保持
        self._filter_progress_rows(getattr(self, "_progress_filter_text", ""))
        # G36-5 空态引导：无行 → 显示 overlay
        self._update_empty_download()

    def _update_empty_download(self):
        """G36-5 下载中心空态：进度表无可见行时显示引导 overlay。"""
        try:
            ov = getattr(self, "_empty_download", None)
            if ov is None:
                return
            visible = self.table.rowCount() == 0
            ov.setVisible(visible)
            if visible:
                ov.setGeometry(self.table.rect())
                ov.raise_()
        except Exception:
            pass

    def _add_table_row(self, index, spec: RepoSpec):
        # 关闭排序再插入行，保证 index 与行号对齐（引擎回调按 index 定位）
        was_sorting = self.table.isSortingEnabled()
        if was_sorting:
            self.table.setSortingEnabled(False)
        name_item = QTableWidgetItem(spec.folder_name)
        name_item.setToolTip(spec.url_https)
        name_item.setData(Qt.ItemDataRole.UserRole, spec.folder_name)  # 供过滤
        # G21-1：行内保存 engine index，排序/过滤后暂停仍能定位正确任务
        name_item.setData(Qt.ItemDataRole.UserRole + 1, int(index))
        self.table.setItem(index, 0, name_item)
        prog = QProgressBar()
        prog.setRange(0, 0)
        prog.setFormat("%p%")
        self.table.setCellWidget(index, 1, prog)
        status_item = QTableWidgetItem("等待中")
        status_item.setData(Qt.ItemDataRole.UserRole, "pending")  # 供排序
        status_item.setForeground(QColor(PALETTE["text_dim"]))
        self.table.setItem(index, 2, status_item)
        act_item = QTableWidgetItem("—")
        act_item.setForeground(QColor(PALETTE["text_dim"]))
        self.table.setItem(index, 3, act_item)
        msg_item = QTableWidgetItem("—")
        msg_item.setForeground(QColor(PALETTE["text_dim"]))
        self.table.setItem(index, 4, msg_item)
        if was_sorting:
            self.table.setSortingEnabled(True)

    # --------------------------------------------------------- G02-1/2 搜索与排序

    def _schedule_progress_filter(self, text=""):
        """防抖：停止上一个定时器，200ms 后应用过滤。"""
        self._progress_filter_text = text
        if not hasattr(self, "_pf_timer"):
            from PyQt6.QtCore import QTimer
            self._pf_timer = QTimer(self)
            self._pf_timer.setSingleShot(True)
            self._pf_timer.setInterval(200)
            self._pf_timer.timeout.connect(lambda: self._filter_progress_rows(text))
        self._pf_timer.start()

    def _filter_progress_rows(self, text=""):
        """按仓库名校验过滤进度表行；返回可见行数（供测试）。"""
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
        return self.table.rowCount() - hidden

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
                       f"已请求暂停 {paused} 个任务（其余继续）…")
        self.owner.statusBar().showMessage(f"正在暂停 {paused} 个任务…")

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
            _mw.QMessageBox.information(self.owner, "提示", "有任务正在运行，请等本轮结束后重试。")
            return
        idx = self._row_engine_index(row)
        spec = self.owner.row_specs.get(idx)
        if spec is None:
            return
        target = self.owner.target_edit.text().strip() or str(self.owner.data_dir / "clones")
        self.owner._launch([spec], target_root=Path(target),
                     shallow=False, depth=self.owner.depth_spin.value(), clear_input=False)
        self.owner._emit_log(_mw._fmt_dt(), LogLevel.WARN, f"已重试仓库：{spec.display}")

    def _copy_row_detail(self, row: int):
        """复制该行错误详情（message + detail）到剪贴板。"""
        msg_item = self.table.item(row, 4)
        detail = ""
        if msg_item is not None:
            detail = msg_item.toolTip() or msg_item.text()
        from PyQt6.QtWidgets import QApplication as _QApp
        _QApp.clipboard().setText(detail)
        self.owner.statusBar().showMessage("错误详情已复制")

    def _open_row_dir(self, row: int):
        """打开该行仓库所在目录（Windows startfile / POSIX 打开器）。"""
        idx = self._row_engine_index(row)
        spec = self.owner.row_specs.get(idx)
        if spec is None:
            return
        if spec.local_path:
            p = Path(spec.local_path)
        else:
            p = Path(self.owner.target_edit.text().strip() or self.owner.data_dir / "clones") / spec.folder_name
        if not p.is_dir():
            _mw.QMessageBox.warning(self.owner, "目录不存在", str(p))
            return
        if sys.platform == "win32":
            os.startfile(str(p))  # noqa
        else:
            import shutil
            openers = ("xdg-open", "open")
            for op in openers:
                if shutil.which(op):
                    import subprocess
                    subprocess.Popen([op, str(p)])
                    break

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

    @pyqtSlot(int, str)
    def _on_worker_progress(self, index, percent):
        row = self._row_for(index)
        if row is None:
            return
        bar = self.table.cellWidget(row, 1)
        if isinstance(bar, QProgressBar) and percent:
            try:
                bar.setRange(0, 100)
                bar.setValue(int(percent))
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
            w = self.table.cellWidget(row, 1)
            if isinstance(w, QProgressBar):
                try:
                    w.setFormat(f"%p%  {text}")
                except Exception:
                    pass

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
        # G69-5：**始终**按行内 engine index 反查真实行 —— 排序开启时行号 ≠ index，
        # 直接用 index 会把状态/动作/详情写到别的仓库那一行（实测 300/300 错位）。
        row = self._row_for(index)
        if row is None or not (0 <= row < self.table.rowCount()):
            return  # 补行失败 → 无可写目标，直接放弃（异常路径已留痕）
        bar = self.table.cellWidget(row, 1)
        if isinstance(bar, QProgressBar):
            bar.setRange(0, 1)
            bar.setValue(1)
        status_map = {
            SyncStatus.SUCCESS: (PALETTE["accent2"], "成功 ✓"),
            SyncStatus.FAILED: (PALETTE["error"], "失败 ✕"),
            SyncStatus.CANCELLED: (PALETTE["warning"], "已取消 ⊘"),
            SyncStatus.CONFLICT: (PALETTE["warning"], "冲突 ⚠"),
            SyncStatus.SKIPPED: (PALETTE["text_dim"], "跳过 →"),
            SyncStatus.RUNNING: (PALETTE["accent"], "更新中…"),
        }
        color, label = status_map.get(res.status, (PALETTE["text"], str(res.status.value)))
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
        # G35-8 状态格 tooltip：message + detail 拼接（与详情列 tooltip 互补）
        st.setToolTip(f"{res.message or ''}" + (f"\n{res.detail or ''}" if res.detail else ""))
        act = self.table.item(row, 3)
        action_label = {
            "cloned": "新建克隆",
            "updated": f"增量 +{res.commits}",
            "fetched": "已最新",
            "empty": "空仓库",
            "skipped": "跳过",
            "conflict": "冲突保留",
            "cancelled": "取消",
            "failed": "失败",
        }.get(res.action.value, res.action.value)
        if act is not None:
            act.setText(action_label)
            act.setForeground(QColor(color))
        msg = self.table.item(row, 4)
        if msg is not None:
            msg.setText(res.message)
            msg.setToolTip(res.detail or "")
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
        self._on_batch_result(res.status)

    def _animate_result_row(self, index: int, status: SyncStatus):
        """G36-6 完成动效：成功绿/失败红背景色 1.2s 淡出到主题底色。

        仅在非 offscreen（有真实渲染）且设置开启时由 _on_worker_result 调用。
        动效结束后背景复位为主题面板色，不影响排序/过滤的 UserRole 数据。
        """
        try:
            from PyQt6.QtCore import QVariantAnimation
            from PyQt6.QtGui import QBrush
            st = self.table.item(index, 2)
            if st is None:
                return
            flash = PALETTE["accent2"] if status == SyncStatus.SUCCESS else PALETTE["error"]
            start_color = QColor(flash)
            base_color = QColor(PALETTE["panel"])
            anim = QVariantAnimation(self)
            anim.setDuration(1200)
            anim.setStartValue(start_color)
            anim.setEndValue(base_color)
            anim.valueChanged.connect(
                lambda c: st.setBackground(QBrush(QColor(c))))
            anim.finished.connect(
                lambda: st.setBackground(QBrush(base_color)))
            # 保留引用防止被 GC（存到窗口级列表）
            if not hasattr(self.owner, "_row_anims"):
                self.owner._row_anims = []
            self.owner._row_anims.append(anim)
            anim.finished.connect(lambda: self.owner._row_anims.remove(anim))
            anim.start()
        except Exception:
            pass

    # ------------------------------------------------------------ 槽

    def _on_engine_line(self, index, text, level):
        self.owner._emit_log(_mw._fmt_dt(), LogLevel(level or "info"),
                       f"[{index}] {text}" if index is not None else text)

    @pyqtSlot()
    def _on_engine_finished(self):
        if not self.owner.busy:
            return
        done = 0
        ok = fail = conflict = 0
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
        self.owner.busy = False
        self._reset_buttons()
        try:
            if getattr(self.owner, "tray", None) is not None:
                self.owner.tray.update_counts(reset=True)  # 空闲态 tooltip（G22-4）
        except Exception:
            pass
        self.owner.statusBar().showMessage(
            f"完成：成功 {ok} · 冲突 {conflict} · 失败 {fail}")
        completed = f"成功 {ok} · 冲突 {conflict} · 失败 {fail}"
        self.owner._emit_log(_mw._fmt_dt(), LogLevel.SYSTEM, f"全部任务结束：{completed}")
        # 下载完成自动清空输入框
        if getattr(self.owner, "_clear_after_finish", False):
            self.owner.repo_input.clear()
            self.owner._emit_log(_mw._fmt_dt(), LogLevel.SYSTEM, "下载完成，已自动清空输入框。")
        self.owner._load_db_into_grid()
        # 全部完成系统通知（托盘存在时）
        try:
            if getattr(self.owner, "tray", None) is not None:
                self.owner.tray.notify(
                    "全部任务完成",
                    f"成功 {ok} · 冲突 {conflict} · 失败 {fail}")
        except Exception:
            pass
        # G49-7 完成通知 Webhook：显式开启 + URL 非空才发送；后台线程，失败不影响主流程
        try:
            failed_names = []
            for i in range(self.table.rowCount()):
                it0 = self.table.item(i, 0)
                it2 = self.table.item(i, 2)
                if it2 and (it2.data(Qt.ItemDataRole.UserRole) or it2.text()) == "failed" and it0:
                    failed_names.append(it0.text())
            _s = getattr(self.owner, "settings", None)
            if _s is not None:
                _params = (ok, fail, conflict, 0, ok + fail + conflict, failed_names)

                def _kl():
                    try:
                        from ..app.notify import maybe_notify as _mn
                        # G67-6：传 data_dir 以便写审计台账（notify.log）
                        if _mn(_s, *_params,
                               data_dir=getattr(self.owner, "data_dir", None)):
                            # G67-2：工作线程不得直接触碰 QWidget。原实现此处调
                            # owner.statusBar().showMessage(...) —— 改为 emit 信号，
                            # 由 GUI 线程的槽更新状态栏（Qt 自动走 QueuedConnection）。
                            try:
                                self.owner.notify_sent.emit("完成通知 Webhook 已发送")
                            except Exception:
                                pass
                    except Exception:
                        pass

                import threading
                threading.Thread(target=_kl, daemon=True).start()
        except Exception:
            pass
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
    def _reset_batch_overview(self, total: int):
        """G46-4：新批次开始时重置总览计数。"""
        self._batch_total = int(total or 0)
        self._batch_done = 0
        self._batch_ok = 0
        self._render_batch_overview()

    def _on_batch_result(self, status: SyncStatus):
        """G46-4：单个终态结果到达后累加并刷新总览。"""
        self._batch_done = getattr(self, "_batch_done", 0) + 1
        if status == SyncStatus.SUCCESS:
            self._batch_ok = getattr(self, "_batch_ok", 0) + 1
        self._render_batch_overview()

    def _render_batch_overview(self):
        """渲染「已完成 x/总 y · 成功率 %」+ 迷你进度条。"""
        total = getattr(self, "_batch_total", 0)
        done = getattr(self, "_batch_done", 0)
        ok = getattr(self, "_batch_ok", 0)
        try:
            if total <= 0:
                self.batch_total_lbl.setText("批次：—")
                self.batch_bar.setValue(0)
                return
            pct = min(100, round(done / total * 100))
            rate = round(ok / done * 100) if done else 0
            self.batch_total_lbl.setText(f"已完成 {done}/{total} · 成功率 {rate}%")
            self.batch_bar.setValue(pct)
        except Exception:
            pass

    # ------------------------------------------------------------ G46-12 进度右键菜单统一
    def _build_progress_menu(self, row: int):
        """构造进度表右键菜单（不 exec，便于离屏测试）。

        重试项始终显示：不可用时置灰 + tooltip 说明（G46-12 统一语义）。
        """
        from PyQt6.QtWidgets import QMenu
        name_item = self.table.item(row, 0)
        status_item = self.table.item(row, 2)
        menu = QMenu(self)
        if name_item is None or status_item is None:
            return menu
        status_key = status_item.data(Qt.ItemDataRole.UserRole) or ""
        act_retry = menu.addAction("⟳ 重试此仓库")
        retryable = status_key in ("failed", "cancelled")
        act_retry.setEnabled(retryable)
        act_retry.setToolTip("仅失败/已取消行可重试" if not retryable else "重新调度该仓库")
        act_retry.triggered.connect(
            lambda _=False, r=row: self._retry_table_row(r))
        menu.addSeparator()
        act_copy = menu.addAction("复制错误详情")
        act_copy.triggered.connect(
            lambda _=False, r=row: self._copy_row_detail(r))
        act_open = menu.addAction("打开所在目录")
        act_open.triggered.connect(
            lambda _=False, r=row: self._open_row_dir(r))
        return menu

    def _on_worker_result_motion_guard(self) -> bool:
        """G46-8：prefers_reduced_motion 开启时禁用完成动效。"""
        try:
            if bool(getattr(self.owner.settings, "prefers_reduced_motion", False)):
                return True
        except Exception:
            pass
        return False

