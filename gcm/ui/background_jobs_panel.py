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

from PyQt6.QtCore import Qt, QTime
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from ..app.background_jobs import registry
from ..i18n import tr
from .layout import fit_dialog_to_screen

_COLS = tuple(tr(c) for c in ("任务", "状态", "间隔", "上次运行", "下次预计", "上次结果"))


def _fmt_ts(ts: float) -> str:
    if not ts:
        return "—"
    return datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")


def _fmt_interval(sec: float) -> str:
    if sec <= 0:
        return "—"
    if sec >= 3600:
        hours = sec / 3600.0
        return tr("{hours:g} 小时").format(hours=hours)
    return tr("{min:g} 分钟").format(min=sec / 60.0)


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
        # 立即生效——queue_runner 每 tick 都重读 store）。创建队列 UI 已随 G80-B7-3-S4b 落地（A-2-3 修正原过时注释）。
        self.btn_new_queue = QPushButton(tr("新建队列…"))
        self.btn_new_queue.setToolTip(tr("创建定时同步队列：名称 + 每行一个仓库地址"))
        self.btn_new_queue.clicked.connect(self._create_queue)
        row.addWidget(self.btn_new_queue)
        self.btn_toggle_queue = QPushButton(tr("启用/停用所选队列"))
        self.btn_toggle_queue.setToolTip(tr("仅对「定时同步队列」行生效；立即写入数据库"))
        self.btn_toggle_queue.clicked.connect(self._toggle_selected_queue)
        row.addWidget(self.btn_toggle_queue)
        self.btn_edit_queue = QPushButton(tr("编辑时间窗…"))
        self.btn_edit_queue.setToolTip(tr("修改所选定时队列的周几、起止时刻与补跑策略"))
        self.btn_edit_queue.clicked.connect(self._edit_selected_queue)
        row.addWidget(self.btn_edit_queue)
        self.btn_delete_queue = QPushButton(tr("删除所选队列"))
        self.btn_delete_queue.setToolTip(tr("删除后不可撤销；会二次确认"))
        self.btn_delete_queue.clicked.connect(self._delete_selected_queue)
        row.addWidget(self.btn_delete_queue)
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
        if not self._queue_write(
                lambda: db.queue.add_queue(
                    str(name).strip(),
                    [{"owner": sp.owner, "repo": sp.repo,
                      "url_https": sp.url_https,
                      "folder_name": sp.folder_name,
                      "ref": getattr(sp, "ref", "") or ""} for sp in specs]),
                tr("创建同步队列"), tr("创建失败")):
            return
        self.refresh()
        QMessageBox.information(self, tr("已创建"),
                                tr("队列「{}」已创建（{} 个仓库，默认每天随时可跑；"
                                   "可点「编辑时间窗…」设置周几与起止时刻）").format(name, len(specs)))

    @staticmethod
    def _queue_hhmm(edit: QTimeEdit) -> str:
        t = edit.time()
        return f"{t.hour():02d}:{t.minute():02d}" if t.isValid() else ""

    def _show_write_error(self, e: ValueError, context: str, title: str) -> None:
        from .error_dialog import show_error
        show_error(self, e, context=context, title=title)

    def _queue_write(self, action, context: str, title: str) -> bool:
        """队列写操作的统一错误处理（复用同一个 except，不扩异常预算）。"""
        try:
            action()
            return True
        except ValueError as e:
            self._show_write_error(e, context, title)
            return False

    def _selected_queue(self):
        """当前行若是队列，返回 (db, queue row)；否则给提示并返回 None。"""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, tr("提示"), tr("请先在表中选中一行"))
            return None
        jobs = registry().snapshot()
        if row >= len(jobs):
            return self._queue_from_row(row - len(jobs))
        QMessageBox.information(self, tr("提示"), tr("请选中「定时同步队列」这一行"))
        return None

    def _queue_from_row(self, idx: int):
        owner = self._owner_ref() if callable(self._owner_ref) else self._owner_ref
        db = getattr(owner, "db", None) if owner is not None else None
        if db is None or not hasattr(db, "queue"):
            QMessageBox.warning(self, tr("无法操作"), tr("当前没有数据库连接"))
            return None
        queues = db.queue.list_queues()
        if not (0 <= idx < len(queues)):
            QMessageBox.information(self, tr("提示"), tr("该队列已不存在，请刷新"))
            return None
        return db, queues[idx]

    def _edit_selected_queue(self) -> None:
        picked = self._selected_queue()
        if picked is None:
            return
        db, q = picked
        dlg = QDialog(self)
        dlg.setWindowTitle(tr("编辑队列时间窗"))
        form = QFormLayout(dlg)
        days_row = QWidget(dlg)
        days_lay = QHBoxLayout(days_row)
        days_lay.setContentsMargins(0, 0, 0, 0)
        labels = (tr("一"), tr("二"), tr("三"), tr("四"), tr("五"), tr("六"), tr("日"))
        boxes = []
        current = set(q.get("days_set") or [])
        for i, label in enumerate(labels, start=1):
            box = QCheckBox(label, days_row)
            box.setChecked(i in current)
            boxes.append(box)
            days_lay.addWidget(box)
        form.addRow(tr("运行日："), days_row)
        start_edit = QTimeEdit(dlg)
        start_edit.setDisplayFormat("HH:mm")
        end_edit = QTimeEdit(dlg)
        end_edit.setDisplayFormat("HH:mm")
        for edit, raw in ((start_edit, q.get("start_time") or ""),
                          (end_edit, q.get("end_time") or "")):
            parsed = raw.split(":") if raw else None
            if parsed and len(parsed) == 2:
                edit.setTime(QTime(int(parsed[0]), int(parsed[1])))
        unlimited = QCheckBox(tr("不限起止时刻（清空时间窗）"), dlg)
        # 当前队列无时间窗（每天随时）时默认勾选：直接保存不得把空窗写成 00:00
        # （00:00–00:00 是空窗口，会让队列从此永不运行）。
        unlimited.setChecked(not (q.get("start_time") or q.get("end_time")))
        form.addRow(unlimited)
        form.addRow(tr("开始："), start_edit)
        form.addRow(tr("结束："), end_edit)
        missed = QComboBox(dlg)
        missed.addItem(tr("跳过"), "skip")
        missed.addItem(tr("补跑一次"), "run_once")
        idx = missed.findData(q.get("on_missed") or "skip")
        missed.setCurrentIndex(max(0, idx))
        form.addRow(tr("错过窗口："), missed)
        btns = QHBoxLayout()
        ok_btn = QPushButton(tr("保存"))
        cancel_btn = QPushButton(tr("取消"))
        cancel_btn.clicked.connect(dlg.reject)
        ok_btn.clicked.connect(dlg.accept)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        btns.addWidget(ok_btn)
        form.addRow(btns)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        days = ",".join(str(i) for i, b in enumerate(boxes, start=1)
                        if b.isChecked())
        start, end = ("", "") if unlimited.isChecked() else (
            self._queue_hhmm(start_edit), self._queue_hhmm(end_edit))
        if not self._queue_write(
                lambda: db.queue.update_queue(
                    int(q["id"]), days=days, start=start,
                    end=end, on_missed=missed.currentData()),
                tr("编辑同步队列时间窗"), tr("保存失败")):
            return
        self.refresh()

    def _delete_selected_queue(self) -> None:
        picked = self._selected_queue()
        if picked is None:
            return
        db, q = picked
        answer = QMessageBox.question(
            self, tr("删除定时队列"),
            tr("确定删除队列「{}」？该操作不可撤销。").format(q["name"]),
            QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        db.queue.delete_queue(int(q["id"]))
        owner = self._owner_ref() if callable(self._owner_ref) else self._owner_ref
        oplog = getattr(owner, "oplog", None)
        if oplog is not None and hasattr(oplog, "record"):
            oplog.record("queue_delete", str(q["name"]),
                         summary=tr("删除定时同步队列 {}").format(q["name"]),
                         before={"id": q["id"], "days": q.get("days_of_week", ""),
                                 "start": q.get("start_time", ""),
                                 "end": q.get("end_time", "")},
                         undoable=False)
        self.refresh()

    def _toggle_selected_queue(self) -> None:
        """启停选中的定时同步队列（写库立即生效；非队列行给出提示）。"""
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, tr("提示"), tr("请先在表中选中一行"))
            return
        jobs = registry().snapshot()
        if row >= len(jobs):
            picked = self._queue_from_row(row - len(jobs))
            if picked is None:
                return
            db, q = picked
            db.queue.set_enabled(int(q["id"]), not bool(q.get("enabled")))
            self.refresh()
            return
        QMessageBox.information(self, tr("提示"), tr("请选中「定时同步队列」这一行"))

    def refresh(self) -> None:
        """读一次登记表快照并重绘（不持锁、不触发任何调度）。"""
        jobs = registry().snapshot()
        owner = self._owner_ref() if callable(self._owner_ref) else self._owner_ref
        db = getattr(owner, "db", None) if owner is not None else None
        queues = db.queue.list_queues() if db is not None and hasattr(db, "queue") else []
        total = len(jobs) + len(queues)
        self.table.setRowCount(max(1, total))
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
        for offset, q in enumerate(queues):
            days = q.get("days_set") or []
            window = (f"{q.get('start_time') or '00:00'}–{q.get('end_time') or '24:00'}"
                      if q.get("start_time") or q.get("end_time") else tr("每天随时"))
            status = tr("已启用") if q.get("enabled") else tr("已关闭")
            cells = (tr("定时同步队列：{}").format(q["name"]), status,
                     tr("周 {} · {}").format(",".join(map(str, days)), window),
                     q.get("last_run_at") or "—", "—", q.get("last_status") or "—")
            for col, text in enumerate(cells):
                self.table.setItem(len(jobs) + offset, col, QTableWidgetItem(str(text)))
        if not total:
            self.table.setRowCount(1)
            item = QTableWidgetItem(tr("（还没有登记任何后台任务）"))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, len(_COLS))
