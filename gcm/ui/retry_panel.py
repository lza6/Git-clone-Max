"""G71-4 重试队列面板：把「失败后自动重投」从日志里搬到可操作的表里。

参考项目 SurgeDM/Surge 的可观测性要点：队列/重试不是黑盒 —— 用户能看到
**哪条任务、失败几次、下次什么时候重试、为什么失败、该做什么**，并能立即重试
或放弃。主项目此前只有日志行「已加入失败重试队列」，用户无法查看或干预。

数据源优先级：
1. `owner.engine.retry_queue`（运行中的引擎实例，实时）；
2. `<data_dir>/retry_queue.json`（窗口刚启动/引擎未创建时直接读盘）。

本模块不修改队列结构，所有写操作都委托给 `app/retry_queue.py`。
"""
from __future__ import annotations

import time
import weakref
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..app.errors import category_label, classify_error
from ..i18n import tr

_COLS = ("仓库", "次数", "下次重试", "类别", "为什么 / 怎么办", "状态")


_STATE_LABELS = {
    "queued": "排队中",
    "due": "待重试",
    "retrying": "重试中",
    "dropped": "已放弃",
}


def _fmt_ts(ts: float) -> str:
    """把 epoch 秒格式化为本地时间；0/非法 → 「现在」。"""
    try:
        value = float(ts)
    except (TypeError, ValueError):
        return tr("现在")
    if value <= 0:
        return tr("现在")
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(value))
    except (OverflowError, OSError, ValueError):
        return tr("未知")


def retry_rows(queue: Any) -> list[dict[str, Any]]:
    """从队列对象取出展示行（兼容旧记录：缺 category 时现场分类）。

    `queue` 只要求有 `snapshot()` 或 `list_entries()`；两者都没有 → 空列表。
    """
    if queue is None:
        return []
    rows: list[dict[str, Any]] = []
    try:
        if hasattr(queue, "snapshot"):
            rows = list(queue.snapshot())
        elif hasattr(queue, "list_entries"):
            rows = list(queue.list_entries())
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for rec in rows:
        if not isinstance(rec, dict):
            continue
        cat = str(rec.get("category") or "")
        if not cat:
            info = classify_error(str(rec.get("message") or ""),
                                  str(rec.get("detail") or ""))
            cat = info.category
            rec = {**rec, "title": rec.get("title") or info.title,
                   "cause": rec.get("cause") or info.cause,
                   "action": rec.get("action") or info.action}
        out.append({**rec, "category": cat,
                    "category_label": category_label(cat), "category_text": tr(category_label(cat))})
    return out


class RetryQueuePanel(QWidget):
    """重试队列表格 + 操作按钮。可脱离主窗口单独构造（测试用）。"""

    retried = pyqtSignal(int)      # 已请求立即重试的条数
    changed = pyqtSignal()         # 队列被修改（供宿主刷新计数）

    def __init__(self, owner=None, queue: Any = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._owner_ref = (weakref.ref(owner) if owner is not None
                           else (lambda: None))
        self._explicit_queue = queue
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(2000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()

    @property
    def owner(self):
        return self._owner_ref()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setSpacing(8)
        self.summary_lbl = QLabel(tr("重试队列：—"))
        self.summary_lbl.setObjectName("muted")
        v.addWidget(self.summary_lbl)

        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in _COLS])
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAccessibleName(tr("失败重试队列表"))
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton(tr("刷新"))
        self.btn_refresh.clicked.connect(self.refresh)
        row.addWidget(self.btn_refresh)
        # G73：DLQ 视图（已放弃任务可查看并重新入队）
        self.ck_show_dropped = QCheckBox(tr("显示已放弃"))
        self.ck_show_dropped.setToolTip(
            tr("查看超过最大重试次数的任务；可单条重新入队（重置尝试次数）"))
        self.ck_show_dropped.toggled.connect(self.refresh)
        row.addWidget(self.ck_show_dropped)
        self.btn_retry = QPushButton(tr("立即重试选中"))
        self.btn_retry.setObjectName("primary")
        self.btn_retry.clicked.connect(self.retry_selected)
        row.addWidget(self.btn_retry)
        self.btn_requeue = QPushButton(tr("重新入队选中"))
        self.btn_requeue.setToolTip(tr("把已放弃任务放回重试队列（attempts 重置）"))
        self.btn_requeue.clicked.connect(self.requeue_selected)
        row.addWidget(self.btn_requeue)
        self.btn_remove = QPushButton(tr("移除选中"))
        self.btn_remove.clicked.connect(self.remove_selected)
        row.addWidget(self.btn_remove)
        self.btn_retry_all = QPushButton(tr("全部立即重试"))
        self.btn_retry_all.clicked.connect(self.retry_all)
        row.addWidget(self.btn_retry_all)
        self.btn_clear = QPushButton(tr("清空队列"))
        self.btn_clear.clicked.connect(self.clear_queue)
        row.addWidget(self.btn_clear)
        row.addStretch(1)
        v.addLayout(row)

    # ------------------------------------------------------------ 数据源
    def queue(self):
        """取当前队列对象：显式注入 > 运行中引擎 > 读盘。"""
        if self._explicit_queue is not None:
            return self._explicit_queue
        owner = self.owner
        if owner is None:
            return None
        eng = getattr(owner, "engine", None)
        q = getattr(eng, "retry_queue", None) if eng is not None else None
        if q is not None:
            return q
        try:
            from ..app.retry_queue import RetryQueue
            data_dir = getattr(owner, "data_dir", None)
            if data_dir is None:
                return None
            # G71-11（审查 R5）：兜底实例的 enabled 必须跟随设置，否则
            # 「自动重试未启用」时用户点立即重试只会得到假成功。
            enabled = bool(getattr(getattr(owner, "settings", None),
                                   "retry_enabled", False))
            return RetryQueue(Path(data_dir) / "retry_queue.json", enabled=enabled)
        except Exception:
            return None

    def _enabled(self) -> bool:
        """当前是否真的会自动重投（显式队列优先，其次设置/引擎队列）。"""
        if self._explicit_queue is not None:
            return bool(getattr(self._explicit_queue, "enabled", True))
        q = self.queue()
        if q is not None:
            return bool(getattr(q, "enabled", True))
        owner = self.owner
        return bool(getattr(getattr(owner, "settings", None), "retry_enabled", False))

    def _selected_keys(self) -> list[str]:
        keys: list[str] = []
        for idx in sorted({i.row() for i in self.table.selectedIndexes()}):
            item = self.table.item(idx, 0)
            key = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
            if key:
                keys.append(str(key))
        return keys

    # ------------------------------------------------------------ 刷新
    def _dropped_mode(self) -> bool:
        return bool(self.ck_show_dropped.isChecked())

    def refresh(self) -> int:
        """重新读取队列并渲染；返回行数。"""
        q = self.queue()
        dropped = self._dropped_mode()
        if dropped:
            try:
                raw = list(q.dropped(200)) if q is not None else []
            except Exception:
                raw = []
            rows = []
            for rec in raw:
                if not isinstance(rec, dict):
                    continue
                cat = str(rec.get("category") or "other")
                rows.append({**rec, "category": cat,
                             "category_text": tr(category_label(cat)),
                             "next_retry_at": 0})
        else:
            rows = retry_rows(q)
        self.table.setHorizontalHeaderItem(
            2, QTableWidgetItem(tr("时间") if dropped else tr("下次重试")))
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for i, rec in enumerate(rows):
            name = QTableWidgetItem(str(rec.get("display") or rec.get("key") or ""))
            name.setData(Qt.ItemDataRole.UserRole, str(rec.get("key") or ""))
            name.setToolTip(str(rec.get("message") or ""))
            self.table.setItem(i, 0, name)
            self.table.setItem(i, 1, QTableWidgetItem(str(rec.get("attempts", 0))))
            when = rec.get("time") if dropped else _fmt_ts(rec.get("next_retry_at", 0))
            self.table.setItem(i, 2, QTableWidgetItem(str(when or "—")))
            self.table.setItem(i, 3, QTableWidgetItem(str(rec.get("category_text") or "")))
            why = "\n".join(x for x in (
                tr(str(rec.get("title") or "")),
                tr(str(rec.get("cause") or "")),
                tr(str(rec.get("action") or ""))) if x)
            why_item = QTableWidgetItem(why or str(rec.get("message") or ""))
            why_item.setToolTip(why or str(rec.get("message") or ""))
            self.table.setItem(i, 4, why_item)
            state = "dropped" if dropped else str(
                rec.get("state") or rec.get("status") or "queued")
            self.table.setItem(i, 5, QTableWidgetItem(
                tr(_STATE_LABELS.get(state, state))))
        enabled = self._enabled()
        # G71-11（审查 R5）：禁用态不能给「已请求立即重试」的假象
        self.btn_retry.setEnabled(enabled and not dropped)
        self.btn_retry_all.setEnabled(enabled and not dropped)
        self.btn_requeue.setEnabled(dropped)
        self.btn_remove.setEnabled(not dropped)
        if dropped:
            self.summary_lbl.setText(
                tr("已放弃：{n} 条（可重新入队；attempts 将重置）").format(n=len(rows)))
        elif enabled:
            self.summary_lbl.setText(tr("重试队列：{n} 条").format(n=len(rows)))
        else:
            self.summary_lbl.setText(
                tr("重试队列：{n} 条（自动重试未启用，仍可查看/移除）").format(n=len(rows)))
        return len(rows)

    # ------------------------------------------------------------ 操作
    def retry_selected(self) -> int:
        if not self._enabled():
            self._log(tr("自动重试未启用，无法立即重投；请先在设置页开启"))
            return 0
        keys = self._selected_keys()
        q = self.queue()
        n = 0
        for key in keys:
            try:
                if q is not None and q.retry_now(key):
                    n += 1
            except Exception:
                pass
        if n:
            self._wake_engine()
            self.retried.emit(n)
            self.changed.emit()
            self._log(tr("已请求立即重试 {n} 条").format(n=n))
        self.refresh()
        return n

    def requeue_selected(self) -> int:
        """G73：把已放弃任务重新入队（DLQ redrive）。"""
        if not self._dropped_mode():
            return 0
        keys = self._selected_keys()
        q = self.queue()
        n = 0
        for key in keys:
            try:
                if q is not None and q.requeue_dropped(key):
                    n += 1
            except Exception:
                pass
        if n:
            self._wake_engine()
            self.changed.emit()
            note = "" if self._enabled() else tr("（自动重试未启用，开启后才会重投）")
            self._log(tr("已重新入队 {n} 条{note}").format(n=n, note=note))
        self.refresh()
        return n

    def remove_selected(self) -> int:
        keys = self._selected_keys()
        q = self.queue()
        n = 0
        for key in keys:
            try:
                if q is not None and q.remove(key):
                    n += 1
            except Exception:
                pass
        if n:
            self.changed.emit()
            self._log(tr("已从重试队列移除 {n} 条").format(n=n))
        self.refresh()
        return n

    def retry_all(self) -> int:
        if not self._enabled():
            self._log(tr("自动重试未启用，无法立即重投；请先在设置页开启"))
            return 0
        q = self.queue()
        try:
            n = int(q.retry_all_now()) if q is not None else 0
        except Exception:
            n = 0
        if n:
            self._wake_engine()
            self.retried.emit(n)
            self.changed.emit()
            self._log(tr("已请求立即重试 {n} 条").format(n=n))
        self.refresh()
        return n

    def clear_queue(self) -> int:
        q = self.queue()
        try:
            n = int(q.count()) if q is not None else 0
        except Exception:
            n = 0
        if n:
            try:
                q.clear()
            except Exception:
                n = 0
            if n:
                self.changed.emit()
                self._log(tr("已清空重试队列（{n} 条）").format(n=n))
        self.refresh()
        return n

    # ------------------------------------------------------------ 辅助
    def _wake_engine(self) -> None:
        """让引擎立刻检查到期任务（否则要等 15s 轮询）。"""
        try:
            eng = getattr(self.owner, "engine", None)
            if eng is not None and hasattr(eng, "run_due_retries"):
                eng.run_due_retries()
        except Exception:
            pass

    def _log(self, text: str) -> None:
        try:
            owner = self.owner
            if owner is not None and hasattr(owner, "_emit_log"):
                from . import main_window as _mw
                from .theme import LogLevel
                owner._emit_log(_mw._fmt_dt(), LogLevel.INFO, text)
        except Exception:
            pass

    def closeEvent(self, e):  # noqa: N802 - Qt 命名
        """关闭时停掉自动刷新定时器（避免销毁后仍周期唤醒事件循环）。"""
        try:
            self._timer.stop()
        except Exception:
            pass
        super().closeEvent(e)


class RetryQueueDialog(QDialog):
    """重试队列独立对话框（入口：下载中心「重试队列」按钮）。"""

    retried = pyqtSignal(int)
    changed = pyqtSignal()

    def __init__(self, owner=None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(tr("失败重试队列"))
        from .layout import fit_dialog_to_screen
        fit_dialog_to_screen(self, 760, 420, 520, 320)
        self.panel = RetryQueuePanel(owner, parent=self)
        self.panel.retried.connect(self.retried)
        self.panel.changed.connect(self.changed)
        lay = QVBoxLayout(self)
        lay.addWidget(self.panel, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_close = QPushButton(tr("关闭"))
        self.btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_close)
        lay.addLayout(row)
        self.panel.refresh()
        # 对话框 accept/reject 后停掉面板自动刷新（绑定方法，避免 lambda 引用环）
        self.finished.connect(self._on_finished)

    def _on_finished(self, _result: int = 0) -> None:
        self.panel.close()

    def refresh(self) -> int:
        return self.panel.refresh()
