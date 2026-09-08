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
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..app.url_lib import parse_repo_url, parse_urls
from ..app.worker import CancelFlag, CloneWorker, TaskPayload, WorkerSignals
from ..db.repo_db import Database, load_progress
from ..db.settings import Settings, SettingsStore
from ..git.service import GitService
from ..models import RepoSpec, SyncResult, SyncStatus
from .theme import LogEvent, LogLevel, LogModel, PALETTE, QSS, make_highlighter

DOMAIN = "github.com"


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

        # 自建线程池，不碰全局实例；并发数来自持久化设置
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(max(1, min(16, int(self.settings.concurrency))))

        self.tasks: list = []               # list[CancelFlag]
        self.flags: dict = {}               # index -> CancelFlag
        self.row_specs: dict = {}           # index -> RepoSpec
        self.busy = False
        self._close_requested = False

        # 日志模型
        self.log = LogModel(max_entries=3000)
        self.log.appended.connect(self._on_log_appended)

        # git 服务（回调直接转发到 log；超时/重试/代理来自设置）
        self.service = GitService(
            self.data_dir / "clones",
            on_line=lambda c: self.log.append(_fmt_dt(), LogLevel.INFO, f"[{c.text}]"),
            cancelled=lambda: self._any_cancel(),
            fetch_timeout=self.settings.fetch_timeout,
            clone_timeout=self.settings.clone_timeout,
            retries=self.settings.retries,
            proxy=self.settings.proxy,
        )

        self._build_ui()
        self.setStyleSheet(QSS)
        self.log.append(_fmt_dt(), LogLevel.SYSTEM, f"Git-clone-Max 启动，数据目录：{self.data_dir}")
        self.log.append(_fmt_dt(), LogLevel.SYSTEM, f"并行线程：{self.pool.maxThreadCount()}")
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
        self.thread_lbl = QLabel(f"并行线程 {self.pool.maxThreadCount()}")
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
        self.btn_refresh.clicked.connect(self._load_db_into_grid)
        self.btn_delete = QPushButton("🗑 删除选中记录")
        self.btn_delete.clicked.connect(self.delete_selected)
        top.addWidget(self.btn_update_all)
        top.addWidget(self.btn_cancel_manage)
        top.addWidget(self.btn_refresh)
        top.addWidget(self.btn_delete)
        v.addLayout(top)

        self.manage_table = QTableWidget(0, 6)
        self.manage_table.setHorizontalHeaderLabels(
            ["文件夹", "仓库", "路径", "最近同步", "HEAD", "历史"])
        for col in range(6):
            self.manage_table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch)
        self.manage_table.verticalHeader().setVisible(False)
        self.manage_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.manage_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.manage_table.setMinimumHeight(260)
        v.addWidget(self.manage_table, 3)

        self.manage_desc = QLabel(
            "「一键更新全部」会按 作者__仓库 命名找到每个仓库目录，"
            "已存在则 git fetch 增量更新；本地有改动冲突时保留本地、标记冲突，绝不覆盖。")
        self.manage_desc.setObjectName("muted")
        self.manage_desc.setWordWrap(True)
        v.addWidget(self.manage_desc)

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
        self.spin_concurrency.setRange(1, 16)
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
    @pyqtSlot(int)
    def _on_log_appended(self, count):
        self.log_count.setText(f"{count} 条")
        if hasattr(self, "log_view") and self.log_view and self.log._entries:
            ev = self.log._entries[-1]
            prefix = {
                LogLevel.WARN: "[警告] ",
                LogLevel.ERROR: "[错误] ",
                LogLevel.SYSTEM: "[系统] ",
            }.get(ev.level, "")
            self.log_view.appendPlainText(f"[{ev.time}] {prefix}{ev.text}")
            # 只保留尾部，滚动跟随
            sb = self.log_view.verticalScrollBar()
            if sb.value() >= sb.maximum() - 40:
                self.log_view.verticalScrollBar().setValue(
                    self.log_view.verticalScrollBar().maximum())

    def clear_log(self):
        self.log.clear()
        self.log_view.clear()

    # ------------------------------------------------------------ 设置持久化
    def _save_concurrency(self, value):
        self.settings.concurrency = int(value)
        if self.pool is not None:
            self.pool.setMaxThreadCount(int(value))
        self.thread_lbl.setText(f"并行线程 {self.pool.maxThreadCount()}")
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
            self.log.append(_fmt_dt(), LogLevel.INFO, f"日志已导出：{path}")
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
            self.log.append(_fmt_dt(), LogLevel.WARN,
                            f"已忽略 {len(invalid)} 行无效地址：{', '.join(invalid)}")
        target = self.target_edit.text().strip()
        if not target:
            QMessageBox.warning(self, "提示", "请先选择下载目录。")
            return
        Path(target).mkdir(parents=True, exist_ok=True)

        shallow = self.mode_combo.currentIndex() == 1
        depth = self.depth_spin.value()
        # 下载完成后自动清空输入框（用户要求）
        self._launch(specs, target_root=Path(target), shallow=shallow, depth=depth,
                     clear_input=True)

    def update_all(self):
        if self.busy:
            QMessageBox.information(self, "提示", "有任务正在运行，请先取消或等待完成。")
            return
        rows = self.db.list_repos(DOMAIN)
        if not rows:
            QMessageBox.information(self, "提示", "数据库中没有已记录的仓库，请先到「下载中心」添加。")
            return
        # 按 local_path 反推根目录集合：仓库可能分散在多个下载目录
        from collections import OrderedDict
        groups: "OrderedDict[str, list]" = OrderedDict()
        for r in rows:
            root = str(Path(r["local_path"]).parent)
            groups.setdefault(root, []).append(r)
        if len(groups) == 1:
            root = next(iter(groups))
            specs = [RepoSpec(owner=r["owner"], repo=r["repo"],
                              url_https=r["url"], folder_name=r["folder_name"])
                     for r in groups[root]]
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
        specs = [RepoSpec(owner=r["owner"], repo=r["repo"],
                          url_https=r["url"], folder_name=r["folder_name"])
                 for r in rows]
        self.log.append(_fmt_dt(), LogLevel.SYSTEM, f"一键更新：处理根目录 {root}（{len(specs)} 个仓库）")
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

        self.log.append(_fmt_dt(), LogLevel.SYSTEM,
                        f"开始并行同步 {len(specs)} 个仓库（{'浅克隆 depth=' + str(depth) if shallow else '满量'}）")
        self.log.append(_fmt_dt(), LogLevel.SYSTEM, f"根目录：{target_root}")

        # 下载完成后自动清空输入框（默认开启）
        self._clear_after_finish = clear_input and not multi_root_relay
        self._multi_root_relay = multi_root_relay

        # 重建 service（根目录可变；超时/重试/代理来自设置）
        self.service = GitService(
            target_root,
            on_line=lambda c: self.log.append(_fmt_dt(), LogLevel.INFO, f"[{c.text}]"),
            cancelled=lambda: self._any_cancel(),
            fetch_timeout=self.settings.fetch_timeout,
            clone_timeout=self.settings.clone_timeout,
            retries=self.settings.retries,
            proxy=self.settings.proxy,
        )
        self._prepare_table(len(specs))
        self._pending_count = len(specs)
        for i, spec in enumerate(specs):
            flag = CancelFlag()
            self.flags[i] = flag
            self.row_specs[i] = spec
            self._add_table_row(i, spec)
            payload = TaskPayload(spec=spec, flag=flag)
            worker = CloneWorker(i, payload, self.service, self.db,
                                 str(self.progress_path),
                                 on_line=lambda c, i=i: self.log.append(
                                     _fmt_dt(), LogLevel.INFO, f"[{i}] {c.text}"))
            worker.signals.line.connect(self._on_worker_line)
            worker.signals.progress.connect(self._on_worker_progress)
            worker.signals.result.connect(self._on_worker_result)
            worker.signals.finished.connect(self._on_worker_finished)
            self.tasks.append(worker)
            self.pool.start(worker)
        self.statusBar().showMessage(f"并行同步中：{len(specs)} 个仓库…")

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

    # ------------------------------------------------------------ 槽
    @pyqtSlot(int, str, str)
    def _on_worker_line(self, index, text, level):
        self.log.append(_fmt_dt(), LogLevel(level or "info"), f"[{index}] {text}")

    @pyqtSlot(int, str)
    def _on_worker_progress(self, index, percent):
        root = self.flags.get(index)
        if root is None:
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
        self.log.append(_fmt_dt(), LogLevel.INFO if res.status == SyncStatus.SUCCESS else LogLevel.WARN,
                        f"[{index}] {label}：{res.message}（{action_label}）")

    @pyqtSlot()
    def _on_worker_finished(self):
        if not self.busy:
            return
        # 显式完成计数（不依赖表格状态文本）：每来一个 finished 信号减一
        self._pending_count = max(0, getattr(self, "_pending_count", 0) - 1)
        if self._pending_count > 0:
            return  # 还有 worker 在跑，不提前复位
        done = 0
        ok = fail = conflict = 0
        for i in range(self.table.rowCount()):
            it = self.table.item(i, 2)
            if not it:
                continue
            t = it.text()
            if t in ("成功", "失败", "已取消", "冲突"):
                done += 1
                if t == "成功":
                    ok += 1
                elif t == "冲突":
                    conflict += 1
                elif t == "失败":
                    fail += 1
        if done >= self.table.rowCount() and self.table.rowCount() > 0:
            self.busy = False
            self.btn_start.setEnabled(True)
            self.btn_cancel.setEnabled(False)
            self.btn_update_all.setEnabled(True)
            self.btn_cancel_manage.setEnabled(False)
            self.repo_input.setEnabled(True)
            self.mode_combo.setEnabled(True)
            self.depth_spin.setEnabled(self.mode_combo.currentIndex() == 1)
            self.statusBar().showMessage(
                f"完成：成功 {ok} · 冲突 {conflict} · 失败 {fail}")
            completed = f"成功 {ok} · 冲突 {conflict} · 失败 {fail}"
            self.log.append(_fmt_dt(), LogLevel.SYSTEM, f"全部任务结束：{completed}")
            # 下载完成自动清空输入框
            if getattr(self, "_clear_after_finish", False):
                self.repo_input.clear()
                self.log.append(_fmt_dt(), LogLevel.SYSTEM, "下载完成，已自动清空输入框。")
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

    def cancel_all(self):
        for flag in self.flags.values():
            flag.cancel()
        self.log.append(_fmt_dt(), LogLevel.WARN, "已请求取消全部任务…")
        self.statusBar().showMessage("正在取消…")
        # 由结果槽统一复位

    def _any_cancel(self) -> bool:
        return any(f() for f in self.flags.values())

    # ------------------------------------------------------------ 管理页
    def _load_db_into_grid(self):
        rows = self.db.list_repos(DOMAIN)
        self.manage_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [r["folder_name"], f"{r['owner']}/{r['repo']}", r["local_path"],
                    (r["last_sync_at"] or "")[:19], (r["head_sha"] or "")[:8], ""]
            for c, v in enumerate(vals):
                item = QTableWidgetItem(v)
                if c == 0:
                    item.setToolTip(r["url"])
                self.manage_table.setItem(i, c, item)
            # 历史链接按钮
            btn = QPushButton("查看")
            btn.setStyleSheet("padding: 2px 8px; font-size: 12px;")
            rid = r["id"]
            btn.clicked.connect(lambda _=False, rid=rid: self.show_history(rid))
            self.manage_table.setCellWidget(i, 5, btn)
        self.manage_stats.setText(f"共 {len(rows)} 个仓库")

    def show_history(self, repo_id: int):
        hist = self.db.history(repo_id, 20)
        lines = []
        for h in hist:
            lines.append(
                f"{h['started_at']} {h['action']:10s} {h['status']:8s} "
                f"+{h['commits']}  {h['message'][:70]}")
        QMessageBox.information(self, "同步历史", "\n".join(lines) or "暂无记录")

    def delete_selected(self):
        rows = sorted({i.row() for i in self.manage_table.selectedItems()})
        if not rows:
            QMessageBox.information(self, "提示", "请先选择要删除的记录。")
            return
        if QMessageBox.question(self, "确认删除",
                                f"将从数据库中删除 {len(rows)} 条记录（不影响已下载的仓库目录）。\n继续？") != QMessageBox.StandardButton.Yes:
            return
        repo_ids = set()
        for r in rows:
            item = self.manage_table.item(r, 1)
            if item:
                owner_repo = item.text()
                o, _, rr = owner_repo.partition("/")
                rec = self.db.get_repo(o, rr)
                if rec:
                    repo_ids.add(rec["id"])
        for rid in repo_ids:
            self.db.delete_repo(rid)
        self._load_db_into_grid()
        self.log.append(_fmt_dt(), LogLevel.INFO, f"已删除 {len(repo_ids)} 条数据库记录")

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
            for flag in self.flags.values():
                flag.cancel()
        self._close_requested = True
        # 取消剩余任务并清空未启动队列，避免退出后留下孤儿 git 进程
        for flag in self.flags.values():
            flag.cancel()
        try:
            if self.pool is not None:
                self.pool.clear()
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