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
    QMessageBox,
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
        # G80-B7-3 / S4：选中「定时同步队列」行时可启停（写 sync_queue.enabled，
        # 立即生效——queue_runner 每 tick 都重读 store）。创建队列仍需后续编辑 UI。
        self.btn_new_queue = QPushButton(tr("新建队列…"))
        self.btn_new_queue.setToolTip(tr("创建定时同步队列：名称 + 每行一个仓库地址"))
        self.btn_new_queue.clicked.connect(self._create_queue)
        row.addWidget(self.btn_new_queue)
        self.btn_toggle_queue = QPushButton(tr("启用/停用所选队列"))
        self.btn_toggle_queue.setToolTip(tr("仅对「定时同步队列」行生效；立即写入数据库"))
        self.btn_toggle_queue.clicked.connect(self._toggle_selected_queue)
        row.addWidget(self.btn_toggle_queue)
        row.addStretch(1)
        self.btn_close = QPushButton(tr("关闭"))
        self.btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_close)
        v.addLayout(row)

        self.refresh()

    def _create_queue(self) -> None:
        """创建定时同步队列（S4 收尾：最小可用编辑——名称 + 仓库地址每行一个）。

        刻意保持简单：不做复杂的时间窗编辑器（周几/起止时刻可后续在 DB 里调，
        或加设置页分组）；本对话框解决「不用手改数据库就能建队列」。
        """
        from PyQt6.QtWidgets import QInputDialog, QPlainTextEdit

        owner = self._owner_ref() if callable(self._owner_ref) else self._owner_ref
        db = getattr(owner, "db", None) if owner is not None else None
        if db is None or not hasattr(db, "queue"):
            QMessageBox.warning(self, tr("无法操作"), tr("当前没有数据库连接"))
            return
        name, ok = QInputDialog.getText(self, tr("新建定时队列"), tr("队列名称："))
        if not ok or not str(name).strip():
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("输入仓库地址"))
        v = QVBoxLayout(dlg)
        tip = QLabel(tr("每行一个仓库地址（同下载页格式）："))
        tip.setObjectName("muted")
        v.addWidget(tip)
        edit = QPlainTextEdit()
        edit.setPlaceholderText("https://github.com/owner/repo")
        edit.setMinimumSize(480, 200)
        v.addWidget(edit)
        btns = QHBoxLayout()
        ok_btn = QPushButton(tr("确定"))
        cancel_btn = QPushButton(tr("取消"))
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn.clicked.connect(dlg.accept)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)
        v.addLayout(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        text = edit.toPlainText()
        if not text.strip():
            QMessageBox.warning(self, tr("创建失败"), tr("没有输入任何仓库地址"))
            return
        from ..app.url_lib import parse_urls
        specs, invalid = parse_urls(text)
        if not specs:
            QMessageBox.warning(self, tr("创建失败"),
                                tr("没有可识别的仓库地址（{} 行无效）").format(len(invalid)))
            return
        try:
            db.queue.add_queue(str(name).strip(),
                               [{"owner": sp.owner, "repo": sp.repo,
                                 "url_https": sp.url_https,
                                 "folder_name": sp.folder_name} for sp in specs])
        except ValueError as e:
            QMessageBox.warning(self, tr("创建失败"), str(e))
            return
        self.refresh()
        QMessageBox.information(self, tr("已创建"),
                                tr("队列「{}」已创建（{} 个仓库，默认每天随时可跑；"
                                   "时间窗可在数据库调整）").format(name, len(specs)))

    def _toggle_selected_queue(self) -> None:
        """启停选中的定时同步队列（写库立即生效；非队列行给出提示）。"""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, tr("提示"), tr("请先在表中选中一行"))
            return
        jobs = registry().snapshot()
        if row >= len(jobs) or jobs[row].key != "sync_queue":
            QMessageBox.information(self, tr("提示"), tr("请选中「定时同步队列」这一行"))
            return
        owner = self._owner_ref() if callable(self._owner_ref) else self._owner_ref
        db = getattr(owner, "db", None) if owner is not None else None
        if db is None or not hasattr(db, "queue"):
            QMessageBox.warning(self, tr("无法操作"), tr("当前没有数据库连接"))
            return
        queues = db.queue.list_queues()
        if not queues:
            QMessageBox.information(self, tr("提示"),
                                   tr("还没有创建任何同步队列（可通过数据库 sync_queue 表添加）"))
            return
        # 多个队列时操作全部取反过于激进：只切换第一个未处理的（与面板单行选中一致，
        # 这里选择「把所有队列统一翻转到与当前状态相反」——对单队列用户即直觉行为）。
        any_enabled = any(q.get("enabled") for q in queues)
        for q in queues:
            db.queue.set_enabled(int(q["id"]), not any_enabled)
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
