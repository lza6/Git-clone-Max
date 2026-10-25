"""「后台任务」面板（G80-B7-1）：把 4 个隐形定时器变成可见状态。

为什么需要：自动更新检查 / 看门狗续跑 / 阈值告警扫描 / 定时报表都是"设了间隔就等着"，
用户遇到"报表没生成""告警没响"时，没有任何地方能看出**它有没有在跑、上次跑成没成、
下次什么时候跑**。这个对话框把 `app/background_jobs` 的登记表渲染成一张表。

设计取舍：
- **不加后台定时器**：每次打开/点刷新时读一次快照（避免又多一个定时器）；
- **不编造时间**：本次进程还没跑过的任务显示「启动后首轮」，而不是猜一个时间；
- 关掉的任务显示「已关闭」（而不是留空让人以为坏了）。
"""
from __future__ import annotations

import datetime

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..app.background_jobs import registry
from ..i18n import tr
from .layout import fit_dialog_to_screen

_COLS = ("任务", "状态", "间隔", "上次运行", "下次预计", "上次结果")


def _fmt_ts(ts: float) -> str:
    if not ts:
        return "—"
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _fmt_interval(sec: float) -> str:
    if sec <= 0:
        return "—"
    if sec >= 3600:
        hours = sec / 3600.0
        return f"{hours:g} 小时"
    return f"{sec / 60.0:g} 分钟"


class BackgroundJobsDialog(QDialog):
    """后台任务状态表（只读 + 手动刷新）。"""

    def __init__(self, owner=None, parent=None):
        super().__init__(parent)
        self._owner_ref = owner
        self.setWindowTitle(tr("后台任务"))
        fit_dialog_to_screen(self, 820, 420, 640, 320)

        v = QVBoxLayout(self)
        v.setSpacing(8)
        hint = QLabel(tr("这些任务只在你打开应用时运行；「上次运行」是本次进程内的记录。"))
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        v.addWidget(hint)

        self.table = QTableWidget(0, len(_COLS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in _COLS])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        v.addWidget(self.table, 1)

        row = QHBoxLayout()
        self.btn_refresh = QPushButton(tr("刷新"))
        self.btn_refresh.clicked.connect(self.refresh)
        row.addWidget(self.btn_refresh)
        row.addStretch(1)
        self.btn_close = QPushButton(tr("关闭"))
        self.btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_close)
        v.addLayout(row)

        self.refresh()

    def refresh(self) -> None:
        """读一次登记表快照并重绘（不持锁、不触发任何调度）。"""
        jobs = registry().snapshot()
        self.table.setRowCount(len(jobs))
        for i, job in enumerate(jobs):
            if not job.enabled:
                status = tr("已关闭")
            elif job.last_run_at <= 0:
                status = tr("已启用（启动后首轮）")
            else:
                status = tr("运行中")
            next_text = _fmt_ts(job.next_run_at) if job.enabled else "—"
            result = job.last_error or job.last_result or "—"
            cells = (job.title, status, _fmt_interval(job.interval_sec),
                     _fmt_ts(job.last_run_at), next_text, result)
            for col, text in enumerate(cells):
                item = QTableWidgetItem(str(text))
                if col == 5 and job.last_error:
                    item.setToolTip(str(job.last_error))
                self.table.setItem(i, col, item)
        if not jobs:
            self.table.setRowCount(1)
            item = QTableWidgetItem(tr("（还没有登记任何后台任务）"))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, len(_COLS))
