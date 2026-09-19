"""G45-6 仓库管理页面板（QWidget 子类）：管理页 UI + 管理动作。
跨窗口动作（一键更新/取消/导入本地/双击历史/行内历史按钮）经信号回传 MainWindow；
其余服务（db/emit_log/statusBar/_std_icon）经 owner 引用。对外方法名与
MainWindow 保持一致（MainWindow 保留同名转发，行为零变化）。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from . import main_window as _mw  # 兼容测试对 main_window.QMessageBox 等模块级 patch
from .theme import LogLevel


class ManagePanel(QWidget):
    """仓库管理页面板。跨窗口动作经信号回传 MainWindow。"""

    update_all_requested = pyqtSignal()
    cancel_all_requested = pyqtSignal()
    import_local_requested = pyqtSignal()
    manage_double_clicked = pyqtSignal(object)
    history_row_clicked = pyqtSignal(int)

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setSpacing(8)
        top = QHBoxLayout()
        self.manage_stats = QLabel("共 0 个仓库")
        self.manage_stats.setObjectName("muted")
        top.addWidget(self.manage_stats)
        top.addStretch()
        self.btn_update_all = QPushButton("一键更新全部")
        self.btn_update_all.setObjectName("primary")
        self.btn_update_all.clicked.connect(lambda _=False: self.update_all_requested.emit())
        self.btn_update_all.setIcon(self.owner._std_icon("refresh", self.btn_update_all))
        self.btn_cancel_manage = QPushButton("取消全部")
        self.btn_cancel_manage.setEnabled(False)
        self.btn_cancel_manage.clicked.connect(lambda _=False: self.cancel_all_requested.emit())
        self.btn_cancel_manage.setIcon(self.owner._std_icon("stop", self.btn_cancel_manage))
        self.btn_refresh = QPushButton("刷新列表")
        self.btn_refresh.clicked.connect(self._refresh_manage)
        self.btn_refresh.setIcon(self.owner._std_icon("refresh", self.btn_refresh))
        self.btn_import_local = QPushButton("导入本地已有仓库")
        self.btn_import_local.clicked.connect(lambda _=False: self.import_local_requested.emit())
        self.btn_import_local.setIcon(self.owner._std_icon("folder", self.btn_import_local))
        self.btn_delete = QPushButton("删除选中记录")
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_delete.setIcon(self.owner._std_icon("trash", self.btn_delete))
        self.btn_batch_tag = QPushButton("打标签")
        self.btn_batch_tag.setToolTip("给选中的仓库追加标签（多选批量）")
        self.btn_batch_tag.clicked.connect(self.batch_tag_selected)
        self.btn_export_selected = QPushButton("导出所选")
        self.btn_export_selected.setToolTip("把选中的仓库导出为 CSV 报表")
        self.btn_export_selected.clicked.connect(self.export_selected_csv)
        self.btn_export_selected.setIcon(self.owner._std_icon("save", self.btn_export_selected))
        top.addWidget(self.btn_import_local)
        top.addWidget(self.btn_update_all)
        top.addWidget(self.btn_cancel_manage)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_delete)
        top.addWidget(self.btn_batch_tag)
        top.addWidget(self.btn_export_selected)
        v.addLayout(top)

        # 模型化视图：数据与视图解耦，大批量行不卡（共享按钮 + 懒加载）
        from .manage_model import ManageModel
        self.manage_model = ManageModel(parent=self)
        self.manage_table = QTableView()
        self.manage_table.setModel(self.manage_model)
        self.manage_table.horizontalHeader().setDefaultAlignment(Qt.AlignmentFlag.AlignLeft)
        self.manage_table.horizontalHeader().setStretchLastSection(True)
        self.manage_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.manage_table.verticalHeader().setVisible(False)
        self.manage_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.manage_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.manage_table.setSelectionMode(QTableView.SelectionMode.ExtendedSelection)
        self.manage_table.setAlternatingRowColors(False)
        self.manage_table.setMinimumHeight(260)
        self.manage_table.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.manage_table.doubleClicked.connect(self.manage_double_clicked.emit)
        # G03-7 末列「查看」按钮委托：每行独立可点击（不再依赖选中行 + 共享按钮）
        from .manage_model import HistoryButtonDelegate
        self._hist_delegate = HistoryButtonDelegate(self.manage_table)
        self._hist_delegate.clicked.connect(self.history_row_clicked.emit)
        self.manage_table.setItemDelegateForColumn(5, self._hist_delegate)
        v.addWidget(self.manage_table, 3)
        # G36-5 管理页空态引导：overlay 标签（有仓库时隐藏）
        self._empty_manage = QLabel("📁 尚未导入仓库\n点击「导入本地已有仓库」开始")
        self._empty_manage.setObjectName("muted")
        self._empty_manage.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_manage.setWordWrap(True)
        self._empty_manage.setVisible(False)
        self._empty_manage.setParent(self.manage_table)
        self._empty_manage.raise_()

        self.manage_desc = QLabel(
            "「一键更新全部」会按 作者__仓库 命名找到每个仓库目录，"
            "已存在则 git fetch 增量更新；本地有改动冲突时保留本地、标记冲突，绝不覆盖。\n"
            "「导入本地已有仓库」能扫描任意目录下已存在的 git 仓库，一并纳入管理。")
        self.manage_desc.setObjectName("muted")
        self.manage_desc.setWordWrap(True)

        self.manage_table.verticalHeader().setDefaultSectionSize(34)
        v.addWidget(self.manage_desc)

        # 保持状态栏"历史"列宽固定
        self.manage_table.setColumnWidth(5, 70)
        v.addWidget(self.manage_table)




    def _load_db_into_grid(self):
        """从 DB 加载仓库列表到模型（全 host，含本地导入仓库；批量懒渲染）。"""
        self.manage_model.set_rows(self.owner.db.list_repos())
        base = f"共 {self.manage_model.rowCount()} 个仓库"
        # G35-6 平台分布：内存聚合（host_summary 由 manage_model 提供）
        try:
            hs = self.manage_model.host_summary()
            if hs:
                base += f" ｜ {hs}"
        except Exception:
            pass
        self.manage_stats.setText(base)
        # G36-5 管理页空态：无仓库时显示引导 overlay
        try:
            ov = getattr(self, "_empty_manage", None)
            if ov is not None:
                visible = self.manage_model.rowCount() == 0
                ov.setVisible(visible)
                if visible:
                    ov.setGeometry(self.manage_table.rect())
                    ov.raise_()
        except Exception:
            pass

    def _refresh_manage(self):
        self.owner._load_db_into_grid()

    def delete_selected(self):
        rows = sorted({i.row() for i in self.manage_table.selectedIndexes()})
        if not rows:
            _mw.QMessageBox.information(self.owner, "提示", "请先选择要删除的记录。")
            return
        if _mw.QMessageBox.question(self.owner, "确认删除",
                                f"将从数据库中删除 {len(rows)} 条记录（不影响已下载的仓库目录）。\n继续？") != _mw.QMessageBox.StandardButton.Yes:
            return
        repo_ids = set()
        for r in rows:
            row = self.manage_model.row_at(r)
            if row is not None:
                rec = self.owner.db.get_repo(row.owner, row.repo, row.host)
                if rec:
                    repo_ids.add(rec["id"])
        for rid in repo_ids:
            self.owner.db.delete_repo(rid)
        self.owner._load_db_into_grid()
        self.owner._emit_log(_mw._fmt_dt(), LogLevel.INFO, f"已删除 {len(repo_ids)} 条数据库记录")

    # --------------------------------------------------------- G35-9 批量操作

    def batch_tag_selected(self):
        """给选中的仓库追加标签（多选批量，覆盖式设置指定标签）。"""
        rows = sorted({i.row() for i in self.manage_table.selectedIndexes()})
        if not rows:
            _mw.QMessageBox.information(self.owner, "提示", "请先选择要打标签的仓库。")
            return
        tag, ok = _mw.QInputDialog.getText(
            self, "批量打标签", "输入标签名（多个用逗号分隔，将覆盖原标签）：")
        if not ok:
            return
        tags = [t.strip() for t in tag.split(",") if t.strip()]
        if not tags:
            return
        n = 0
        for r in rows:
            row = self.manage_model.row_at(r)
            if row is not None:
                rec = self.owner.db.get_repo(row.owner, row.repo, row.host)
                if rec:
                    self.owner.db.set_tags(rec["id"], tags)
                    n += 1
        self.owner._load_db_into_grid()
        self.owner._emit_log(_mw._fmt_dt(), LogLevel.INFO, f"已为 {n} 个仓库设置标签：{', '.join(tags)}")
        self.owner.statusBar().showMessage(f"已为 {n} 个仓库设置标签")

    def export_selected_csv(self):
        """把选中的仓库导出为 CSV（仅所选行；无历史则同样导出元数据）。"""
        rows = sorted({i.row() for i in self.manage_table.selectedIndexes()})
        if not rows:
            _mw.QMessageBox.information(self.owner, "提示", "请先选择要导出的仓库。")
            return
        path, _ = _mw.QFileDialog.getSaveFileName(
            self, "导出所选仓库", str(self.owner.data_dir / "selected_repos.csv"),
            "CSV (*.csv)")
        if not path:
            return
        try:
            import csv as _csv
            sel = []
            for r in rows:
                row = self.manage_model.row_at(r)
                if row is not None:
                    sel.append({
                        "owner": row.owner, "repo": row.repo, "host": row.host,
                        "local_path": row.local_path, "folder_name": row.folder_name,
                        "tags": row.tags,
                    })
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = _csv.DictWriter(f, fieldnames=["owner", "repo", "host",
                                                   "local_path", "folder_name", "tags"])
                w.writeheader()
                w.writerows(sel)
            self.owner._emit_log(_mw._fmt_dt(), LogLevel.INFO, f"已导出所选 {len(sel)} 个仓库：{path}")
            self.owner.statusBar().showMessage(f"已导出 {len(sel)} 个仓库")
        except Exception as e:
            _mw.QMessageBox.critical(self.owner, "导出失败", str(e))

    def _on_manage_double_clicked(self, index):
        row = index.row()
        r = self.manage_model.row_at(row)
        if r is not None:
            self.owner.show_history(r.repo_id)

    def _on_hist_row_clicked(self, row: int):
        """G03-7 行内「查看」按钮委托回调：直接打开该行历史（无需先选中）。"""
        r = self.manage_model.row_at(row)
        if r is not None:
            self.owner.show_history(r.repo_id)

    def _on_hist_btn(self):
        """兼容入口：对当前选中的行打开历史（多选场景仍可用）。"""
        idx = self.manage_table.selectionModel().selectedRows()
        if not idx:
            return
        for i in idx:
            r = self.manage_model.row_at(i.row())
            if r is not None:
                self.owner.show_history(r.repo_id)
