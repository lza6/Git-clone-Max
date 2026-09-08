# -*- coding: utf-8 -*-
"""本地仓库导入对话框：扫描目标目录，勾选要纳入管理的已有 git 仓库。"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..app.scanner import RepoInfo, to_spec
from ..db.repo_db import Database
from .scan_worker import ScanWorker


class LocalReposDialog(QDialog):
    """选择要导入的本地已有 git 仓库。

    - 后台线程扫描（git 查询有 IO），完成信号回主线程渲染
    - 过滤 + 勾选 + 批量导入
    """

    def __init__(self, parent=None, root_paths=None, db: Optional[Database] = None,
                 max_depth: int = 2):
        super().__init__(parent)
        self.setWindowTitle("导入本地已有仓库")
        self.resize(860, 560)
        self.setMinimumSize(720, 420)
        self._db = db
        self._max_depth = max_depth
        self._rows: List[RepoInfo] = []
        self._filtered: List[RepoInfo] = []
        self._selected: List[RepoInfo] = []
        self._worker: Optional[ScanWorker] = None

        self._roots: List[Path] = []
        if root_paths:
            self._roots = [Path(p) for p in root_paths if Path(p).is_dir()]

        self._build_ui()
        self._load_roots()
        QTimer.singleShot(0, self._start_scan)

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setSpacing(8)

        top = QHBoxLayout()
        top.addWidget(QLabel("扫描目录："))
        self.root_combo = QLineEdit()
        self.root_combo.setPlaceholderText("目录间用 ; 分隔（默认：克隆根目录）")
        top.addWidget(self.root_combo, 1)
        btn_scan = QPushButton("重新扫描")
        btn_scan.clicked.connect(self._start_scan)
        top.addWidget(btn_scan)
        v.addLayout(top)

        v.addWidget(QLabel("发现以下已有 git 仓库（勾选后点击「导入」纳入管理）："))

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["", "仓库", "远端", "本地路径"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setMinimumHeight(260)
        v.addWidget(self.table, 1)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("筛选仓库名…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        v.addWidget(self.filter_edit)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        v.addWidget(self.progress)

        self.status = QLabel("")
        self.status.setObjectName("muted")
        v.addWidget(self.status)

        btns = QDialogButtonBox()
        self.btn_select_all = QPushButton("全选")
        self.btn_select_all.clicked.connect(self._select_all)
        self.btn_select_none = QPushButton("全不选")
        self.btn_select_none.clicked.connect(self._select_none)
        btns.addButton(self.btn_select_all, QDialogButtonBox.ButtonRole.ActionRole)
        btns.addButton(self.btn_select_none, QDialogButtonBox.ButtonRole.ActionRole)
        self._import_btn = btns.addButton("导入", QDialogButtonBox.ButtonRole.AcceptRole)
        self._import_btn.clicked.connect(self._on_import)
        btn_cancel = btns.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        btn_cancel.clicked.connect(self.reject)
        v.addWidget(btns)

    def _load_roots(self):
        if self._roots:
            self.root_combo.setText(";".join(str(p) for p in self._roots))

    # ------------------------------------------------------------ 扫描
    def _start_scan(self):
        if self._worker is not None:
            return
        roots = [Path(p.strip()) for p in self.root_combo.text().split(";") if p.strip()]
        if not roots:
            QMessageBox.information(self, "提示", "请先填写要扫描的目录。")
            return
        self._roots = roots
        self._set_scanning(True)
        self._worker = ScanWorker(self._roots, self._max_depth, parent=self)
        self._worker.finished.connect(self._on_scan_done)
        self._worker.failed.connect(self._on_scan_failed)
        self._worker.start()

    def _on_scan_done(self, result: List[RepoInfo]):
        self._rows = result
        self._set_scanning(False)
        self._render()
        self.status.setText(f"共发现 {len(result)} 个本地仓库")

    def _on_scan_failed(self, err: str):
        self._set_scanning(False)
        self.status.setText(f"扫描失败：{err}")
        QMessageBox.warning(self, "扫描失败", err)

    def _set_scanning(self, on: bool):
        self.progress.setVisible(on)
        self.status.setText("扫描中…" if on else self.status.text())
        self.table.setEnabled(not on)
        self.filter_edit.setEnabled(not on)
        self._import_btn.setEnabled(not on)
        if not on:
            self._worker = None

    # ------------------------------------------------------------ 过滤 / 选择
    def _render(self):
        self._apply_filter()

    def _apply_filter(self, *args):
        q = self.filter_edit.text().strip().lower()
        if q:
            self._filtered = [i for i in self._rows if q in i.display.lower()
                              or q in i.path.lower()]
        else:
            self._filtered = list(self._rows)
        self._rebuild_table()

    def _rebuild_table(self):
        rows = self._filtered
        self.table.setRowCount(len(rows))
        self.table.setUpdatesEnabled(False)
        for i, info in enumerate(rows):
            chk = QCheckBox()
            chk.setChecked(True)
            self.table.setCellWidget(i, 0, chk)
            self.table.setItem(i, 1, QTableWidgetItem(info.display))
            self.table.setItem(i, 2, QTableWidgetItem(info.remote_url or "（无远端）"))
            self.table.setItem(i, 3, QTableWidgetItem(info.path))
        self.table.setUpdatesEnabled(True)

    def _checked(self) -> List[RepoInfo]:
        sel = []
        for i, info in enumerate(self._filtered):
            w = self.table.cellWidget(i, 0)
            if isinstance(w, QCheckBox) and w.isChecked():
                sel.append(info)
        return sel

    def _select_all(self):
        for i in range(len(self._filtered)):
            w = self.table.cellWidget(i, 0)
            if isinstance(w, QCheckBox):
                w.setChecked(True)

    def _select_none(self):
        for i in range(len(self._filtered)):
            w = self.table.cellWidget(i, 0)
            if isinstance(w, QCheckBox):
                w.setChecked(False)

    def _on_import(self):
        sel = self._checked()
        if not sel:
            QMessageBox.information(self, "提示", "请至少勾选一个仓库。")
            return
        self._selected = sel
        self.accept()

    def _import_and_accept(self):
        """勾选 → 入库 → accept（供手动调用，未绑定时测试直达）。"""
        sel = self._checked()
        if not sel:
            QMessageBox.information(self, "提示", "请至少勾选一个仓库。")
            return
        self._selected = sel
        self.accept()

    @property
    def selected(self) -> List[RepoInfo]:
        return self._selected

    def import_selected(self) -> int:
        """把勾选的仓库写入 DB，返回导入数量。"""
        if not self._db or not self._selected:
            return 0
        n = 0
        for info in self._selected:
            spec = to_spec(info)
            try:
                self._db.upsert_repo(spec, info.path,
                                     host=info.host or "local",
                                     default_branch=None,
                                     head_sha=info.head_sha or None)
                n += 1
            except Exception:
                continue
        return n
