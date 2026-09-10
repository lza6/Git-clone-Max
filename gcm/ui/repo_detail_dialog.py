# -*- coding: utf-8 -*-
"""仓库详情对话框：展示仓库元信息与同步历史。"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
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
    - 底部按钮：打开目录 / 复制路径 / 关闭
    """

    def __init__(self, repo: dict, history: list, parent=None):
        super().__init__(parent)
        self.repo = repo
        self.history = list(history or [])

        owner = str(repo.get("owner", ""))
        name = str(repo.get("repo", ""))
        self.setWindowTitle(f"{owner}/{name} - 仓库详情")
        self.resize(860, 560)
        self.setMinimumSize(680, 420)
        self.setStyleSheet(QSS)

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
            from ..db.repo_db import Database
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
        grid.setColumnStretch(1, 1)
        return grid

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

        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)
        btns.addWidget(self.btn_close)
        return btns

    # ------------------------------------------------------------ 槽
    def open_dir(self) -> None:
        """打开仓库本地目录；目录不存在时弹警告。"""
        path = str(self.repo.get("local_path") or "")
        if path and os.path.isdir(path):
            os.startfile(path)
        else:
            QMessageBox.warning(self, "目录不存在", f"仓库目录不存在：\n{path or '（路径为空）'}")

    def copy_path(self) -> None:
        """复制本地路径到剪贴板。"""
        QApplication.clipboard().setText(str(self.repo.get("local_path") or ""))
