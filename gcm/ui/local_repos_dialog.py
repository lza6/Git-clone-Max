"""本地仓库导入对话框：扫描目标目录，勾选要纳入管理的已有 git 仓库。"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

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

from ..app.scanner import RepoInfo, known_keys_from_db, to_spec
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
        self._rows: list[RepoInfo] = []
        self._filtered: list[RepoInfo] = []
        self._selected: list[RepoInfo] = []
        self._worker: Optional[ScanWorker] = None
        # P-perf：已入库键集合预载（O(1) 判断），避免逐行查库
        self._known_keys: set[str] = set()
        self._refresh_known_keys()

        self._roots: list[Path] = []
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

        self.chk_only_new = QCheckBox("仅显示未入库")
        self.chk_only_new.stateChanged.connect(self._apply_filter)
        v.addWidget(self.chk_only_new)

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
        self.chk_overwrite = QCheckBox("覆盖已入库路径")
        btns.addButton(self.btn_select_all, QDialogButtonBox.ButtonRole.ActionRole)
        btns.addButton(self.btn_select_none, QDialogButtonBox.ButtonRole.ActionRole)
        btns.addButton(self.chk_overwrite, QDialogButtonBox.ButtonRole.ActionRole)
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

    def _on_scan_done(self, result: list[RepoInfo]):
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

    def _refresh_known_keys(self) -> None:
        """预载已入库键集合（list_repos 一次全量），O(1) 判断后续每一行。"""
        if self._db is None:
            self._known_keys = set()
            return
        try:
            self._known_keys = known_keys_from_db(self._db.list_repos())
        except Exception:
            self._known_keys = set()

    def _is_imported(self, info: RepoInfo) -> bool:
        """判断仓库是否已入库（基于预载键集合，O(1)）；db 为 None 时一律未入库。"""
        return info.key in self._known_keys

    def _apply_filter(self, *args):
        q = self.filter_edit.text().strip().lower()
        if q:
            # 用 UserRole 中的原显示名匹配（灰标追加的「（已入库）」后缀不参与过滤）
            self._filtered = [
                i for i in self._rows
                if (q in i.display.lower() or q in i.path.lower())
            ]
        else:
            self._filtered = list(self._rows)
        if self.chk_only_new.isChecked():
            self._filtered = [i for i in self._filtered if not self._is_imported(i)]
        self._rebuild_table()

    def _rebuild_table(self):
        rows = self._filtered
        self.table.setRowCount(len(rows))
        self.table.setUpdatesEnabled(False)
        for i, info in enumerate(rows):
            imported = self._is_imported(info)
            # P-perf：用 item checkState 替代 QCheckBox cellWidget（几千行渲染不卡）
            chk = QTableWidgetItem()
            chk_flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable \
                | Qt.ItemFlag.ItemIsUserCheckable
            if imported:
                chk_flags &= ~Qt.ItemFlag.ItemIsEnabled
            chk.setFlags(chk_flags)
            chk.setCheckState(
                Qt.CheckState.Unchecked if imported else Qt.CheckState.Checked)
            self.table.setItem(i, 0, chk)
            display = info.display
            if imported:
                display = f"{display}（已入库）"
            item = QTableWidgetItem(display)
            item.setData(Qt.ItemDataRole.UserRole, info.display)
            self.table.setItem(i, 1, item)
            self.table.setItem(i, 2, QTableWidgetItem(info.remote_url or "（无远端）"))
            self.table.setItem(i, 3, QTableWidgetItem(info.path))
        self.table.setUpdatesEnabled(True)

    def _checked(self) -> list[RepoInfo]:
        sel = []
        for i, info in enumerate(self._filtered):
            it = self.table.item(i, 0)
            if it is not None and it.checkState() == Qt.CheckState.Checked:
                sel.append(info)
        return sel

    def _select_all(self):
        for i in range(len(self._filtered)):
            it = self.table.item(i, 0)
            if it is not None and (it.flags() & Qt.ItemFlag.ItemIsEnabled):
                it.setCheckState(Qt.CheckState.Checked)

    def _select_none(self):
        for i in range(len(self._filtered)):
            it = self.table.item(i, 0)
            if it is not None:
                it.setCheckState(Qt.CheckState.Unchecked)

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
    def selected(self) -> list[RepoInfo]:
        return self._selected

    def import_selected(self) -> int:
        """把勾选的仓库写入 DB，返回导入数量。

        已入库仓库默认跳过（不覆盖原 local_path）；
        勾选「覆盖已入库路径」后才重新 upsert。
        """
        if not self._db or not self._selected:
            return 0
        to_write = []
        for info in self._selected:
            if self._is_imported(info) and not self.chk_overwrite.isChecked():
                continue
            spec = to_spec(info)
            to_write.append((spec, info.path, info.host or "local",
                             info.head_sha or None))
        if not to_write:
            return 0
        try:
            n = self._db.bulk_upsert_repos(to_write)
        except Exception:
            # 批量失败回退到逐条 upsert（保持可用性）
            n = 0
            for spec, local_path, host, head_sha in to_write:
                try:
                    self._db.upsert_repo(spec, local_path, host=host,
                                         default_branch=None, head_sha=head_sha)
                    n += 1
                except Exception:
                    continue
        # 更新已知键集合，保证后续勾选/过滤正确
        self._refresh_known_keys()
        return n
