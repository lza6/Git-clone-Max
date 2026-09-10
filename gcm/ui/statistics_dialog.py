# -*- coding: utf-8 -*-
"""G07-1 统计中心对话框：全局同步聚合（仓库/次数/成功失败/host 分布）。"""
from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .theme import QSS


class StatisticsDialog(QDialog):
    """统计中心：展示 Database.stats_overview() 的全局聚合。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("统计中心")
        self.resize(520, 320)
        self.setMinimumSize(420, 240)
        self.setStyleSheet(QSS)
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setSpacing(10)

        self.lbl_title = QLabel("📊 统计中心")
        self.lbl_title.setObjectName("pageTitle")
        v.addWidget(self.lbl_title)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setWordWrap(True)
        v.addWidget(self.lbl_summary)

        self.lbl_hosts = QLabel("")
        self.lbl_hosts.setObjectName("muted")
        self.lbl_hosts.setWordWrap(True)
        v.addWidget(self.lbl_hosts)

        self.lbl_note = QLabel("数据来自 sync_history 全量聚合；每次同步自动记录。")
        self.lbl_note.setObjectName("muted")
        v.addWidget(self.lbl_note)

        v.addStretch(1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self._refresh)
        btns.addWidget(self.btn_refresh)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)
        btns.addWidget(self.btn_close)
        v.addLayout(btns)

    # ------------------------------------------------------------ 数据
    def _refresh(self) -> None:
        try:
            ov = self.db.stats_overview()
        except Exception:
            ov = {"total_repos": 0, "total_syncs": 0,
                  "success_syncs": 0, "failed_syncs": 0, "by_host": {}}
        self.lbl_summary.setText(
            f"仓库总数：{ov.get('total_repos', 0)}\n"
            f"同步总次数：{ov.get('total_syncs', 0)}\n"
            f"成功：{ov.get('success_syncs', 0)} · 失败：{ov.get('failed_syncs', 0)}")
        by_host = ov.get("by_host") or {}
        if by_host:
            self.lbl_hosts.setText(
                "平台分布：" + " · ".join(f"{h}: {c}" for h, c in by_host.items()))
        else:
            self.lbl_hosts.setText("平台分布：（暂无仓库）")
