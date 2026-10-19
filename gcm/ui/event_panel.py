"""G71-10 事件流查看器：把 EventBus 的「执行轨迹」在 GUI 里可视化。

参考项目 hermes-hudui / tracecrate 的做法：黑匣子不只是日志文本，而是
**按类型/时间/任务筛选的结构化事件**。本对话框直接读 `EventBus.snapshot()`，
不持久化、不额外写文件；与 SSE/诊断包消费同一份事件。

默认**隐藏** `task_progress`（32 并发时它最吵），可在下拉框切换查看。
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
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

from ..app.event_bus import EventType, get_event_bus
from ..i18n import tr

_COLS = ("#", "时间", "类型", "仓库", "状态", "摘要")


def _fmt_time(ts: float) -> str:
    try:
        return time.strftime("%H:%M:%S", time.localtime(float(ts)))
    except Exception:
        return "—"


def _summary(ev: Any) -> str:
    """按事件类型压成一行摘要（不展开整个 JSON）。"""
    d = getattr(ev, "data", {}) or {}
    t = getattr(ev, "type", "")
    if t == EventType.TASK_RESULT:
        return f"{d.get('status', '')} · {d.get('action', '')} · {d.get('message', '')}"
    if t == EventType.TASK_ERROR:
        return f"[{d.get('category', '')}] {d.get('title', '')} · {d.get('action', '')}"
    if t in (EventType.RETRY_QUEUED, EventType.RETRY_DROPPED):
        return f"{d.get('title', '')} · attempts={d.get('attempts', '')}"
    if t == EventType.TASK_PROGRESS:
        return f"{d.get('percent', '')}% {d.get('detail', '')}".strip()
    if t == EventType.BATCH_FINISHED:
        return f"cancelled={d.get('cancelled', '')} counts={d.get('counts', '')}"
    if t == EventType.BATCH_STARTED:
        return f"total={d.get('total', '')} depth={d.get('fetch_depth', '')}"
    return str(d.get('message', ''))[:160]


def filter_events(events: list[Any], mode: str) -> list[Any]:
    """按下拉框模式过滤事件（纯函数，便于测试）。"""
    if mode == "progress":
        return [e for e in events if e.type == EventType.TASK_PROGRESS]
    if mode == "errors":
        return [e for e in events if e.type in (EventType.TASK_ERROR,
                                                EventType.RETRY_DROPPED)]
    if mode == "retry":
        return [e for e in events if e.type in (
            EventType.RETRY_QUEUED, EventType.RETRY_STARTED,
            EventType.RETRY_DROPPED)]
    if mode == "batch":
        return [e for e in events if e.type in (EventType.BATCH_STARTED,
                                                EventType.BATCH_FINISHED)]
    # 默认：除进度外全部
    return [e for e in events if e.type != EventType.TASK_PROGRESS]


class EventStreamPanel(QWidget):
    """事件表格 + 类型过滤 + 自动刷新。"""

    def __init__(self, bus: Any = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._bus = bus or get_event_bus()
        self._paused = False
        self._build_ui()
        self._timer = QTimer(self)
        self._timer.setInterval(1500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setSpacing(8)
        row = QHBoxLayout()
        self.mode_combo = QComboBox()
        self.mode_combo.addItem(tr("全部（不含进度）"), "default")
        self.mode_combo.addItem(tr("任务进度"), "progress")
        self.mode_combo.addItem(tr("错误 / 放弃"), "errors")
        self.mode_combo.addItem(tr("重试"), "retry")
        self.mode_combo.addItem(tr("批次"), "batch")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        row.addWidget(QLabel(tr("类型：")))
        row.addWidget(self.mode_combo)
        self.ck_pause = QCheckBox(tr("暂停自动刷新"))
        self.ck_pause.toggled.connect(self._on_pause)
        row.addWidget(self.ck_pause)
        self.btn_refresh = QPushButton(tr("刷新"))
        self.btn_refresh.clicked.connect(self.refresh)
        row.addWidget(self.btn_refresh)
        self.btn_copy = QPushButton(tr("复制 JSON"))
        self.btn_copy.clicked.connect(self.copy_json)
        row.addWidget(self.btn_copy)
        row.addStretch(1)
        self.count_lbl = QLabel("")
        self.count_lbl.setObjectName("muted")
        row.addWidget(self.count_lbl)
        v.addLayout(row)

        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in _COLS])
        self.table.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAccessibleName(tr("事件流表格"))
        v.addWidget(self.table, 1)

    def _on_pause(self, checked: bool) -> None:
        self._paused = bool(checked)

    def _on_mode_changed(self, _index: int = 0) -> None:
        """类型下拉变化 → 立即刷新（绑定方法，避免 lambda 引用环）。"""
        self.refresh()

    def _tick(self) -> None:
        if not self._paused:
            self.refresh()

    def closeEvent(self, e):  # noqa: N802 - Qt 命名
        """关闭时停掉自动刷新定时器（避免对话框销毁后仍周期唤醒事件循环）。"""
        try:
            self._timer.stop()
        except Exception:
            pass
        super().closeEvent(e)

    def current_events(self, limit: int = 500) -> list[Any]:
        mode = str(self.mode_combo.currentData() or "default")
        # G71-11（审查 S1）：进度事件单独缓冲；只有进度模式才显式取它。
        types = {
            "progress": [EventType.TASK_PROGRESS],
            "errors": [EventType.TASK_ERROR, EventType.RETRY_DROPPED],
            "retry": [EventType.RETRY_QUEUED, EventType.RETRY_STARTED,
                      EventType.RETRY_DROPPED],
            "batch": [EventType.BATCH_STARTED, EventType.BATCH_FINISHED],
        }.get(mode)
        try:
            events = self._bus.snapshot(limit=limit, types=types)
        except Exception:
            return []
        return filter_events(events, mode)

    def refresh(self) -> int:
        """重绘事件表；返回显示行数。"""
        events = self.current_events()
        self.table.setRowCount(0)
        self.table.setRowCount(len(events))
        for i, ev in enumerate(events):
            self.table.setItem(i, 0, QTableWidgetItem(str(getattr(ev, "seq", ""))))
            self.table.setItem(i, 1, QTableWidgetItem(_fmt_time(getattr(ev, "ts", 0))))
            self.table.setItem(i, 2, QTableWidgetItem(str(getattr(ev, "type", ""))))
            d = getattr(ev, "data", {}) or {}
            self.table.setItem(i, 3, QTableWidgetItem(str(d.get("key") or d.get("folder_name") or "")))
            self.table.setItem(i, 4, QTableWidgetItem(str(d.get("status") or "")))
            item = QTableWidgetItem(_summary(ev))
            # G71-11（审查 S7）：tooltip 不再为每行做 indent JSON（500 行 × 每秒
            # 一次会有明显主线程开销）；只在行数较少时给完整载荷。
            if len(events) <= 120:
                try:
                    item.setToolTip(json.dumps(
                        getattr(ev, "to_dict", lambda: {})(),
                        ensure_ascii=False, separators=(",", ":"))[:3000])
                except Exception:
                    pass
            self.table.setItem(i, 5, item)
        self.count_lbl.setText(tr("显示 {n} 条").format(n=len(events)))
        return len(events)

    def copy_json(self) -> int:
        """把当前过滤后的完整事件复制到剪贴板（已打码）。"""
        events = self.current_events()
        try:
            text = json.dumps([e.to_dict() for e in events], ensure_ascii=False, indent=2)
            from PyQt6.QtWidgets import QApplication
            QApplication.clipboard().setText(text)
        except Exception:
            return 0
        return len(events)


class EventStreamDialog(QDialog):
    """事件流独立对话框（入口：设置页「关于与更新」）。"""

    def __init__(self, bus: Any = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(tr("事件流（黑匣子执行轨迹）"))
        from .layout import fit_dialog_to_screen
        fit_dialog_to_screen(self, 860, 520, 560, 360)
        self.panel = EventStreamPanel(bus=bus, parent=self)
        lay = QVBoxLayout(self)
        lay.addWidget(self.panel, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        btn_close = QPushButton(tr("关闭"))
        btn_close.clicked.connect(self.accept)
        row.addWidget(btn_close)
        lay.addLayout(row)
        self.panel.refresh()
        # 对话框 accept/reject 后停掉面板自动刷新（绑定方法，避免 lambda 引用环）
        self.finished.connect(self._on_finished)

    def _on_finished(self, _result: int = 0) -> None:
        self.panel.close()
