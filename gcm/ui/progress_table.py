"""G45-6 进度表面板（QWidget 子类）：进度表 + 空态 overlay + 过滤框 +
进度/结果回填。持有 owner（MainWindow）引用访问窗口服务；对外方法名与
MainWindow 保持一致（MainWindow 保留同名转发，行为零变化）。
"""
from __future__ import annotations

import os
import sys
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
        self.owner = owner
        # 高频进度详情节流：速率/对象数批量刷新（32 并发时不阻塞主线程）
        self._detail_batch: dict = {}
        self._detail_timer = QTimer(self)
        self._detail_timer.setInterval(300)
        self._detail_timer.timeout.connect(self._flush_detail_batch)
        self._progress_filter_text = ""
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setSpacing(10)
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
        name_item = self.table.item(row, 0)
        status_item = self.table.item(row, 2)
        if name_item is None or status_item is None:
            return
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        # 用 UserRole 存的原始状态键判断（符号只是视觉后缀，不参与逻辑）
        status_key = status_item.data(Qt.ItemDataRole.UserRole) or ""
        if status_key in ("failed", "cancelled"):
            act_retry = menu.addAction("⟳ 重试此仓库")
            act_retry.triggered.connect(
                lambda _=False, r=row: self._retry_table_row(r))
            menu.addSeparator()
        act_copy = menu.addAction("复制错误详情")
        act_copy.triggered.connect(
            lambda _=False, r=row: self._copy_row_detail(r))
        act_open = menu.addAction("打开所在目录")
        act_open.triggered.connect(
            lambda _=False, r=row: self._open_row_dir(r))
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

    @pyqtSlot(int, str)
    def _on_worker_progress(self, index, percent):
        if not (0 <= index < self.table.rowCount()):
            return
        bar = self.table.cellWidget(index, 1)
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
            w = self.table.cellWidget(idx, 1)
            if isinstance(w, QProgressBar):
                try:
                    w.setFormat(f"%p%  {text}")
                except Exception:
                    pass

    @pyqtSlot(int, SyncResult)
    def _on_worker_result(self, index, res: SyncResult):
        bar = self.table.cellWidget(index, 1)
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
        st = self.table.item(index, 2)
        st.setText(label)
        st.setForeground(QColor(color))
        # 状态原始值存 UserRole：排序/右键/统计按原始值判断（符号仅视觉）
        st.setData(Qt.ItemDataRole.UserRole, res.status.value)
        # G35-8 状态格 tooltip：message + detail 拼接（与详情列 tooltip 互补）
        st.setToolTip(f"{res.message or ''}" + (f"\n{res.detail or ''}" if res.detail else ""))
        act = self.table.item(index, 3)
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
        act.setText(action_label)
        act.setForeground(QColor(color))
        msg = self.table.item(index, 4)
        msg.setText(res.message)
        msg.setToolTip(res.detail or "")
        # G36-6 完成动效：成功绿/失败红背景色 1.2s 消隐（设置开关；offscreen 自动关）
        if bool(getattr(self.owner.settings, "animations", True)) and \
                os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            try:
                self._animate_result_row(index, res.status)
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
