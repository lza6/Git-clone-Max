# -*- coding: utf-8 -*-
"""主窗口：三个 Tab（下载中心 / 仓库管理 / 设置 与 黑匣子日志）。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import QThreadPool, QTimer, Qt, pyqtSlot
from PyQt6.QtGui import QCloseEvent, QColor, QFont, QTextCursor
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..app.engine import SyncEngine
from ..app.url_lib import parse_repo_url, parse_urls
from ..app.worker import CancelFlag, CloneWorker, TaskPayload, WorkerSignals
from ..db.repo_db import Database, load_progress
from ..db.settings import Settings, SettingsStore
from ..git.service import GitService
from ..models import RepoSpec, SyncResult, SyncStatus
from .repo_detail_dialog import RepoDetailDialog
from .theme import LogEvent, LogLevel, LogModel, PALETTE, QSS, make_highlighter

DOMAIN = "github.com"
MAX_CONCURRENCY = 32  # 并发上限：拉满速度（设置 SpinBox 同源）


def _fmt_dt() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _dur(ms: int) -> str:
    if ms <= 0:
        return "—"
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    return f"{int(s // 60)}m{int(s % 60)}s"


class MainWindow(QMainWindow):
    """Git-clone-Max 主窗口。"""

    def __init__(self, data_dir: str | Path, db: Database | None = None,
                 settings: SettingsStore | None = None):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = db or Database(self.data_dir / "repos.db")
        self.settings_store = settings or SettingsStore(self.data_dir / "settings.json")
        self.settings = self.settings_store.load()
        self.progress_path = self.data_dir / "progress.json"

        # 统一调度引擎：并发/去重/进度落盘/取消 全部收敛到 engine（不再各自拼装）
        self.engine = SyncEngine(
            self.data_dir / "clones",
            db=self.db,
            progress_path=self.progress_path,
            concurrency=int(self.settings.concurrency),
            fetch_timeout=self.settings.fetch_timeout,
            clone_timeout=self.settings.clone_timeout,
            retries=self.settings.retries,
            proxy=self.settings.proxy,
            token=self.settings.token,
        )
        self.engine.line.connect(self._on_engine_line)
        self.engine.progress.connect(self._on_worker_progress)
        self.engine.result.connect(self._on_worker_result)
        self.engine.finished.connect(self._on_engine_finished)
        # 统一调度引擎：并发/去重/进度落盘/取消 全部收敛到 engine（不再各自拼装）
        self.engine = SyncEngine(
            self.data_dir / "clones",
            db=self.db,
            progress_path=self.progress_path,
            concurrency=int(self.settings.concurrency),
            fetch_timeout=self.settings.fetch_timeout,
            clone_timeout=self.settings.clone_timeout,
            retries=self.settings.retries,
            proxy=self.settings.proxy,
            token=self.settings.token,
        )
        self.engine.line.connect(self._on_engine_line)
        self.engine.progress.connect(self._on_worker_progress)
        self.engine.result.connect(self._on_worker_result)
        self.engine.finished.connect(self._on_engine_finished)
        # 兼容旧引用（测试/托盘可能读 pool）
        self.pool = self.engine.pool

        self.tasks: list = []               # list[SyncEngine] 兼容引用（实际由 engine 管理）
        self.flags: dict = {}               # index -> CancelFlag（引擎接管后仅兼容辅助）
        self.row_specs: dict = {}           # index -> RepoSpec
        self.busy = False
        self._close_requested = False

        # 日志模型
        self.log = LogModel(max_entries=3000)
        self.log.appended.connect(self._on_log_appended)

        # 日志洪峰节流：git 高输出时合并为一次批量刷新，避免主线程被刷屏拖慢
        self._log_batch: list = []
        self._log_batch_timer = QTimer(self)
        self._log_batch_timer.setInterval(120)
        self._log_batch_timer.timeout.connect(self._flush_log_batch)
        self._log_batch_timer.start()

        self._build_ui()
        self.setStyleSheet(QSS)
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, f"Git-clone-Max 启动，数据目录：{self.data_dir}")
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, f"并行线程：{self.engine.concurrency}（上限 {MAX_CONCURRENCY}）")
        self._load_db_into_grid()

        # 系统托盘（失败静默降级，不影响主流程）
        from .tray import TrayController
        self.tray = TrayController(parent=self)
        if self.settings.minimize_to_tray:
            self.tray.install()

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        self.setWindowTitle("Git-clone-Max — GitHub 仓库批量并行下载")
        self.resize(1240, 840)
        self.setMinimumSize(1020, 700)

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # 顶栏
        head = QHBoxLayout()
        t = QLabel("⬇  Git-clone-Max")
        t.setObjectName("pageTitle")
        head.addWidget(t)
        head.addStretch()
        self.thread_lbl = QLabel(f"并行线程 {self.engine.concurrency}")
        self.thread_lbl.setObjectName("muted")
        head.addWidget(self.thread_lbl)
        root.addLayout(head)

        self.tabs = QTabWidget()
        root.addWidget(self.tabs, 1)

        self.tab_download = QWidget()
        self.tab_manage = QWidget()
        self.tab_settings = QWidget()
        self.tabs.addTab(self.tab_download, "下载中心")
        self.tabs.addTab(self.tab_manage, "仓库管理")
        self.tabs.addTab(self.tab_settings, "设置与日志")

        self._build_download_tab()
        self._build_manage_tab()
        self._build_settings_tab()

        self.statusBar().showMessage("就绪")

    # ---------------- 下载中心
    def _build_download_tab(self):
        v = QVBoxLayout(self.tab_download)
        v.setSpacing(10)

        box = QGroupBox("仓库地址（每行一个）")
        b = QVBoxLayout(box)
        self.repo_input = QPlainTextEdit()
        self.repo_input.setPlaceholderText(
            "每行一个 GitHub 仓库地址，例如：\n"
            "https://github.com/vercel-labs/skills\n"
            "git@github.com:microsoft/azure-skills.git\n"
            "vercel-labs/agent-skills\n"
            "（地址中的 作者/仓库名 将自动作为文件夹名：作者__仓库）"
        )
        self.repo_input.setMinimumHeight(150)
        b.addWidget(self.repo_input)
        quick = QHBoxLayout()
        quick.addWidget(QLabel("快捷填充："))
        for repo in ("vercel-labs/skills", "anthropics/skills",
                     "microsoft/azure-skills", "remotion-dev/skills",
                     "slidevjs/slidev", "openmeterio/openmeter"):
            btn = QPushButton(repo)
            btn.clicked.connect(lambda _=False, r=repo: self.repo_input.appendPlainText(
                f"https://github.com/{r}"))
            quick.addWidget(btn)
        quick.addStretch()
        b.addLayout(quick)
        v.addWidget(box)

        opts = QHBoxLayout()
        g1 = QGroupBox("下载位置")
        l1 = QHBoxLayout(g1)
        self.target_edit = QLineEdit(str(self.data_dir / "clones"))
        btn_browse = QPushButton("浏览…")
        btn_browse.clicked.connect(self.choose_target)
        l1.addWidget(self.target_edit, 1)
        l1.addWidget(btn_browse)
        opts.addWidget(g1, 1)

        g2 = QGroupBox("克隆模式")
        l2 = QHBoxLayout(g2)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["满量克隆（完整历史）", "浅克隆（最新代码）"])
        self.depth_spin = QSpinBox()
        self.depth_spin.setRange(1, 10000)
        self.depth_spin.setValue(1)
        self.depth_spin.setSuffix(" 层")
        self.depth_spin.setEnabled(False)
        self.mode_combo.currentIndexChanged.connect(
            lambda i: self.depth_spin.setEnabled(i == 1))
        l2.addWidget(self.mode_combo)
        l2.addWidget(self.depth_spin)
        opts.addWidget(g2)
        v.addLayout(opts)

        btns = QHBoxLayout()
        self.btn_start = QPushButton("▶  开始并行下载 / 更新")
        self.btn_start.setObjectName("primary")
        self.btn_start.setMinimumHeight(38)
        self.btn_start.clicked.connect(self.start_all)
        self.btn_cancel = QPushButton("⏹ 取消全部")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_all)
        self.btn_clear = QPushButton("清空列表")
        self.btn_clear.clicked.connect(self.repo_input.clear)
        self.btn_open = QPushButton("📂 打开克隆目录")
        self.btn_open.clicked.connect(self.open_target)
        btns.addWidget(self.btn_start, 2)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_clear)
        btns.addWidget(self.btn_open)
        btns.addStretch()
        v.addLayout(btns)

        # 进度表
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["仓库", "进度", "状态", "本次", "详情"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in (1, 2, 3, 4):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(200)
        v.addWidget(self.table, 3)

    # ---------------- 仓库管理
    def _build_manage_tab(self):
        v = QVBoxLayout(self.tab_manage)
        top = QHBoxLayout()
        self.manage_stats = QLabel("共 0 个仓库")
        self.manage_stats.setObjectName("muted")
        top.addWidget(self.manage_stats)
        top.addStretch()
        self.btn_update_all = QPushButton("⟳  一键更新全部")
        self.btn_update_all.setObjectName("primary")
        self.btn_update_all.clicked.connect(self.update_all)
        self.btn_cancel_manage = QPushButton("取消全部")
        self.btn_cancel_manage.setEnabled(False)
        self.btn_cancel_manage.clicked.connect(self.cancel_all)
        self.btn_refresh = QPushButton("刷新列表")
        self.btn_refresh.clicked.connect(self._refresh_manage)
        self.btn_import_local = QPushButton("📥 导入本地已有仓库")
        self.btn_import_local.clicked.connect(self.import_local_repos)
        self.btn_delete = QPushButton("🗑 删除选中记录")
        self.btn_delete.clicked.connect(self.delete_selected)
        top.addWidget(self.btn_import_local)
        top.addWidget(self.btn_update_all)
        top.addWidget(self.btn_cancel_manage)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_delete)
        v.addLayout(top)

        # 模型化视图：数据与视图解耦，大批量行不卡（共享按钮 + 懒加载）
        from .manage_model import ManageModel
        self.manage_model = ManageModel(parent=self)
        self.manage_table = QTableView()
        self.manage_table.setModel(self.manage_model)
        labels = ["文件夹", "仓库", "路径", "最近同步", "HEAD", "历史"]
        self.manage_table.horizontalHeader().setDefaultAlignment(
            Qt.AlignmentFlag.AlignLeft)
        self.manage_table.horizontalHeader().setStretchLastSection(True)
        self.manage_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch)
        self.manage_table.verticalHeader().setVisible(False)
        self.manage_table.setEditTriggers(QTableView.EditTrigger.NoEditTriggers)
        self.manage_table.setSelectionBehavior(
            QTableView.SelectionBehavior.SelectRows)
        self.manage_table.setSelectionMode(
            QTableView.SelectionMode.ExtendedSelection)
        self.manage_table.setAlternatingRowColors(False)
        self.manage_table.setMinimumHeight(260)
        self.manage_table.setContextMenuPolicy(Qt.ContextMenuPolicy.DefaultContextMenu)
        self.manage_table.doubleClicked.connect(self._on_manage_double_clicked)
        # 每行共享的"查看历史"按钮（避免每行 setCellWidget 占用）
        self._hist_btn = QPushButton("查看")
        self._hist_btn.setStyleSheet(
            "QPushButton { padding: 2px 8px; font-size: 12px; }")
        self._hist_btn.clicked.connect(self._on_hist_btn)
        v.addWidget(self.manage_table, 3)

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

    # ---------------- 设置与日志
    def _build_settings_tab(self):
        v = QVBoxLayout(self.tab_settings)
        v.setSpacing(10)

        # 设置与日志
        g1 = QGroupBox("启动与后台运行")
        l1 = QHBoxLayout(g1)
        self.ck_autostart = QCheckBox("开机自启（写入任务计划：登录时启动一次）")
        l1.addWidget(self.ck_autostart)
        self.btn_check_update = QPushButton("检查更新")
        self.btn_check_update.clicked.connect(self.check_update_now)
        l1.addWidget(self.btn_check_update)
        l1.addStretch()
        v.addWidget(g1)

        # 并行 / 网络设置（持久化到 settings.json）
        g3 = QGroupBox("并行与网络")
        l3 = QGridLayout(g3)
        l3.setContentsMargins(10, 10, 10, 10)
        l3.setHorizontalSpacing(12)
        l3.setVerticalSpacing(8)
        l3.addWidget(QLabel("并发数（1–16）："), 0, 0)
        self.spin_concurrency = QSpinBox()
        self.spin_concurrency.setRange(1, 32)
        self.spin_concurrency.setValue(int(self.settings.concurrency))
        self.spin_concurrency.valueChanged.connect(self._save_concurrency)
        l3.addWidget(self.spin_concurrency, 0, 1)
        l3.addWidget(QLabel("fetch 超时（秒）："), 0, 2)
        self.spin_fetch_timeout = QSpinBox()
        self.spin_fetch_timeout.setRange(10, 3600)
        self.spin_fetch_timeout.setValue(int(self.settings.fetch_timeout))
        self.spin_fetch_timeout.valueChanged.connect(self._save_fetch_timeout)
        l3.addWidget(self.spin_fetch_timeout, 0, 3)
        l3.addWidget(QLabel("自动重试（次）："), 1, 0)
        self.spin_retries = QSpinBox()
        self.spin_retries.setRange(0, 5)
        self.spin_retries.setValue(int(self.settings.retries))
        self.spin_retries.valueChanged.connect(self._save_retries)
        l3.addWidget(self.spin_retries, 1, 1)
        l3.addWidget(QLabel("HTTP 代理："), 1, 2)
        self.edit_proxy = QLineEdit(self.settings.proxy)
        self.edit_proxy.setPlaceholderText("http://127.0.0.1:7890（留空不代理）")
        self.edit_proxy.editingFinished.connect(self._save_proxy)
        l3.addWidget(self.edit_proxy, 1, 3)
        self.ck_unshallow = QCheckBox("浅克隆仓库更新时拉全量历史")
        self.ck_unshallow.setChecked(bool(self.settings.fetch_unshallow))
        self.ck_unshallow.stateChanged.connect(self._save_fetch_unshallow)
        l3.addWidget(self.ck_unshallow, 2, 0, 1, 2)
        l3.addWidget(QLabel("GitHub Token："), 2, 2)
        self.edit_token = QLineEdit(self.settings.token)
        self.edit_token.setPlaceholderText("私有仓库认证令牌（可选，留空不传递）")
        self.edit_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_token.editingFinished.connect(self._save_token)
        l3.addWidget(self.edit_token, 2, 3)
        v.addWidget(g3)

        g2 = QGroupBox("黑匣子日志（实时）")
        l2 = QVBoxLayout(g2)
        tb = QHBoxLayout()
        self.log_count = QLabel("0 条")
        self.log_count.setObjectName("muted")
        tb.addWidget(self.log_count)
        tb.addStretch()
        self.btn_save_log = QPushButton("导出日志…")
        self.btn_save_log.clicked.connect(self.save_log)
        self.btn_clear_log = QPushButton("清空日志")
        self.btn_clear_log.clicked.connect(self.clear_log)
        tb.addWidget(self.btn_save_log)
        tb.addWidget(self.btn_clear_log)
        l2.addLayout(tb)
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("console")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(4000)
        l2.addWidget(self.log_view, 1)
        v.addWidget(g2, 2)

        v.addStretch()

    # ------------------------------------------------------------ 日志
    def _emit_log(self, time: str, level: LogLevel, text: str):
        """统一日志入口：入模型 + 节流批量渲染（高频 git 输出不阻塞主线程）。"""
        if not hasattr(self, "_log_batch"):
            self._log_batch = []
            self._log_batch_timer = QTimer(self)
            self._log_batch_timer.setInterval(120)
            self._log_batch_timer.timeout.connect(self._flush_log_batch)
            self._log_batch_timer.start()
        self.log.append(time, level, text)
        # 节流：把待渲染条目并入批量，由定时器统一 flush
        from .theme import LogEvent as _LE
        ev = _LE(time, level, text)
        self._log_batch.append(ev)

    def _flush_log_batch(self):
        if not getattr(self, "_log_batch", None):
            return
        batch, self._log_batch = self._log_batch, []
        if not (hasattr(self, "log_view") and self.log_view):
            return
        cur = self.log_view
        for ev in batch:
            prefix = {
                LogLevel.WARN: "[警告] ",
                LogLevel.ERROR: "[错误] ",
                LogLevel.SYSTEM: "[系统] ",
            }.get(ev.level, "")
            cur.appendPlainText(f"[{ev.time}] {prefix}{ev.text}")
        # 滚动跟随（仅在用户已处于底部时）
        sb = cur.verticalScrollBar()
        if sb.value() >= sb.maximum() - 40:
            cur.verticalScrollBar().setValue(cur.verticalScrollBar().maximum())

    @pyqtSlot(int)
    def _on_log_appended(self, count):
        self.log_count.setText(f"{count} 条")

    def has_pending_logs(self) -> bool:
        """供 closeEvent 判断是否还有积压节流日志需 flush。"""
        return bool(getattr(self, "_log_batch", None))

    def clear_log(self):
        self.log.clear()
        # 清空节流积压，避免清空后 ≤120ms 幽灵追加回旧行
        if hasattr(self, "_log_batch"):
            self._log_batch.clear()
        self.log_view.clear()

    # ------------------------------------------------------------ 设置持久化
    def _save_concurrency(self, value):
        self.settings.concurrency = int(value)
        if hasattr(self, "engine") and self.engine is not None:
            self.engine.set_concurrency(int(value))
            self.thread_lbl.setText(f"并行线程 {self.engine.concurrency}")
        self.settings_store.save(self.settings)

    def _save_fetch_timeout(self, value):
        self.settings.fetch_timeout = int(value)
        self.settings_store.save(self.settings)

    def _save_retries(self, value):
        self.settings.retries = int(value)
        self.settings_store.save(self.settings)

    def _save_proxy(self):
        self.settings.proxy = self.edit_proxy.text().strip()
        self.settings_store.save(self.settings)

    def _save_token(self):
        self.settings.token = self.edit_token.text().strip()
        self.settings_store.save(self.settings)

    def _save_fetch_unshallow(self, checked):
        self.settings.fetch_unshallow = bool(checked)
        self.settings_store.save(self.settings)

    # ------------------------------------------------------------ 更新检查
    def check_update_now(self):
        try:
            from ..app.updater import check_latest
            from .. import __version__
            has_new, ver, url, err = check_latest()
            if err:
                QMessageBox.information(self, "检查更新", err)
            elif has_new:
                ret = QMessageBox.question(
                    self, "发现新版本",
                    f"当前版本 {__version__}，最新版本 {ver}。\n是否打开下载页面？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
                if ret == QMessageBox.StandardButton.Yes and url:
                    import webbrowser
                    webbrowser.open(url)
            else:
                QMessageBox.information(self, "检查更新", f"已是最新版本（{__version__}）")
        except Exception as e:
            QMessageBox.information(self, "检查更新", f"检查更新失败：{e}")

    def save_log(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "导出日志", str(self.data_dir / "clone_log.txt"), "文本文件 (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(self.log.to_plain_text(), encoding="utf-8")
            self._emit_log(_fmt_dt(), LogLevel.INFO, f"日志已导出：{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    # ------------------------------------------------------------ 动作
    def choose_target(self):
        d = QFileDialog.getExistingDirectory(self, "选择下载目录", self.target_edit.text())
        if d:
            self.target_edit.setText(d)

    def open_target(self):
        path = self.target_edit.text().strip()
        if not os.path.isdir(path):
            QMessageBox.warning(self, "目录不存在", path)
            return
        if sys.platform == "win32":
            os.startfile(path)  # noqa
        else:
            import shutil
            openers = ("xdg-open", "open")
            for op in openers:
                if shutil.which(op):
                    import subprocess
                    subprocess.Popen([op, path])
                    break

    def start_all(self):
        if self.busy:
            return
        text = self.repo_input.toPlainText()
        specs, invalid = parse_urls(text)
        if not specs:
            QMessageBox.information(
                self, "提示",
                "没有可用的 GitHub 地址。\n\n有效示例：\n"
                "https://github.com/vercel-labs/skills\ngit@github.com:microsoft/azure-skills.git\n"
                "vercel-labs/agent-skills")
            return
        if invalid:
            shown = "\n".join(f"  ✗ {line}" for line in invalid)
            ret = QMessageBox.question(
                self, "无效地址（忽略并继续？）",
                f"{len(invalid)} 行不是有效的 GitHub 仓库地址：\n{shown}\n\n"
                "继续将只同步有效行，是否忽略无效行？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
            self._emit_log(_fmt_dt(), LogLevel.WARN,
                            f"已忽略 {len(invalid)} 行无效地址：{', '.join(invalid)}")
        target = self.target_edit.text().strip()
        if not target:
            QMessageBox.warning(self, "提示", "请先选择下载目录。")
            return
        try:
            Path(target).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "提示", f"无法创建下载目录：{e}")
            return
        shallow = self.mode_combo.currentIndex() == 1
        depth = self.depth_spin.value()
        # 下载完成后自动清空输入框（用户要求）
        self._launch(specs, target_root=Path(target), shallow=shallow, depth=depth,
                     clear_input=True)
        if invalid:
            shown = "\n".join(f"  ✗ {line}" for line in invalid)
            ret = QMessageBox.question(
                self, "无效地址（忽略并继续？）",
                f"{len(invalid)} 行不是有效的 GitHub 仓库地址：\n{shown}\n\n"
                "继续将只同步有效行，是否忽略无效行？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
            self._emit_log(_fmt_dt(), LogLevel.WARN,
                            f"已忽略 {len(invalid)} 行无效地址：{', '.join(invalid)}")
        target = self.target_edit.text().strip()
        if not target:
            QMessageBox.warning(self, "提示", "请先选择下载目录。")
            return
        try:
            Path(target).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "提示", f"无法创建下载目录：{e}")
            return

        shallow = self.mode_combo.currentIndex() == 1
        depth = self.depth_spin.value()
        # 下载完成后自动清空输入框（用户要求）
        self._launch(specs, target_root=Path(target), shallow=shallow, depth=depth,
                     clear_input=True)

    @staticmethod
    def _rows_to_specs(rows):
        """从 DB 行构造 RepoSpec（统一入口，携带 local_path / is_local）。

        导入仓库的 local_path 必须透传，否则 GitService._repo_dir 会用
        root/folder_name 推导错误路径 → 误走克隆。is_local 由 url 是否存在判定
        （纯本地无远端才 is_local；本地路径远端仍可 fetch 增量更新）。
        """
        return [RepoSpec(owner=r["owner"], repo=r["repo"],
                         url_https=r["url"], folder_name=r["folder_name"],
                         local_path=r.get("local_path") or "",
                         is_local=not bool(r.get("url")))
                for r in rows]

    def update_all(self):
        if self.busy:
            QMessageBox.information(self, "提示", "有任务正在运行，请先取消或等待完成。")
            return
        rows = self.db.list_repos()  # 全 host（含本地导入仓库）
        if not rows:
            QMessageBox.information(self, "提示", "数据库中没有已记录的仓库，请先到「下载中心」添加。")
            return
        # 按 local_path 反推根目录集合：仓库可能分散在多个下载目录
        from collections import OrderedDict
        groups: "OrderedDict[str, list]" = OrderedDict()
        for r in rows:
            root = str(Path(r["local_path"] or r["folder_name"]).parent)
            groups.setdefault(root, []).append(r)
        if len(groups) == 1:
            root = next(iter(groups))
            specs = self._rows_to_specs(groups[root])
            self._launch(specs, target_root=Path(root), shallow=False, depth=1)
            return
        # 多根目录逐组串行启动（每组内部并行）
        self._multi_root_update = list(groups.items())
        self._update_next_root()

    def _update_next_root(self):
        if not getattr(self, "_multi_root_update", None):
            return
        root, rows = self._multi_root_update.pop(0)
        if self.busy:
            return  # 当前组仍在运行，等 finished 槽再拉下一组
        specs = self._rows_to_specs(rows)
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, f"一键更新：处理根目录 {root}（{len(specs)} 个仓库）")
        self._launch(specs, target_root=Path(root), shallow=False, depth=1,
                     multi_root_relay=True)

    def _launch(self, specs, target_root, shallow, depth, clear_input=False,
                multi_root_relay=False):
        self.busy = True
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_update_all.setEnabled(False)
        self.btn_cancel_manage.setEnabled(True)
        self.repo_input.setEnabled(False)
        self.mode_combo.setEnabled(False)
        self.depth_spin.setEnabled(False)

        self._emit_log(_fmt_dt(), LogLevel.SYSTEM,
                        f"开始并行同步 {len(specs)} 个仓库（{'浅克隆 depth=' + str(depth) if shallow else '满量'}）")
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, f"根目录：{target_root}")

        # 下载完成后自动清空输入框（默认开启）
        self._clear_after_finish = clear_input and not multi_root_relay
        self._multi_root_relay = multi_root_relay

        # 统一调度：engine 重建 root（根目录可变；超时/重试/代理/token 来自设置）
        self.engine = SyncEngine(
            target_root,
            db=self.db,
            progress_path=self.progress_path,
            concurrency=int(self.settings.concurrency),
            fetch_timeout=self.settings.fetch_timeout,
            clone_timeout=self.settings.clone_timeout,
            retries=self.settings.retries,
            proxy=self.settings.proxy,
            token=self.settings.token,
        )
        self.engine.line.connect(self._on_engine_line)
        self.engine.progress.connect(self._on_worker_progress)
        self.engine.result.connect(self._on_worker_result)
        self.engine.finished.connect(self._on_engine_finished)
        self.pool = self.engine.pool

        # 表格行数与去重后实际调度数对齐（engine 内部去重）
        self._prepare_table(len(specs))
        host_by_key: dict = {}
        for spec in specs:
            host = "github.com"
            try:
                rec = self.db.get_repo(spec.owner, spec.repo, None)
                if rec and rec.get("host"):
                    host = rec["host"]
            except Exception:
                pass
            host_by_key[f"{spec.owner}/{spec.repo}"] = host
        # 表格预填行（engine 去重后实际索引与表格行号可能不对齐，
        # 表格行只用于展示：先用完整列表填行，engine 结果按 index 回填）
        self._pending_count = len(specs)
        for i, spec in enumerate(specs):
            self._add_table_row(i, spec)
            self.row_specs[i] = spec
            self.flags[i] = CancelFlag()

        fetch_depth = depth if shallow else 0
        launched = self.engine.launch(
            specs,
            host_by_key=host_by_key,
            fetch_depth=fetch_depth,
            unshallow=getattr(self.settings, "fetch_unshallow", False),
            clear_input=clear_input,
            check_existing=True,
        )
        if launched == 0:
            self.busy = False
            self._reset_buttons()
            self._on_engine_finished()
        self.statusBar().showMessage(f"并行同步中：{len(specs)} 个仓库…")

    # ------------------------------------------------------------ 槽
    def _on_engine_line(self, index, text, level):
        self._emit_log(_fmt_dt(), LogLevel(level or "info"),
                       f"[{index}] {text}" if index is not None else text)

    def _prepare_table(self, n):
        self.table.setRowCount(0)
        self.table.setRowCount(n)

    def _add_table_row(self, index, spec: RepoSpec):
        name_item = QTableWidgetItem(spec.folder_name)
        name_item.setToolTip(spec.url_https)
        self.table.setItem(index, 0, name_item)
        prog = QProgressBar()
        prog.setRange(0, 0)
        self.table.setCellWidget(index, 1, prog)
        status_item = QTableWidgetItem("等待中")
        status_item.setForeground(QColor(PALETTE["text_dim"]))
        self.table.setItem(index, 2, status_item)
        act_item = QTableWidgetItem("—")
        act_item.setForeground(QColor(PALETTE["text_dim"]))
        self.table.setItem(index, 3, act_item)
        msg_item = QTableWidgetItem("—")
        msg_item.setForeground(QColor(PALETTE["text_dim"]))
        self.table.setItem(index, 4, msg_item)

    @pyqtSlot(int, str)
    def _on_worker_progress(self, index, percent):
        if not (0 <= index < self.table.rowCount()):
            return
        bar = self.table.cellWidget(index, 1)
        if isinstance(bar, QProgressBar) and percent:
            try:
                bar.setRange(0, 100)
                bar.setValue(int(percent))
            except Exception:
                pass

    @pyqtSlot(int, SyncResult)
    def _on_worker_result(self, index, res: SyncResult):
        bar = self.table.cellWidget(index, 1)
        if isinstance(bar, QProgressBar):
            bar.setRange(0, 1)
            bar.setValue(1)
        status_map = {
            SyncStatus.SUCCESS: (PALETTE["accent2"], "成功"),
            SyncStatus.FAILED: (PALETTE["error"], "失败"),
            SyncStatus.CANCELLED: (PALETTE["warning"], "已取消"),
            SyncStatus.CONFLICT: (PALETTE["warning"], "冲突"),
            SyncStatus.SKIPPED: (PALETTE["text_dim"], "跳过"),
        }
        color, label = status_map.get(res.status, (PALETTE["text"], str(res.status.value)))
        st = self.table.item(index, 2)
        st.setText(label)
        st.setForeground(QColor(color))
        act = self.table.item(index, 3)
        action_label = {
            "cloned": "新建克隆",
            "updated": f"增量 +{res.commits}",
            "fetched": "已最新",
            "empty": "空仓库",
            "skipped": "跳过",
            "conflict": "冲突保留",
            "cancelled": "取消",
            "failed": "失败",
        }.get(res.action.value, res.action.value)
        act.setText(action_label)
        act.setForeground(QColor(color))
        msg = self.table.item(index, 4)
        msg.setText(res.message)
        msg.setToolTip(res.detail or "")
        # 记录到 DB 由 worker 内部完成
        self._emit_log(_fmt_dt(), LogLevel.INFO if res.status == SyncStatus.SUCCESS else LogLevel.WARN,
                        f"[{index}] {label}：{res.message}（{action_label}）")

    # ------------------------------------------------------------ 槽
    @pyqtSlot()
    def _on_engine_finished(self):
        if not self.busy:
            return
        done = 0
        ok = fail = conflict = 0
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 2)
            if not it:
                continue
            t = it.text()
            if t in ("成功", "失败", "已取消", "冲突", "跳过"):
                done += 1
                if t == "成功":
                    ok += 1
                elif t == "冲突":
                    conflict += 1
                elif t == "失败":
                    fail += 1
        self.busy = False
        self._reset_buttons()
        self.statusBar().showMessage(
            f"完成：成功 {ok} · 冲突 {conflict} · 失败 {fail}")
        completed = f"成功 {ok} · 冲突 {conflict} · 失败 {fail}"
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, f"全部任务结束：{completed}")
        # 下载完成自动清空输入框
        if getattr(self, "_clear_after_finish", False):
            self.repo_input.clear()
            self._emit_log(_fmt_dt(), LogLevel.SYSTEM, "下载完成，已自动清空输入框。")
        self._load_db_into_grid()
        # 全部完成系统通知（托盘存在时）
        try:
            if getattr(self, "tray", None) is not None:
                self.tray.notify(
                    "全部任务完成",
                    f"成功 {ok} · 冲突 {conflict} · 失败 {fail}")
        except Exception:
            pass
        # 多根目录继任：还有下一组则继续（QTimer 调度避免嵌套重入）
        if getattr(self, "_multi_root_relay", False) and self._multi_root_update:
            QTimer.singleShot(0, self._update_next_root)
        else:
            self._multi_root_relay = False

    def _reset_buttons(self):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_update_all.setEnabled(True)
        self.btn_cancel_manage.setEnabled(False)
        self.repo_input.setEnabled(True)
        self.mode_combo.setEnabled(True)
        self.depth_spin.setEnabled(self.mode_combo.currentIndex() == 1)

    def cancel_all(self):
        if hasattr(self, "engine") and self.engine is not None:
            self.engine.cancel_all()
        self._emit_log(_fmt_dt(), LogLevel.WARN, "已请求取消全部任务…")
        self.statusBar().showMessage("正在取消…")
        # 由结果槽统一复位

    def _any_cancel(self) -> bool:
        if hasattr(self, "engine") and self.engine is not None:
            return self.engine.is_cancelled()
        return any(f() for f in getattr(self, "flags", {}).values())

    # ------------------------------------------------------------ 管理页
    def _load_db_into_grid(self):
        """从 DB 加载仓库列表到模型（全 host，含本地导入仓库；批量懒渲染）。"""
        self.manage_model.set_rows(self.db.list_repos())
        self.manage_stats.setText(f"共 {self.manage_model.rowCount()} 个仓库")

    def _refresh_manage(self):
        self._load_db_into_grid()

    def _on_manage_double_clicked(self, index):
        row = index.row()
        r = self.manage_model.row_at(row)
        if r is not None:
            self.show_history(r.repo_id)

    def _on_hist_btn(self):
        """共享按钮：对当前选中的行打开历史。"""
        idx = self.manage_table.selectionModel().selectedRows()
        if not idx:
            return
        for i in idx:
            r = self.manage_model.row_at(i.row())
            if r is not None:
                self.show_history(r.repo_id)

    def import_local_repos(self):
        """打开「导入本地已有仓库」对话框。"""
        if self.busy:
            QMessageBox.information(self, "提示", "有任务正在运行，请稍后再导入。")
            return
        from .local_repos_dialog import LocalReposDialog
        # 默认扫描范围：当前克隆根目录 + 管理页已有仓库的父目录
        root = self.target_edit.text().strip()
        roots = [root] if root else []
        try:
            for r in self.manage_model._rows:
                p = Path(r.local_path).parent
                if p.is_dir() and str(p) not in roots:
                    roots.append(str(p))
        except Exception:
            pass
        dlg = LocalReposDialog(parent=self, root_paths=roots, db=self.db)
        dlg.exec()
        if dlg.selected:
            n = dlg.import_selected()
            self._load_db_into_grid()
            self._emit_log(_fmt_dt(), LogLevel.INFO, f"已导入 {n} 个本地仓库")
            self.statusBar().showMessage(f"已导入 {n} 个本地仓库")

    def show_history(self, repo_id: int):
        """打开仓库详情对话框；记录已不存在时回退为纯文本历史提示。"""
        repo = next((r for r in self.db.list_repos() if r.get("id") == repo_id), None)
        hist = self.db.history(repo_id, 50)
        if repo is None:
            lines = []
            for h in hist:
                lines.append(
                    f"{h['started_at']} {h['action']:10s} {h['status']:8s} "
                    f"+{h['commits']}  {h['message'][:70]}")
            QMessageBox.information(self, "同步历史", "\n".join(lines) or "仓库不存在或已删除")
            return
        RepoDetailDialog(repo=repo, history=hist, parent=self).exec()

    def delete_selected(self):
        rows = sorted({i.row() for i in self.manage_table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "提示", "请先选择要删除的记录。")
            return
        if QMessageBox.question(self, "确认删除",
                                f"将从数据库中删除 {len(rows)} 条记录（不影响已下载的仓库目录）。\n继续？") != QMessageBox.StandardButton.Yes:
            return
        repo_ids = set()
        for r in rows:
            row = self.manage_model.row_at(r)
            if row is not None:
                rec = self.db.get_repo(row.owner, row.repo, row.host)
                if rec:
                    repo_ids.add(rec["id"])
        for rid in repo_ids:
            self.db.delete_repo(rid)
        self._load_db_into_grid()
        self._emit_log(_fmt_dt(), LogLevel.INFO, f"已删除 {len(repo_ids)} 条数据库记录")

    # ------------------------------------------------------------ 关闭
    def closeEvent(self, e: QCloseEvent):
        if self.busy:
            ret = QMessageBox.question(
                self, "确认退出",
                "有任务正在运行，退出将中断（已下载内容不会丢失）。确定退出？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
            if hasattr(self, "engine") and self.engine is not None:
                self.engine.cancel_all()
        self._close_requested = True
        # flush 剩余节流日志，避免关窗瞬间最后若干行（如"全部任务结束"）丢失
        try:
            self._flush_log_batch()
        except Exception:
            pass
        # 引擎统一收尾：取消剩余任务、flush 进度、清空未启动队列
        try:
            if hasattr(self, "engine") and self.engine is not None:
                self.engine.shutdown()
        except Exception:
            pass
        try:
            self.settings_store.save(self.settings)
            self.db.close()
        except Exception:
            pass
        try:
            if getattr(self, "tray", None) is not None and self.tray.tray is not None:
                self.tray.tray.hide()
        except Exception:
            pass
        e.accept()