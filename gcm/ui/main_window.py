"""主窗口：三个 Tab（下载中心 / 仓库管理 / 设置 与 黑匣子日志）。"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QCloseEvent, QColor
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,  # noqa: F401  — 测试 mock 引用（download_tools 经本模块访问）
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..app.engine import SyncEngine
from ..app.url_lib import parse_urls
from ..app.worker import CancelFlag
from ..db.repo_db import Database
from ..db.settings import SettingsStore
from ..models import RepoSpec, SyncResult, SyncStatus
from .log_buffer import LogBuffer
from .repo_detail_dialog import RepoDetailDialog
from .theme import PALETTE, LogLevel, LogModel

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
        self.engine.progress_detail.connect(self._on_worker_progress_detail)
        self.engine.result.connect(self._on_worker_result)
        self.engine.finished.connect(self._on_engine_finished)
        # 兼容旧引用（测试/托盘可能读 pool）
        self.pool = self.engine.pool

        self.tasks: list = []               # list[SyncEngine] 兼容引用（实际由 engine 管理）
        self.flags: dict = {}               # index -> CancelFlag（引擎接管后仅兼容辅助）
        self.row_specs: dict = {}           # index -> RepoSpec
        self.busy = False
        self._close_requested = False
        # G33-6 启动耗时记录（构造结束写回；自检时展示，>3s 提示较慢）
        self._startup_start = time.perf_counter()
        self._startup_ms = 0

        # 高频进度详情节流：速率/对象数批量刷新（32 并发时不阻塞主线程）
        self._detail_batch: dict = {}
        self._detail_timer = QTimer(self)
        self._detail_timer.setInterval(300)
        self._detail_timer.timeout.connect(self._flush_detail_batch)
        # 日志模型
        self.log = LogModel(max_entries=3000)
        self.log.appended.connect(self._on_log_appended)

        # 日志洪峰节流（G33-8 拆分）：git 高输出时合并为一次批量刷新，
        # 由 LogBuffer 定时器驱动；_log_batch/_log_batch_timer 保持兼容引用。
        self._log_buffer: LogBuffer | None = None
        self._log_batch: list = []
        self._log_batch_timer: QTimer | None = None

        self._build_ui()
        # 首次日志写入前先挂 LogBuffer（此时 log_view/log_count 已存在）
        self._ensure_log_buffer()

        # G05-1 应用持久化主题（默认 deep）+ G10-1 字号缩放
        from ..ui import theme as _th
        _th.apply_theme(getattr(self.settings, "theme", "deep"))
        self.setStyleSheet(_th.qss_for_scale(getattr(self.settings, "font_scale", 1.0)))
        # G10-1 快捷键 Ctrl+= 放大 / Ctrl+- 缩小 / Ctrl+0 复位
        from PyQt6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence("Ctrl+="), self, activated=self.zoom_in)
        QShortcut(QKeySequence("Ctrl+-"), self, activated=self.zoom_out)
        QShortcut(QKeySequence("Ctrl+0"), self, activated=self.zoom_reset)
        # G36-8 全局热键：Ctrl+Alt+S 开始/取消、Ctrl+Alt+U 一键更新、Ctrl+Alt+M 显示窗口
        QShortcut(QKeySequence("Ctrl+Alt+S"), self, activated=self._hotkey_start_cancel)
        QShortcut(QKeySequence("Ctrl+Alt+U"), self, activated=self.update_all)
        QShortcut(QKeySequence("Ctrl+Alt+M"), self, activated=self._hotkey_show_window)
        # G02-4 URL 历史：同步成功的地址自动留档（data/history.json）
        from ..db.history import UrlHistory
        self.url_history = UrlHistory(self.data_dir / "history.json")
        # G35-4 任务清单：data/lists/*.json（保存/载入输入区地址）
        from ..db.lists import TaskLists
        self.task_lists = TaskLists(self.data_dir / "lists")
        # G09-1 剪贴板监听（设置开启时启动）
        from ..app.clipboard_watcher import ClipboardWatcher
        self.clipboard_watcher = ClipboardWatcher(
            parent=self, enabled=bool(getattr(self.settings, "clipboard_watch", False)),
            on_url=self._on_clipboard_url)
        if getattr(self.settings, "clipboard_watch", False):
            self.clipboard_watcher.start()
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, f"Git-clone-Max 启动，数据目录：{self.data_dir}")
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, f"并行线程：{self.engine.concurrency}（上限 {MAX_CONCURRENCY}）")
        self._load_db_into_grid()
        # G22-2 崩溃恢复自检：上次会话有未完成/失败项 → 提示并回填输入区供一键续跑
        try:
            self._startup_recovery_prefill()
        except Exception:
            pass

        # 系统托盘（失败静默降级，不影响主流程）
        from .tray import TrayController
        self.tray = TrayController(parent=self)
        if self.settings.minimize_to_tray:
            self.tray.install()

        # 启动后静默检查更新：不阻塞、不弹窗，有新版本仅写日志 + 托盘提示
        QTimer.singleShot(2500, self._check_update_silent)  # G21-3 启动后静默检查更新
        # G33-6 记录本次主窗口启动耗时（含 UI 构建；自检时展示，>3s 提示较慢）
        self._startup_ms = int((time.perf_counter() - self._startup_start) * 1000)
        # G36-1/G36-9：show() 后单次调度——首次运行向导 + 系统深浅色自适应
        QTimer.singleShot(300, self._maybe_show_onboarding)
        QTimer.singleShot(500, self._maybe_apply_system_theme)
        # G37-4 自动更新定时器（分钟级；engine 非 busy 时触发一键更新）
        self._auto_update_timer = QTimer(self)
        self._auto_update_timer.timeout.connect(self._on_auto_update_tick)
        self._restart_auto_update_timer()

    def _restart_auto_update_timer(self):
        """按 settings.auto_update_minutes 重启自动更新定时器（0=关）。"""
        try:
            self._auto_update_timer.stop()
            mins = int(getattr(self.settings, "auto_update_minutes", 0) or 0)
            if mins > 0:
                self._auto_update_timer.start(mins * 60 * 1000)
        except Exception:
            pass

    def _on_auto_update_tick(self):
        """G37-4 定时触发：engine 非 busy 时一键更新全部（busy 跳过顺延下轮）。"""
        if self.busy:
            self._emit_log(_fmt_dt(), LogLevel.INFO, "自动更新跳过：任务运行中")
            return
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM,
                       f"自动更新触发（每 {self.settings.auto_update_minutes} 分钟）")
        try:
            self.update_all()
        except Exception as e:
            self._emit_log(_fmt_dt(), LogLevel.WARN, f"自动更新失败：{e}")

    # ------------------------------------------------------------ G36-1 首次运行向导
    def _maybe_show_onboarding(self):
        """首次运行（settings.first_run_done 未置位）→ 显示三步向导。

        跳过或完成均写 first_run_done；目录/模式回填设置。仅真实窗口展示
        （offscreen/测试环境自动跳过，避免模态阻塞）。
        """
        if getattr(self.settings, "first_run_done", False):
            return
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        try:
            from .onboarding import OnboardingDialog
            dlg = OnboardingDialog(parent=self,
                                   default_dir=str(self.data_dir / "clones"))
            dlg.exec()
            self.settings.first_run_done = True
            if getattr(dlg, "selected_dir", ""):
                self.target_edit.setText(dlg.selected_dir)
                self.settings.download_dir = dlg.selected_dir
            mode = getattr(dlg, "selected_mode", "") or ""
            if mode.startswith("浅"):
                self.mode_combo.setCurrentIndex(1)
            self.settings_store.save(self.settings)
        except Exception as e:
            self._emit_log(_fmt_dt(), LogLevel.WARN,
                           f"首次运行向导未完成：{e}")

    # ------------------------------------------------------------ G36-9 跟随系统深浅色
    def _maybe_apply_system_theme(self):
        """跟随 Windows 系统深浅色：仅在用户设置 theme=auto 时自动切换。

        说明：默认主题手动（deep/light/nord），用户显式选 auto 才读取注册表
        并应用 light/deep；读取失败无副作用，保持现状。
        """
        try:
            if getattr(self.settings, "theme", "deep") != "auto":
                return
            from ..app.system_theme import detect_system_light_theme, map_system_to_theme
            pref = detect_system_light_theme()
            target = map_system_to_theme(pref)
            if target and target in ("light", "deep"):
                from ..ui import theme as _th
                _th.apply_theme(target)
                self.setStyleSheet(
                    _th.qss_for_scale(getattr(self.settings, "font_scale", 1.0)))
                self._apply_theme_to_children()
                self.settings.theme = target
                self._emit_log(_fmt_dt(), LogLevel.INFO,
                               f"已按系统深浅色切换主题：{target}")
        except Exception:
            pass

    # ------------------------------------------------------------ G22-2 崩溃恢复
    def _startup_recovery_prefill(self):
        """启动时检查 progress.json：in_progress 非空（上次中断）或 failed 非空

        （上次有失败项）→ 日志提示 + 输入区回填待恢复地址（用户可一键重跑）。
        只回填不自动启动，避免静默网络操作。
        """
        from ..db.repo_db import load_progress
        prog = load_progress(self.progress_path)
        pending_keys = list((prog.get("in_progress") or {}).keys())
        failed_keys = [x.get("key", "") for x in (prog.get("failed") or [])]
        recover = [k for k in pending_keys if k and k not in failed_keys]
        lines = []
        if recover:
            lines.extend(recover)
            self._emit_log(_fmt_dt(), LogLevel.WARN,
                           f"上次会话有 {len(recover)} 项未完成，已回填输入区，可直接继续。")
        if failed_keys:
            extra = [k for k in failed_keys if k and k not in lines]
            if extra:
                lines.extend(extra)
                self._emit_log(_fmt_dt(), LogLevel.WARN,
                               f"上次有 {len(failed_keys)} 项失败，已一并回填（可重试）。")
        if lines:
            from ..app.url_lib import parse_any_repo_url
            urls = []
            for k in lines:
                spec = parse_any_repo_url(k)
                if spec is not None:
                    urls.append(spec.url_https)
            if urls:
                self.repo_input.setPlainText("\n".join(urls))

    # --------------------------------------------------------- G36-3 统一图标
    @staticmethod
    def _std_icon(name: str, widget=None):
        """取语义图标（QStyle.StandardPixmap）；无映射返回 None（保留 emoji 文本回退）。"""
        try:
            from .theme import std_icon
            return std_icon(name, widget)
        except Exception:
            return None

    # ------------------------------------------------------------ UI

    def _serialize_geometry(self) -> str:
        """G36-4 当前窗口几何 → "WxH+X+Y"（含最大化状态前缀 M:）。"""
        try:
            if self.isMaximized():
                return f"M:{self.normalGeometry().width()}x{self.normalGeometry().height()}"
            g = self.geometry()
            return f"{g.width()}x{g.height()}+{g.x()}+{g.y()}"
        except Exception:
            return ""

    def _restore_geometry(self) -> None:
        """G36-4 从 settings.geometry 恢复窗口几何（尺寸/位置/最大化）。"""
        raw = str(getattr(self.settings, "geometry", "") or "")
        if not raw:
            return
        try:
            maximized = raw.startswith("M:")
            if maximized:
                raw = raw[2:]
            w, h, x, y = (int(v) for v in raw.replace("x", " ").replace("+", " ").split())
            # 防小屏溢出：只接受最小尺寸以上
            w = max(w, 1020)
            h = max(h, 700)
            self.resize(w, h)
            self.move(x, y)
            if maximized:
                self.showMaximized()
        except Exception:
            pass  # 几何串损坏 → 保持默认

    def _build_ui(self):
        self.setWindowTitle("Git-clone-Max — GitHub 仓库批量并行下载")
        self.resize(1240, 840)
        self.setMinimumSize(1020, 700)
        # G36-4 恢复上次窗口几何（尺寸+位置）；无记录则保持默认
        try:
            self._restore_geometry()
        except Exception:
            pass

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
        # G36-5 首次显示空态 overlay（表初始为空）
        try:
            self._update_empty_download()
            ov = getattr(self, "_empty_manage", None)
            if ov is not None and self.manage_model.rowCount() == 0:
                ov.setVisible(True)
                ov.setGeometry(self.manage_table.rect())
                ov.raise_()
        except Exception:
            pass

    # ---------------- 下载中心
    def _build_download_tab(self):
        v = QVBoxLayout(self.tab_download)
        v.setSpacing(10)

        box = QGroupBox("仓库地址（每行一个）")
        b = QVBoxLayout(box)
        self.repo_input = QPlainTextEdit()
        self.repo_input.setPlaceholderText(
            "每行一个仓库地址，例如：\n"
            "https://github.com/vercel-labs/skills\n"
            "git@github.com:microsoft/azure-skills.git\n"
            "vercel-labs/agent-skills\n"
            "https://gitlab.com/grp/repo@v1.2.0  （@后指定分支/标签）\n"
            "（地址中的 作者/仓库名 将自动作为文件夹名：作者__仓库）"
        )
        self.repo_input.setMinimumHeight(150)
        b.addWidget(self.repo_input)
        # G02-4 右键菜单：从最近历史回填 / 清空历史
        self.repo_input.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.repo_input.customContextMenuRequested.connect(self._repo_input_menu)
        # G35-3 拖拽导入：.txt/.csv → 读入每行地址；目录 → 本地扫描检测
        self.repo_input.setAcceptDrops(True)
        self.repo_input.dragEnterEvent = self._repo_input_drag_enter
        self.repo_input.dropEvent = self._repo_input_drop
        quick = QHBoxLayout()
        quick.addWidget(QLabel("快捷填充："))
        # G35-7 快捷填充可配置：跟随 settings.quick_repos（持久化，可编辑）
        for repo in getattr(self.settings, "quick_repos", None) or ():
            btn = QPushButton(repo)
            btn.clicked.connect(lambda _=False, r=repo: self.repo_input.appendPlainText(
                f"https://github.com/{r}"))
            quick.addWidget(btn)
        quick.addStretch()
        # G35-4 任务清单：保存当前输入区为命名清单 / 下拉载入（分享用 data/lists/*.json）
        self.btn_save_list = QPushButton("💾 保存清单")
        self.btn_save_list.setToolTip("把当前输入区的地址保存为命名任务清单（data/lists/*.json）")
        self.btn_save_list.clicked.connect(self._save_task_list)
        self.combo_load_list = QComboBox()
        self.combo_load_list.setToolTip("选择已保存的清单一键载入")
        self.combo_load_list.setMinimumWidth(180)
        self.btn_load_list = QPushButton("载入")
        self.btn_load_list.setToolTip("把所选清单的地址填入输入区")
        self.btn_load_list.clicked.connect(self._load_task_list)
        quick.addWidget(self.btn_save_list)
        quick.addWidget(self.combo_load_list)
        quick.addWidget(self.btn_load_list)
        b.addLayout(quick)
        v.addWidget(box)

        opts = QHBoxLayout()
        g1 = QGroupBox("下载位置")
        l1 = QHBoxLayout(g1)
        # 恢复上次选择（settings.download_dir），否则默认 data/clones
        default_dir = str(self.settings.download_dir or (self.data_dir / "clones"))
        self.target_edit = QLineEdit(default_dir)
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
        self.btn_start = QPushButton("开始并行下载 / 更新")
        self.btn_start.setObjectName("primary")
        self.btn_start.setMinimumHeight(38)
        self.btn_start.clicked.connect(self.start_all)
        self.btn_start.setIcon(self._std_icon("play", self.btn_start))
        self.btn_cancel = QPushButton("取消全部")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_all)
        self.btn_cancel.setIcon(self._std_icon("stop", self.btn_cancel))
        self.btn_clear = QPushButton("清空列表")
        self.btn_clear.clicked.connect(self.repo_input.clear)
        self.btn_open = QPushButton("打开克隆目录")
        self.btn_open.clicked.connect(self.open_target)
        self.btn_open.setIcon(self._std_icon("open_dir", self.btn_open))
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
        # G02-2 表头点击排序（状态列优先级：运行>等待>成功>冲突>失败>取消>跳过）
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().sortIndicatorChanged.connect(self._on_sort_changed)
        # G35-1 进度表右键菜单：重试此仓库 / 复制错误详情 / 打开所在目录
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._progress_table_menu)
        v.addWidget(self.table, 3)
        # G36-5 下载中心空态引导：overlay 标签（绝对定位在表格上，有数据时隐藏）
        self._empty_download = QLabel("⬇ 粘贴仓库地址开始下载\n（支持多行 / 拖入 txt 文件）")
        self._empty_download.setObjectName("muted")
        self._empty_download.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_download.setWordWrap(True)
        self._empty_download.setVisible(False)
        self._empty_download.setParent(self.table)
        self._empty_download.raise_()

        # G02-1 搜索过滤框（防抖 200ms）+ 进度表右键「暂停此项」（G02-3）
        row_tools = QHBoxLayout()
        row_tools.addWidget(QLabel("搜索："))
        self.progress_filter = QLineEdit()
        self.progress_filter.setPlaceholderText("按仓库名过滤…")
        self.progress_filter.setClearButtonEnabled(True)
        self.progress_filter.textChanged.connect(self._schedule_progress_filter)
        row_tools.addWidget(self.progress_filter, 1)
        btn_pause_selected = QPushButton("⏸ 暂停选中")
        btn_pause_selected.clicked.connect(self.pause_selected)
        row_tools.addWidget(btn_pause_selected)
        row_tools.addStretch()
        v.addLayout(row_tools)

    # ---------------- 仓库管理
    def _build_manage_tab(self):
        v = QVBoxLayout(self.tab_manage)
        top = QHBoxLayout()
        self.manage_stats = QLabel("共 0 个仓库")
        self.manage_stats.setObjectName("muted")
        top.addWidget(self.manage_stats)
        top.addStretch()
        self.btn_update_all = QPushButton("一键更新全部")
        self.btn_update_all.setObjectName("primary")
        self.btn_update_all.clicked.connect(self.update_all)
        self.btn_update_all.setIcon(self._std_icon("refresh", self.btn_update_all))
        self.btn_cancel_manage = QPushButton("取消全部")
        self.btn_cancel_manage.setEnabled(False)
        self.btn_cancel_manage.clicked.connect(self.cancel_all)
        self.btn_cancel_manage.setIcon(self._std_icon("stop", self.btn_cancel_manage))
        self.btn_refresh = QPushButton("刷新列表")
        self.btn_refresh.clicked.connect(self._refresh_manage)
        self.btn_refresh.setIcon(self._std_icon("refresh", self.btn_refresh))
        self.btn_import_local = QPushButton("导入本地已有仓库")
        self.btn_import_local.clicked.connect(self.import_local_repos)
        self.btn_import_local.setIcon(self._std_icon("folder", self.btn_import_local))
        self.btn_delete = QPushButton("删除选中记录")
        self.btn_delete.clicked.connect(self.delete_selected)
        self.btn_delete.setIcon(self._std_icon("trash", self.btn_delete))
        self.btn_batch_tag = QPushButton("打标签")
        self.btn_batch_tag.setToolTip("给选中的仓库追加标签（多选批量）")
        self.btn_batch_tag.clicked.connect(self.batch_tag_selected)
        self.btn_export_selected = QPushButton("导出所选")
        self.btn_export_selected.setToolTip("把选中的仓库导出为 CSV 报表")
        self.btn_export_selected.clicked.connect(self.export_selected_csv)
        self.btn_export_selected.setIcon(self._std_icon("save", self.btn_export_selected))
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
        # G03-7 末列「查看」按钮委托：每行独立可点击（不再依赖选中行 + 共享按钮）
        from .manage_model import HistoryButtonDelegate
        self._hist_delegate = HistoryButtonDelegate(self.manage_table)
        self._hist_delegate.clicked.connect(self._on_hist_row_clicked)
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

    # ---------------- 设置与日志
    def _build_settings_tab(self):
        """G33-8 拆分：设置页控件构建委托给 settings_panel.py / log_buffer.py。

        控件名（self.spin_concurrency / self.log_view 等）仍挂载在本窗口，
        测试与既有引用零变化；此处只做布局组装。
        """
        v = QVBoxLayout(self.tab_settings)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # 设置区：所有设置分组放入可滚动区域（日志区固定在下、不被滚动带跑）
        from .settings_panel import build_log_box, build_settings_ui
        self.settings_scroll = build_settings_ui(self)
        v.addWidget(self.settings_scroll, 1)

        # ---- 黑匣子日志（实时）：固定在下、占剩余空间，日志滚动由控件自身负责
        g2 = build_log_box(self)
        v.addWidget(g2, 1)

    def show_statistics(self):
        """G07-1 打开统计中心对话框。"""
        from .statistics_dialog import StatisticsDialog
        StatisticsDialog(db=self.db, parent=self).exec()

    def export_report(self):
        """G07-3 导出 CSV/Markdown 报表（G33-8 拆分：委托 download_tools）。"""
        from .download_tools import export_report as _er
        _er(self, _fmt_dt)

    def _run_selftest(self, log_path: str | Path | None = None):
        import shutil
        import subprocess
        lines: list[str] = []
        git_path = shutil.which("git")
        if not git_path:
            lines.append("git：未检测到（请安装 Git 后重启应用）")
        else:
            lines.append(f"git：{git_path}")
            try:
                proc = subprocess.run(
                    ["git", "--version"],
                    capture_output=True, text=True, timeout=10,
                    encoding="utf-8", errors="replace")
                out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
                lines.append(f"版本：{out or '未知'}")
            except Exception as e:
                lines.append(f"版本：读取失败（{e}）")
        from PyQt6.QtCore import PYQT_VERSION_STR
        lines.append(f"PyQt6：{PYQT_VERSION_STR}")
        lines.append(f"数据目录：{self.data_dir}")
        lines.append(f"当前并发：{self.engine.concurrency}（上限 {MAX_CONCURRENCY}）")
        lines.append(f"数据目录可写：{'是' if os.access(self.data_dir, os.W_OK) else '否'}")
        # G33-6 启动耗时：构造完成即记录；>3s 标注较慢（exe 解包/低配机器场景）
        startup_ms = getattr(self, "_startup_ms", 0)
        slow_note = "（启动偏慢，可考虑使用便携版）" if startup_ms > 3000 else ""
        lines.append(f"启动耗时：{startup_ms} ms{slow_note}")
        QMessageBox.information(self, "环境自检", "\n".join(lines))
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, f"环境自检完成：{' / '.join(lines)}")
        # G33-6 结构化留痕：selftest|startup_ms=…（供长期监控启动性能回归）
        try:
            from ..app import applog
            applog.info(
                f"selftest|startup_ms={startup_ms}|concurrency={self.engine.concurrency}",
                log_path or (self.data_dir / "app.log"))
        except Exception:
            pass

    # ------------------------------------------------------------ 日志
    def _ensure_log_buffer(self):
        """G33-8 惰性挂载 LogBuffer（log_view/log_count 就绪后调用）。

        保持 _log_batch / _log_batch_timer 兼容引用，供历史测试 tearDown 与 closeEvent 访问。
        """
        if self._log_buffer is not None:
            return
        self._log_buffer = LogBuffer(parent=self, log_view=self.log_view,
                                     log_count=self.log_count)
        self._log_batch = self._log_buffer._batch
        self._log_batch_timer = self._log_buffer.timer

    def _emit_log(self, time: str, level: LogLevel, text: str):
        """统一日志入口：入模型 + 节流批量渲染（高频 git 输出不阻塞主线程）。

        G33-8 拆分：批量渲染逻辑委托 LogBuffer（log_buffer.py）。
        """
        self._ensure_log_buffer()
        self.log.append(time, level, text)
        # 节流：把待渲染条目并入批量，由 LogBuffer 定时器统一 flush
        from .theme import LogEvent as _LE
        self._log_buffer.append(_LE(time, level, text))

    def _flush_log_batch(self):
        """兼容入口：刷日志缓冲（历史测试/closeEvent 引用）。"""
        buf = getattr(self, "_log_buffer", None)
        if buf is not None:
            buf.flush()

    @pyqtSlot(int)
    def _on_log_appended(self, count):
        self.log_count.setText(f"{count} 条")

    def has_pending_logs(self) -> bool:
        """供 closeEvent 判断是否还有积压节流日志需 flush。"""
        buf = getattr(self, "_log_buffer", None)
        return bool(getattr(self, "_log_batch", None)) or (buf is not None and buf.has_pending())

    def clear_log(self):
        self.log.clear()
        # 清空节流积压，避免清空后 ≤120ms 幽灵追加回旧行
        if hasattr(self, "_log_batch"):
            self._log_batch.clear()
        if hasattr(self, "_log_buffer"):
            self._log_buffer.clear_pending()
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

    def _detect_proxy_now(self):
        """G04-3 一键自动检测系统代理并填入（可保存）。"""
        try:
            from ..app.proxy import detect_system_proxy
            val = detect_system_proxy()
            if val:
                self.edit_proxy.setText(val)
                self.settings.proxy = val
                self.settings_store.save(self.settings)
                self._emit_log(_fmt_dt(), LogLevel.INFO, f"已自动检测到代理：{val}")
                self.statusBar().showMessage(f"代理已填入：{val}")
            else:
                self._emit_log(_fmt_dt(), LogLevel.WARN, "未检测到系统代理。")
                self.statusBar().showMessage("未检测到系统代理")
        except Exception as e:
            self._emit_log(_fmt_dt(), LogLevel.WARN, f"自动检测代理失败：{e}")

    def _save_rate_limit(self, value):
        """G04-4 保存下载限速（KiB/s，0=不限）。"""
        self.settings.rate_limit_kbps = int(value or 0)
        self.settings_store.save(self.settings)

    def _save_token(self):
        self.settings.token = self.edit_token.text().strip()
        self.settings_store.save(self.settings)

    def _save_auto_clear(self, checked):
        self.settings.auto_clear = bool(checked)
        self.settings_store.save(self.settings)

    def _save_fetch_unshallow(self, checked):
        self.settings.fetch_unshallow = bool(checked)
        self.settings_store.save(self.settings)

    def _save_submodule(self, checked):
        self.settings.submodule = bool(checked)
        self.settings_store.save(self.settings)

    def _save_clipboard_watch(self, checked):
        """G09-1 保存剪贴板监听开关并启停 watcher。"""
        self.settings.clipboard_watch = bool(checked)
        self.settings_store.save(self.settings)
        try:
            self.clipboard_watcher._enabled_flag = bool(checked)
            if checked:
                self.clipboard_watcher.start()
                self._emit_log(_fmt_dt(), LogLevel.INFO, "剪贴板监听已开启。")
            else:
                self.clipboard_watcher.stop()
                self._emit_log(_fmt_dt(), LogLevel.INFO, "剪贴板监听已关闭。")
        except Exception:
            pass

    def _save_finish_sound(self, checked):
        """G35-2 保存任务完成提示音开关。"""
        self.settings.finish_sound = bool(checked)
        self.settings_store.save(self.settings)

    def _save_animations(self, checked):
        """G36-6 保存任务完成动效开关。"""
        self.settings.animations = bool(checked)
        self.settings_store.save(self.settings)

    def _save_auto_update(self, value):
        """G37-4 保存自动更新间隔并按新间隔重启定时器。"""
        self.settings.auto_update_minutes = int(value or 0)
        self.settings_store.save(self.settings)
        self._restart_auto_update_timer()

    def _on_clipboard_url(self, url: str):
        """剪贴板检测到仓库地址：写日志 + 状态栏提示 + 填入输入框（不自动启动）。"""
        try:
            self.repo_input.appendPlainText(url + "\n")
            self._emit_log(_fmt_dt(), LogLevel.INFO, f"剪贴板检测到仓库地址：{url}（已填入输入框）")
            self.statusBar().showMessage(f"检测到仓库地址：{url}", 5000)
        except Exception:
            pass

    def _save_theme(self, index):
        """G05-1 保存主题选择并即时应用到主窗口。"""
        key = self.theme_combo.itemData(index) if index >= 0 else "deep"
        key = key or "deep"
        self.settings.theme = key
        self.settings_store.save(self.settings)
        try:
            from ..ui import theme as _th
            _th.apply_theme(key)
            self.setStyleSheet(_th.qss_for_scale(getattr(self.settings, "font_scale", 1.0)))
            self._apply_theme_to_children()
        except Exception:
            pass

    # --------------------------------------------------------- G10-1 字号缩放
    def _apply_font_scale(self):
        try:
            from ..ui import theme as _th
            self.setStyleSheet(_th.qss_for_scale(getattr(self.settings, "font_scale", 1.0)))
        except Exception:
            pass

    def zoom_in(self):
        self.settings.font_scale = round(min(1.6, self.settings.font_scale + 0.1), 2)
        self.settings_store.save(self.settings)
        self._apply_font_scale()

    def zoom_out(self):
        self.settings.font_scale = round(max(0.8, self.settings.font_scale - 0.1), 2)
        self.settings_store.save(self.settings)
        self._apply_font_scale()

    def zoom_reset(self):
        self.settings.font_scale = 1.0
        self.settings_store.save(self.settings)
        self._apply_font_scale()

    def _apply_theme_to_children(self):
        """把主题样例应用到弹出的对话框（详情/统计等）。"""
        from ..ui import theme as _th
        for child in self.findChildren(object):
            try:
                if child.__class__.__name__.endswith(("Dialog", "MainWindow")):
                    child.setStyleSheet(_th.QSS)
            except Exception:
                pass

    # ------------------------------------------------------------ 更新检查
    def check_update_now(self):
        try:
            from .. import __version__
            from ..app.updater import check_latest
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

    def _check_update_silent(self):
        """启动后静默后台检查更新：不阻塞、不弹窗；有新版本写日志 + 托盘提示。

        与手动按钮 check_update_now 共用 update_latest，只是结果呈现方式不同
        （err 静默返回；发现新版只记日志，需要时用户可去设置页手动操作）。
        """
        try:
            from .. import __version__
            from ..app.updater import check_latest
            has_new, ver, url, err = check_latest()
            if err:
                # G21-3：静默检查失败必须留痕（结构化 app.log），不再无痕
                try:
                    from ..app.applog import warn as _log_warn
                    _log_warn(f"check_update|silent_fail|{err}",
                              self.data_dir / "app.log")
                except Exception:
                    pass
                return
            if has_new:
                self._emit_log(_fmt_dt(), LogLevel.INFO,
                               f"发现新版本 {ver}（当前 {__version__}），可到设置页检查更新。")
                try:
                    if getattr(self, "tray", None) is not None:
                        self.tray.notify("Git-clone-Max 有新版本", f"最新 {ver}，可下载更新")
                except Exception:
                    pass
        except Exception:
            pass

    def save_log(self):
        from .download_tools import save_log as _sl
        _sl(self, _fmt_dt)

    # ------------------------------------------------------------ 动作
    def _repo_input_menu(self, pos):
        """输入框右键菜单：从最近 URL 历史回填 / 清空历史（G02-4）。"""
        from .download_tools import repo_input_menu as _rim
        _rim(self, pos, _fmt_dt)

    # ------------------------------------------------------------ G35-3 拖拽导入
    def _repo_input_drag_enter(self, e):
        """拖入 .txt/.csv 或目录时接受拖放（否则忽略）。"""
        if e.mimeData().hasUrls():
            urls = e.mimeData().urls()
            for u in urls:
                p = Path(u.toLocalFile())
                if p.is_file() and p.suffix.lower() in (".txt", ".csv"):
                    e.acceptProposedAction()
                    return
                if p.is_dir():
                    e.acceptProposedAction()
                    return
        e.ignore()

    def _repo_input_drop(self, e):
        """拖入文件 → 读入每行地址并追加到输入区；拖入目录 → 本地扫描导入。"""
        if not e.mimeData().hasUrls():
            e.ignore()
            return
        e.acceptProposedAction()
        added_lines: list[str] = []
        dirs: list[str] = []
        for u in e.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_file() and p.suffix.lower() in (".txt", ".csv"):
                try:
                    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                        line = line.strip()
                        if line and not line.startswith(("#", "//")):
                            added_lines.append(line)
                except Exception as exc:
                    self._emit_log(_fmt_dt(), LogLevel.WARN,
                                   f"读取拖入文件失败：{p}（{exc}）")
            elif p.is_dir():
                dirs.append(str(p))
        if added_lines:
            self.repo_input.appendPlainText("\n".join(added_lines) + "\n")
            self._emit_log(_fmt_dt(), LogLevel.INFO,
                           f"已从拖入文件导入 {len(added_lines)} 行地址。")
        if dirs:
            # 复用本地导入扫描通道（默认扫描拖入目录）
            try:
                from .local_repos_dialog import LocalReposDialog
                dlg = LocalReposDialog(parent=self, root_paths=dirs, db=self.db)
                dlg.exec()
                if dlg.selected:
                    n = dlg.import_selected()
                    self._load_db_into_grid()
                    self._emit_log(_fmt_dt(), LogLevel.INFO, f"已从拖入目录导入 {n} 个本地仓库")
                    self.statusBar().showMessage(f"已导入 {n} 个本地仓库")
            except Exception as exc:
                self._emit_log(_fmt_dt(), LogLevel.WARN, f"拖入目录导入失败：{exc}")

    def _clear_url_history(self):
        from .download_tools import clear_url_history as _cuh
        _cuh(self, _fmt_dt)

    # --------------------------------------------------------- G35-4 任务清单
    def _refresh_task_lists(self):
        """刷新清单下拉（保留当前选择若仍在列表）。"""
        names = self.task_lists.list_names()
        current = self.combo_load_list.currentText()
        self.combo_load_list.clear()
        self.combo_load_list.addItems(names)
        if current in names:
            self.combo_load_list.setCurrentText(current)

    def _save_task_list(self):
        """把当前输入区地址保存为命名清单。"""
        text = self.repo_input.toPlainText()
        items = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not items:
            QMessageBox.information(self, "提示", "输入区为空，没有可保存的地址。")
            return
        name, ok = QInputDialog.getText(self, "保存任务清单", "清单名称：")
        if not ok or not name.strip():
            return
        try:
            self.task_lists.save(name.strip(), items)
            self._refresh_task_lists()
            self._emit_log(_fmt_dt(), LogLevel.INFO, f"已保存任务清单：{name.strip()}（{len(items)} 行）")
            self.statusBar().showMessage(f"已保存清单：{name.strip()}")
        except Exception as e:
            QMessageBox.warning(self, "保存失败", str(e))

    def _load_task_list(self):
        """把所选清单的地址填入输入区。"""
        name = self.combo_load_list.currentText()
        if not name:
            QMessageBox.information(self, "提示", "请先选择要载入的清单。")
            return
        items = self.task_lists.items(name)
        if not items:
            QMessageBox.information(self, "提示", f"清单「{name}」为空或不存在。")
            return
        self.repo_input.appendPlainText("\n".join(items) + "\n")
        self._emit_log(_fmt_dt(), LogLevel.INFO, f"已载入任务清单：{name}（{len(items)} 行）")
        self.statusBar().showMessage(f"已载入清单：{name}")

    def choose_target(self):
        from .download_tools import choose_target as _ct
        _ct(self)

    def open_target(self):
        from .download_tools import open_target as _ot
        _ot(self)

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
        # 下载完成后自动清空输入框（跟随设置页开关）
        self._launch(specs, target_root=Path(target), shallow=shallow, depth=depth,
                     clear_input=self.settings.auto_clear)

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
        rows = self.db.list_pending_updates()  # 全 host（已排除黑名单 excluded=1）
        if not rows:
            QMessageBox.information(self, "提示", "数据库中没有已记录的仓库，请先到「下载中心」添加。")
            return
        # 按 local_path 反推根目录集合：仓库可能分散在多个下载目录
        from collections import OrderedDict
        groups: OrderedDict[str, list] = OrderedDict()
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
            submodule=bool(getattr(self.settings, "submodule", False)),
            rate_limit_kbps=int(getattr(self.settings, "rate_limit_kbps", 0) or 0),
        )
        self.engine.line.connect(self._on_engine_line)
        self.engine.progress.connect(self._on_worker_progress)
        self.engine.progress_detail.connect(self._on_worker_progress_detail)
        self.engine.result.connect(self._on_worker_result)
        self.engine.finished.connect(self._on_engine_finished)
        self.pool = self.engine.pool
        # G22-4：托盘进度计数接入（托盘未安装时 update_counts 静默无效）
        try:
            if getattr(self, "tray", None) is not None:
                self.tray.update_counts(reset=True)
        except Exception:
            pass

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
        else:
            # G21-1：表格行与 engine 去重后的索引严格对齐（去重/断点跳过都会
            # 改变唯一清单），旧行全部废弃重建，避免暂停/进度按错行
            n = len(self.engine.specs)
            self._prepare_table(n)
            for i in range(n):
                spec = self.engine.specs[i]
                self._add_table_row(i, spec)
                self.row_specs[i] = spec
            self._pending_count = n
            self.statusBar().showMessage(f"并行同步中：{n} 个仓库…")
            return
        self.statusBar().showMessage(f"并行同步中：{len(specs)} 个仓库…")

    # ------------------------------------------------------------ 槽
    def _on_engine_line(self, index, text, level):
        self._emit_log(_fmt_dt(), LogLevel(level or "info"),
                       f"[{index}] {text}" if index is not None else text)

    def _prepare_table(self, n):
        # 排序开启下先关掉再重建，避免插入行时被自动重排打乱 index
        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(n)
        self.table.setSortingEnabled(was_sorting)
        # 过滤状态保持
        self._filter_progress_rows(getattr(self, "_progress_filter_text", ""))
        # G36-5 空态引导：无行 → 显示 overlay
        self._update_empty_download()

    def _update_empty_download(self):
        """G36-5 下载中心空态：进度表无可见行时显示引导 overlay。"""
        try:
            ov = getattr(self, "_empty_download", None)
            if ov is None:
                return
            visible = self.table.rowCount() == 0
            ov.setVisible(visible)
            if visible:
                ov.setGeometry(self.table.rect())
                ov.raise_()
        except Exception:
            pass

    def _add_table_row(self, index, spec: RepoSpec):
        # 关闭排序再插入行，保证 index 与行号对齐（引擎回调按 index 定位）
        was_sorting = self.table.isSortingEnabled()
        if was_sorting:
            self.table.setSortingEnabled(False)
        name_item = QTableWidgetItem(spec.folder_name)
        name_item.setToolTip(spec.url_https)
        name_item.setData(Qt.ItemDataRole.UserRole, spec.folder_name)  # 供过滤
        # G21-1：行内保存 engine index，排序/过滤后暂停仍能定位正确任务
        name_item.setData(Qt.ItemDataRole.UserRole + 1, int(index))
        self.table.setItem(index, 0, name_item)
        prog = QProgressBar()
        prog.setRange(0, 0)
        prog.setFormat("%p%")
        self.table.setCellWidget(index, 1, prog)
        status_item = QTableWidgetItem("等待中")
        status_item.setData(Qt.ItemDataRole.UserRole, "pending")  # 供排序
        status_item.setForeground(QColor(PALETTE["text_dim"]))
        self.table.setItem(index, 2, status_item)
        act_item = QTableWidgetItem("—")
        act_item.setForeground(QColor(PALETTE["text_dim"]))
        self.table.setItem(index, 3, act_item)
        msg_item = QTableWidgetItem("—")
        msg_item.setForeground(QColor(PALETTE["text_dim"]))
        self.table.setItem(index, 4, msg_item)
        if was_sorting:
            self.table.setSortingEnabled(True)

    # --------------------------------------------------------- G02-1/2 搜索与排序
    def _schedule_progress_filter(self, text=""):
        """防抖：停止上一个定时器，200ms 后应用过滤。"""
        self._progress_filter_text = text
        if not hasattr(self, "_pf_timer"):
            from PyQt6.QtCore import QTimer
            self._pf_timer = QTimer(self)
            self._pf_timer.setSingleShot(True)
            self._pf_timer.setInterval(200)
            self._pf_timer.timeout.connect(lambda: self._filter_progress_rows(text))
        self._pf_timer.start()

    def _filter_progress_rows(self, text=""):
        """按仓库名校验过滤进度表行；返回可见行数（供测试）。"""
        q = (text or "").strip().lower()
        hidden = 0
        for r in range(self.table.rowCount()):
            it = self.table.item(r, 0)
            name = it.text() if it else ""
            if q and q not in name.lower():
                self.table.hideRow(r)
                hidden += 1
            else:
                self.table.showRow(r)
        return self.table.rowCount() - hidden

    def _on_sort_changed(self, section, order):
        """状态列排序：用 UserRole 存的优先级；其余列默认字典序。"""
        try:
            order_map = {"running": 0, "pending": 1, "success": 2,
                         "conflict": 3, "failed": 4, "cancelled": 5, "skipped": 6}
            if section == 2:
                for r in range(self.table.rowCount()):
                    it = self.table.item(r, 2)
                    if it is not None:
                        # 显示文本含符号后缀（G36-2），按 UserRole 原始状态值映射优先级
                        base = it.data(Qt.ItemDataRole.UserRole) or it.text()
                        pri = order_map.get(base, 99)
                        it.setData(Qt.ItemDataRole.UserRole + 1, pri)
        except Exception:
            pass

    # --------------------------------------------------------- G02-3 单仓库暂停
    def pause_selected(self):
        """暂停进度表中选中的行（仅取消该 worker，其余继续）。

        G21-1：行号只是视觉位置（排序/过滤后与 engine index 脱钩），
        必须从行数据读回真实 index 再暂停。
        """
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            return
        paused = 0
        for r in rows:
            it = self.table.item(r, 0)
            idx = it.data(Qt.ItemDataRole.UserRole + 1) if it is not None else None
            if idx is None:
                idx = r  # 兼容：无标记行退化为旧行为
            if (hasattr(self, "engine") and self.engine is not None
                    and self.engine.pause_task(int(idx))):
                paused += 1
        self._emit_log(_fmt_dt(), LogLevel.WARN,
                       f"已请求暂停 {paused} 个任务（其余继续）…")
        self.statusBar().showMessage(f"正在暂停 {paused} 个任务…")

    # --------------------------------------------------------- G35-1 进度表右键菜单
    def _progress_table_menu(self, pos):
        """进度表右键菜单：重试此仓库 / 复制错误详情 / 打开所在目录。

        FAILED/CANCELLED 行可用「重试此仓库」；其余动作对所有行可用。
        """
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        name_item = self.table.item(row, 0)
        status_item = self.table.item(row, 2)
        if name_item is None or status_item is None:
            return
        from PyQt6.QtWidgets import QMenu
        menu = QMenu(self)
        # 用 UserRole 存的原始状态键判断（符号只是视觉后缀，不参与逻辑）
        status_key = status_item.data(Qt.ItemDataRole.UserRole) or ""
        if status_key in ("failed", "cancelled"):
            act_retry = menu.addAction("⟳ 重试此仓库")
            act_retry.triggered.connect(
                lambda _=False, r=row: self._retry_table_row(r))
            menu.addSeparator()
        act_copy = menu.addAction("复制错误详情")
        act_copy.triggered.connect(
            lambda _=False, r=row: self._copy_row_detail(r))
        act_open = menu.addAction("打开所在目录")
        act_open.triggered.connect(
            lambda _=False, r=row: self._open_row_dir(r))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _retry_table_row(self, row: int):
        """单仓库重试：把该行 spec 经 _launch 重新调度（其余任务不受影响）。"""
        if self.busy:
            QMessageBox.information(self, "提示", "有任务正在运行，请等本轮结束后重试。")
            return
        idx = self._row_engine_index(row)
        spec = self.row_specs.get(idx)
        if spec is None:
            return
        target = self.target_edit.text().strip() or str(self.data_dir / "clones")
        self._launch([spec], target_root=Path(target),
                     shallow=False, depth=self.depth_spin.value(), clear_input=False)
        self._emit_log(_fmt_dt(), LogLevel.WARN, f"已重试仓库：{spec.display}")

    def _copy_row_detail(self, row: int):
        """复制该行错误详情（message + detail）到剪贴板。"""
        msg_item = self.table.item(row, 4)
        detail = ""
        if msg_item is not None:
            detail = msg_item.toolTip() or msg_item.text()
        from PyQt6.QtWidgets import QApplication as _QApp
        _QApp.clipboard().setText(detail)
        self.statusBar().showMessage("错误详情已复制")

    def _open_row_dir(self, row: int):
        """打开该行仓库所在目录（Windows startfile / POSIX 打开器）。"""
        idx = self._row_engine_index(row)
        spec = self.row_specs.get(idx)
        if spec is None:
            return
        if spec.local_path:
            p = Path(spec.local_path)
        else:
            p = Path(self.target_edit.text().strip() or self.data_dir / "clones") / spec.folder_name
        if not p.is_dir():
            QMessageBox.warning(self, "目录不存在", str(p))
            return
        if sys.platform == "win32":
            os.startfile(str(p))  # noqa
        else:
            import shutil
            openers = ("xdg-open", "open")
            for op in openers:
                if shutil.which(op):
                    import subprocess
                    subprocess.Popen([op, str(p)])
                    break

    def _row_engine_index(self, row: int) -> int:
        """从表格行读回 engine index（UserRole+1；排序/过滤后仍精确）。"""
        it = self.table.item(row, 0)
        idx = it.data(Qt.ItemDataRole.UserRole + 1) if it is not None else None
        return int(idx) if idx is not None else row

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

    # 高频进度详情（速率/对象数）节流：合并为 300ms 批量刷新，32 并发不刷屏主线程
    @pyqtSlot(int, str)
    def _on_worker_progress_detail(self, index, text):
        if not (0 <= index < self.table.rowCount()):
            return
        self._detail_batch[index] = text
        if not self._detail_timer.isActive():
            self._detail_timer.start()

    def _flush_detail_batch(self):
        if not getattr(self, "_detail_batch", None):
            return
        batch, self._detail_batch = self._detail_batch, {}
        for idx, text in batch.items():
            w = self.table.cellWidget(idx, 1)
            if isinstance(w, QProgressBar):
                try:
                    w.setFormat(f"%p%  {text}")
                except Exception:
                    pass

    @pyqtSlot(int, SyncResult)
    def _on_worker_result(self, index, res: SyncResult):
        bar = self.table.cellWidget(index, 1)
        if isinstance(bar, QProgressBar):
            bar.setRange(0, 1)
            bar.setValue(1)
        status_map = {
            SyncStatus.SUCCESS: (PALETTE["accent2"], "成功 ✓"),
            SyncStatus.FAILED: (PALETTE["error"], "失败 ✕"),
            SyncStatus.CANCELLED: (PALETTE["warning"], "已取消 ⊘"),
            SyncStatus.CONFLICT: (PALETTE["warning"], "冲突 ⚠"),
            SyncStatus.SKIPPED: (PALETTE["text_dim"], "跳过 →"),
            SyncStatus.RUNNING: (PALETTE["accent"], "更新中…"),
        }
        color, label = status_map.get(res.status, (PALETTE["text"], str(res.status.value)))
        # G22-4：托盘计数（成功/失败分流；冲突/失败归 bad，其余终态归 ok）
        try:
            if getattr(self, "tray", None) is not None:
                if res.status in (SyncStatus.FAILED, SyncStatus.CONFLICT):
                    self.tray.update_counts(bad_delta=1)
                else:
                    self.tray.update_counts(ok_delta=1)
        except Exception:
            pass
        st = self.table.item(index, 2)
        st.setText(label)
        st.setForeground(QColor(color))
        # 状态原始值存 UserRole：排序/右键/统计按原始值判断（符号仅视觉）
        st.setData(Qt.ItemDataRole.UserRole, res.status.value)
        # G35-8 状态格 tooltip：message + detail 拼接（与详情列 tooltip 互补）
        st.setToolTip(f"{res.message or ''}" + (f"\n{res.detail or ''}" if res.detail else ""))
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
        # G36-6 完成动效：成功绿/失败红背景色 1.2s 消隐（设置开关；offscreen 自动关）
        if bool(getattr(self.settings, "animations", True)) and \
                os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            try:
                self._animate_result_row(index, res.status)
            except Exception:
                pass
        # 同步成功 → 地址入 URL 历史（G02-4）
        try:
            if res.status == SyncStatus.SUCCESS:
                self.url_history.add(res.spec.url_https)
        except Exception:
            pass
        # 记录到 DB 由 worker 内部完成
        self._emit_log(_fmt_dt(), LogLevel.INFO if res.status == SyncStatus.SUCCESS else LogLevel.WARN,
                        f"[{index}] {label}：{res.message}（{action_label}）")

    def _animate_result_row(self, index: int, status: SyncStatus):
        """G36-6 完成动效：成功绿/失败红背景色 1.2s 淡出到主题底色。

        仅在非 offscreen（有真实渲染）且设置开启时由 _on_worker_result 调用。
        动效结束后背景复位为主题面板色，不影响排序/过滤的 UserRole 数据。
        """
        try:
            from PyQt6.QtCore import QVariantAnimation
            from PyQt6.QtGui import QBrush
            st = self.table.item(index, 2)
            if st is None:
                return
            flash = PALETTE["accent2"] if status == SyncStatus.SUCCESS else PALETTE["error"]
            start_color = QColor(flash)
            base_color = QColor(PALETTE["panel"])
            anim = QVariantAnimation(self)
            anim.setDuration(1200)
            anim.setStartValue(start_color)
            anim.setEndValue(base_color)
            anim.valueChanged.connect(
                lambda c: st.setBackground(QBrush(QColor(c))))
            anim.finished.connect(
                lambda: st.setBackground(QBrush(base_color)))
            # 保留引用防止被 GC（存到窗口级列表）
            if not hasattr(self, "_row_anims"):
                self._row_anims = []
            self._row_anims.append(anim)
            anim.finished.connect(lambda: self._row_anims.remove(anim))
            anim.start()
        except Exception:
            pass

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
            # 用 UserRole 存的原始状态值统计（显示文本含符号后缀，不再做字符串匹配）
            t = it.data(Qt.ItemDataRole.UserRole) or it.text()
            if t in ("success", "failed", "cancelled", "conflict", "skipped"):
                done += 1
                if t == "success":
                    ok += 1
                elif t == "conflict":
                    conflict += 1
                elif t == "failed":
                    fail += 1
        self.busy = False
        self._reset_buttons()
        try:
            if getattr(self, "tray", None) is not None:
                self.tray.update_counts(reset=True)  # 空闲态 tooltip（G22-4）
        except Exception:
            pass
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
        # G35-2 完成提示音（设置开关默认开；QApplication.beep 无 UI 影响）
        try:
            if bool(getattr(self.settings, "finish_sound", True)):
                from PyQt6.QtWidgets import QApplication as _QApp
                _QApp.beep()
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

    # --------------------------------------------------------- G36-8 全局热键
    def _hotkey_start_cancel(self):
        """Ctrl+Alt+S：空闲→开始全部；运行中→取消全部。"""
        if self.busy:
            self.cancel_all()
        else:
            self.start_all()

    def _hotkey_show_window(self):
        """Ctrl+Alt+M：显示并激活主窗口（托盘最小化后可唤回）。"""
        try:
            self.show()
            self.raise_()
            self.activateWindow()
        except Exception:
            pass

    # ------------------------------------------------------------ 管理页
    def _load_db_into_grid(self):
        """从 DB 加载仓库列表到模型（全 host，含本地导入仓库；批量懒渲染）。"""
        self.manage_model.set_rows(self.db.list_repos())
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
        self._load_db_into_grid()

    def _on_manage_double_clicked(self, index):
        row = index.row()
        r = self.manage_model.row_at(row)
        if r is not None:
            self.show_history(r.repo_id)

    def _on_hist_row_clicked(self, row: int):
        """G03-7 行内「查看」按钮委托回调：直接打开该行历史（无需先选中）。"""
        r = self.manage_model.row_at(row)
        if r is not None:
            self.show_history(r.repo_id)

    def _on_hist_btn(self):
        """兼容入口：对当前选中的行打开历史（多选场景仍可用）。"""
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

    # --------------------------------------------------------- G35-9 批量操作
    def batch_tag_selected(self):
        """给选中的仓库追加标签（多选批量，覆盖式设置指定标签）。"""
        rows = sorted({i.row() for i in self.manage_table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "提示", "请先选择要打标签的仓库。")
            return
        tag, ok = QInputDialog.getText(
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
                rec = self.db.get_repo(row.owner, row.repo, row.host)
                if rec:
                    self.db.set_tags(rec["id"], tags)
                    n += 1
        self._load_db_into_grid()
        self._emit_log(_fmt_dt(), LogLevel.INFO, f"已为 {n} 个仓库设置标签：{', '.join(tags)}")
        self.statusBar().showMessage(f"已为 {n} 个仓库设置标签")

    def export_selected_csv(self):
        """把选中的仓库导出为 CSV（仅所选行；无历史则同样导出元数据）。"""
        rows = sorted({i.row() for i in self.manage_table.selectedIndexes()})
        if not rows:
            QMessageBox.information(self, "提示", "请先选择要导出的仓库。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出所选仓库", str(self.data_dir / "selected_repos.csv"),
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
            self._emit_log(_fmt_dt(), LogLevel.INFO, f"已导出所选 {len(sel)} 个仓库：{path}")
            self.statusBar().showMessage(f"已导出 {len(sel)} 个仓库")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def resizeEvent(self, e):
        """G36-5 空态 overlay 跟随表格尺寸重定位。"""
        try:
            if getattr(self, "_empty_download", None) is not None and \
                    self._empty_download.isVisible():
                self._empty_download.setGeometry(self.table.rect())
            if getattr(self, "_empty_manage", None) is not None and \
                    self._empty_manage.isVisible():
                self._empty_manage.setGeometry(self.manage_table.rect())
        except Exception:
            pass
        super().resizeEvent(e)

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
            # G22-1：先取消并等待在跑 worker 收敛（≤8s），杜绝硬杀 git 子进程
            # 与 progress.json 半描述态；超时则记日志后按原语义退出
            try:
                ok = self.engine.drain(8000)
                if not ok:
                    self._emit_log(_fmt_dt(), LogLevel.WARN,
                                   "仍有任务未在 8s 内收敛，将强制退出。")
            except Exception:
                pass
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
            # G36-4 记忆窗口几何（尺寸+位置），重启恢复
            self.settings.geometry = self._serialize_geometry()
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
