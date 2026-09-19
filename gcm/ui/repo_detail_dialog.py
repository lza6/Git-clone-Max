"""仓库详情对话框：展示仓库元信息与同步历史。"""
from __future__ import annotations

import os
import subprocess
import threading

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from .theme import QSS

# 同步动作 → 中文标签
_ACTION_LABELS = {
    "cloned": "克隆",
    "updated": "更新",
    "fetched": "检查",
    "empty": "空仓库",
    "skipped": "跳过",
    "conflict": "冲突",
    "cancelled": "取消",
    "failed": "失败",
}

# 同步状态 → 中文标签
_STATUS_LABELS = {
    "pending": "等待中",
    "running": "进行中",
    "success": "成功",
    "failed": "失败",
    "skipped": "跳过",
    "cancelled": "已取消",
    "conflict": "冲突",
}


class RepoDetailDialog(QDialog):
    """仓库详情对话框。

    - 标题：owner/repo
    - 元信息区：仓库路径 / 主机 / 当前 HEAD / 最近同步 / 默认分支
    - 历史表格：只读展示同步历史
    - 底部按钮：打开目录 / 复制路径 / 复制克隆命令 / 检查远端 / 关闭
    """

    # G37-2 远端检查结果回主线程（worker 线程 emit → 主线程 queued 槽，线程安全）
    remote_checked = pyqtSignal(str)

    def __init__(self, repo: dict, history: list, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.history = list(history or [])
        self._remote_thread: threading.Thread | None = None

        owner = str(repo.get("owner", ""))
        name = str(repo.get("repo", ""))
        self.setWindowTitle(f"{owner}/{name} - 仓库详情")
        self.resize(860, 560)
        self.setMinimumSize(680, 420)
        self.setStyleSheet(QSS)

        self.remote_checked.connect(self._finish_remote_check)
        self._build_ui()

    # ------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setSpacing(10)

        self.lbl_title = QLabel(f"{self.repo.get('owner', '')}/{self.repo.get('repo', '')}")
        self.lbl_title.setObjectName("pageTitle")
        v.addWidget(self.lbl_title)

        v.addLayout(self._build_meta())
        v.addWidget(self._build_stats())

        v.addWidget(QLabel("同步历史："))
        v.addWidget(self._build_table(), 1)

        v.addLayout(self._build_buttons())

    def _build_stats(self) -> QLabel:
        """G03-8 统计区：同步次数/成功/失败/冲突/取消/平均耗时（来自 db.stats_for_repo）。"""
        stats = {}
        try:
            rid = int(self.repo.get("id") or 0)
            if rid and isinstance(self.parent(), object) and hasattr(self.parent(), "db"):
                stats = self.parent().db.stats_for_repo(rid) or {}
        except Exception:
            stats = {}
        if not stats:
            # 兜底：从 self.history 现场聚合（不依赖 parent.db）
            total = len(self.history)
            ok = sum(1 for h in self.history if str(h.get("status")) == "success")
            fail = sum(1 for h in self.history if str(h.get("status")) == "failed")
            conf = sum(1 for h in self.history if str(h.get("status")) == "conflict")
            canc = sum(1 for h in self.history if str(h.get("status")) == "cancelled")
            durs = [int(h.get("duration_ms") or 0) for h in self.history]
            avg = sum(durs) // total if total else 0
            stats = {"total": total, "success": ok, "failed": fail,
                     "conflict": conf, "cancelled": canc, "avg_duration_ms": avg}
        text = (f"共 {stats.get('total', 0)} 次同步 · 成功 {stats.get('success', 0)} · "
                f"失败 {stats.get('failed', 0)} · 冲突 {stats.get('conflict', 0)} · "
                f"取消 {stats.get('cancelled', 0)} · 平均耗时 {stats.get('avg_duration_ms', 0) / 1000:.1f}s")
        self.lbl_stats = QLabel(text)
        self.lbl_stats.setObjectName("muted")
        return self.lbl_stats

    def _build_meta(self) -> QGridLayout:
        grid = QGridLayout()
        grid.setSpacing(6)
        rows = [
            ("仓库路径", str(self.repo.get("local_path") or "")),
            ("主机", str(self.repo.get("host") or "")),
            ("当前 HEAD", str(self.repo.get("head_sha") or "")[:12] or "（无）"),
            ("最近同步", str(self.repo.get("last_sync_at") or "") or "（从未）"),
            ("默认分支", str(self.repo.get("default_branch") or "") or "（未知）"),
        ]
        for i, (label, value) in enumerate(rows):
            key = QLabel(label)
            key.setObjectName("muted")
            val = QLabel(value)
            val.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            grid.addWidget(key, i, 0, alignment=Qt.AlignmentFlag.AlignTop)
            grid.addWidget(val, i, 1, alignment=Qt.AlignmentFlag.AlignTop)
        # G37-6 备注：可编辑行 + 保存按钮（持久化到 repos.note）
        note_row = len(rows)
        key = QLabel("备注")
        key.setObjectName("muted")
        self.note_edit = QLineEdit(str(self.repo.get("note") or ""))
        self.note_edit.setPlaceholderText("添加备注…")
        self.btn_save_note = QPushButton("保存")
        self.btn_save_note.setToolTip("保存备注（persist 到 repos.note）")
        self.btn_save_note.clicked.connect(self._save_note)
        grid.addWidget(key, note_row, 0, alignment=Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.note_edit, note_row, 1, alignment=Qt.AlignmentFlag.AlignTop)
        grid.addWidget(self.btn_save_note, note_row, 2, alignment=Qt.AlignmentFlag.AlignTop)
        grid.setColumnStretch(1, 1)
        return grid

    def _save_note(self):
        """G37-6 保存备注到 DB（通过 parent.db.set_note；无 db 则本地更新 dict）。"""
        note = self.note_edit.text().strip()
        try:
            rid = int(self.repo.get("id") or 0)
            db = None
            try:
                if hasattr(self.parent(), "db"):
                    db = self.parent().db
            except Exception:
                db = None
            if db is not None and rid:
                db.set_note(rid, note)
            self.repo["note"] = note
            QMessageBox.information(self, "备注", "备注已保存。")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))

    def _build_table(self) -> QTableWidget:
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["时间", "动作", "状态", "提交数", "说明"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 80)
        self.table.setColumnWidth(2, 80)
        self.table.setColumnWidth(3, 70)
        self.table.setMinimumHeight(200)

        if not self.history:
            # G47-2 空态占位
            self.table.setRowCount(1)
            item = QTableWidgetItem("（暂无同步记录）")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(0, 0, item)
            self.table.setSpan(0, 0, 1, 5)
            return self.table
        self.table.setRowCount(len(self.history))
        for i, h in enumerate(self.history):
            self._set_row(i, h)
        return self.table

    def _set_row(self, row: int, h: dict) -> None:
        cells = [
            str(h.get("started_at") or ""),
            _ACTION_LABELS.get(str(h.get("action")), str(h.get("action") or "")),
            _STATUS_LABELS.get(str(h.get("status")), str(h.get("status") or "")),
            str(h.get("commits") or 0),
            str(h.get("message") or ""),
        ]
        for col, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if col == 3:
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(row, col, item)

    def _build_buttons(self) -> QHBoxLayout:
        btns = QHBoxLayout()
        btns.addStretch(1)

        self.btn_open_dir = QPushButton("打开目录")
        self.btn_open_dir.clicked.connect(self.open_dir)
        btns.addWidget(self.btn_open_dir)

        self.btn_copy_path = QPushButton("复制路径")
        self.btn_copy_path.clicked.connect(self.copy_path)
        btns.addWidget(self.btn_copy_path)

        self.btn_copy_command = QPushButton("复制克隆命令")
        self.btn_copy_command.clicked.connect(self.copy_clone_command)
        btns.addWidget(self.btn_copy_command)

        self.btn_check_remote = QPushButton("检查远端")
        self.btn_check_remote.clicked.connect(self.check_remote)
        btns.addWidget(self.btn_check_remote)

        # G37-2 远端检查结果（只读、muted 样式；后台线程经 singleShot 回主线程更新）
        self.lbl_remote = QLabel("")
        self.lbl_remote.setObjectName("muted")
        btns.addWidget(self.lbl_remote)

        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)
        btns.addWidget(self.btn_close)
        return btns

    # ------------------------------------------------------------ 槽
    def open_dir(self) -> None:
        """打开仓库本地目录；目录不存在时弹警告（POSIX 用系统打开器）。"""
        path = str(self.repo.get("local_path") or "")
        if path and os.path.isdir(path):
            if os.name == "nt":
                os.startfile(path)  # noqa
            else:
                import shutil
                opener = shutil.which("open") or shutil.which("xdg-open")
                if opener:
                    subprocess.Popen([opener, path])
        else:
            QMessageBox.warning(self, "目录不存在", f"仓库目录不存在：\n{path or '（路径为空）'}")

    def copy_path(self) -> None:
        """复制本地路径到剪贴板。"""
        QApplication.clipboard().setText(str(self.repo.get("local_path") or ""))

    def copy_clone_command(self) -> None:
        """G35-5 复制克隆命令到剪贴板。

        - 无 ref：``git clone <url>``
        - 有 ref：``git clone -b <ref> <url>``（对应 @tag 语法）
        url 为空时回退为 https 形式；仍为空则复制空字符串，不抛异常。
        """
        url = str(self.repo.get("url") or "").strip()
        if not url:
            # 兜底：用 owner/repo 拼 https 地址；仍缺字段时留空
            owner = str(self.repo.get("owner") or "").strip()
            name = str(self.repo.get("repo") or "").strip()
            if owner and name:
                url = f"https://github.com/{owner}/{name}"
        ref = str(self.repo.get("ref") or "").strip()
        if ref:
            cmd = f"git clone -b {ref} {url}"
        else:
            cmd = f"git clone {url}"
        QApplication.clipboard().setText(cmd)

    # ------------------------------------------------------------ G37-2 检查远端
    def check_remote(self) -> None:
        """G37-2 检查远端：后台 ls-remote 并比较本地 HEAD，结果写入 lbl_remote。

        - url 为空 → 同步提示无法检查（不起线程）。
        - 否则起 daemon 线程跑 ``git ls-remote <url> HEAD``，完成后 emit
          ``remote_checked`` 信号经 queued 连接回主线程更新 UI（跨线程改 widget 不安全）。
        - 检查期间禁用按钮，避免重复触发；完成恢复。
        """
        url = str(self.repo.get("url") or "").strip()
        if not url:
            self.lbl_remote.setText("无法检查远端（无 URL 或命令失败）")
            return

        self.btn_check_remote.setEnabled(False)
        self.lbl_remote.setText("检查中…")
        local_sha = str(self.repo.get("head_sha") or "").strip() or None

        def _worker() -> None:
            text = self._compute_remote_text(url, local_sha)
            # emit 信号 → 主线程 queued 槽（Qt 自动投递到主线程事件循环）
            self.remote_checked.emit(text)

        t = threading.Thread(target=_worker, daemon=True)
        t.start()
        self._remote_thread = t  # 持引用防 GC

    def _finish_remote_check(self, text: str) -> None:
        """主线程收尾：写入结果并恢复按钮（remote_checked 的槽，主线程执行）。"""
        self.lbl_remote.setText(text)
        self.btn_check_remote.setEnabled(True)

    def _compute_remote_text(self, url: str, local_sha: str | None) -> str:
        """同步执行 ``git ls-remote <url> HEAD`` 并比较本地 HEAD（工作线程内调用）。

        - 命令失败 / 无输出 → 「无法检查远端（无 URL 或命令失败）」
        - 本地无 HEAD → 「无法比较（本地无 HEAD）」（缺基准无法判断领先/落后）
        - 相同 → 「已最新」；不同 → 「远端有新提交（本地 head 落后）」
        """
        try:
            r = subprocess.run(
                ["git", "ls-remote", url, "HEAD"], capture_output=True, text=True, timeout=30,
            )
            lines = (r.stdout or "").strip().splitlines()
            remote_sha = lines[0].split("\t")[0].strip() if lines else ""
        except Exception:  # noqa: BLE001 - 命令失败统一按无法检查处理
            return "无法检查远端（无 URL 或命令失败）"
        if not remote_sha:
            return "无法检查远端（无 URL 或命令失败）"
        if not local_sha:
            return "无法比较（本地无 HEAD）"
        if local_sha == remote_sha:
            return "已最新"
        return "远端有新提交（本地 head 落后）"
