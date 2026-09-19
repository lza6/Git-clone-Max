"""主窗口：三个 Tab（下载中心 / 仓库管理 / 设置 与 黑匣子日志）。"""
from __future__ import annotations

import os
import time
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSlot
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,  # noqa: F401  — 测试 mock 引用（download_tools 经本模块访问）
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..app.engine import SyncEngine
from ..app.url_lib import parse_urls
from ..app.worker import CancelFlag
from ..db.repo_db import Database
from ..db.settings import SettingsStore
from ..i18n import tr  # G49-1
from ..models import RepoSpec, SyncResult, SyncStatus
from .log_buffer import LogBuffer
from .repo_detail_dialog import RepoDetailDialog
from .theme import LogLevel, LogModel

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
                 settings: SettingsStore | None = None,
                 start_hidden: bool = False):
        super().__init__()
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        # G49-6：--minimized 启动标记（托盘可用时隐入托盘不弹主窗）
        self.start_hidden = bool(start_hidden)
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
            host_tokens=getattr(self.settings, "host_tokens", None) or {},  # G38-1
            mirror_prefix=getattr(self.settings, "mirror_prefix", None) or {},  # G38-2
            precheck_remote=bool(getattr(self.settings, "precheck_remote", False)),  # G38-3
            single_branch=bool(getattr(self.settings, "single_branch", False)),  # G38-4
            force_ipv4=bool(getattr(self.settings, "force_ipv4", False)),  # G38-6
            lfs_enabled=bool(getattr(self.settings, "lfs_enabled", False)),  # G48-2
            post_clone_hook=str(getattr(self.settings, "post_clone_hook", "")),  # G48-5
        )
        self.engine.line.connect(self._on_engine_line)
        self.engine.progress.connect(self._on_worker_progress)
        self.engine.progress_detail.connect(self._on_worker_progress_detail)
        self.engine.result.connect(self._on_worker_result)
        self.engine.finished.connect(self._on_engine_finished)
        self.engine.adapt_changed.connect(self._on_adapt_changed)  # G48-1
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

        # G45-6：进度详情节流定时器已迁入 progress_table.py（ProgressTable）
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
        self.setStyleSheet(self._themed_qss())
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
                self.setStyleSheet(self._themed_qss())
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
        self.tabs.addTab(self.tab_download, tr("下载中心"))
        self.tabs.addTab(self.tab_manage, tr("仓库管理"))
        self.tabs.addTab(self.tab_settings, tr("设置与日志"))
        # G46-2 页签图标化（零资源：复用 std_icon 语义图标）
        for _i, _key in zip((0, 1, 2), ("arrow_down", "folder", "info"), strict=True):
            _ic = self._std_icon(_key, self.tabs)
            if _ic is not None:
                self.tabs.setTabIcon(_i, _ic)

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

        box = QGroupBox(tr("仓库地址（每行一个）"))
        b = QVBoxLayout(box)
        self.repo_input = QPlainTextEdit()
        self.repo_input.setPlaceholderText(
            tr("每行一个仓库地址，例如：\n")
            + "https://github.com/vercel-labs/skills\n"
            + "git@github.com:microsoft/azure-skills.git\n"
            + "vercel-labs/agent-skills\n"
            + tr("https://gitlab.com/grp/repo@v1.2.0  （@后指定分支/标签）\n")
            + tr("（地址中的 作者/仓库名 将自动作为文件夹名：作者__仓库）")
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
        quick.addWidget(QLabel(tr("快捷填充：")))
        # G35-7 快捷填充可配置：跟随 settings.quick_repos（持久化，可编辑）
        for repo in getattr(self.settings, "quick_repos", None) or ():
            btn = QPushButton(repo)
            btn.clicked.connect(lambda _=False, r=repo: self.repo_input.appendPlainText(
                f"https://github.com/{r}"))
            quick.addWidget(btn)
        quick.addStretch()
        # G35-4 任务清单：保存当前输入区为命名清单 / 下拉载入（分享用 data/lists/*.json）
        self.btn_save_list = QPushButton(tr("💾 保存清单"))
        self.btn_save_list.setToolTip(tr("把当前输入区的地址保存为命名任务清单（data/lists/*.json）"))
        self.btn_save_list.clicked.connect(self._save_task_list)
        self.combo_load_list = QComboBox()
        self.combo_load_list.setToolTip(tr("选择已保存的清单一键载入"))
        self.combo_load_list.setMinimumWidth(180)
        self.btn_load_list = QPushButton(tr("载入"))
        self.btn_load_list.setToolTip(tr("把所选清单的地址填入输入区"))
        self.btn_load_list.clicked.connect(self._load_task_list)
        quick.addWidget(self.btn_save_list)
        quick.addWidget(self.combo_load_list)
        quick.addWidget(self.btn_load_list)
        b.addLayout(quick)
        v.addWidget(box)

        opts = QHBoxLayout()
        g1 = QGroupBox(tr("下载位置"))
        l1 = QHBoxLayout(g1)
        # 恢复上次选择（settings.download_dir），否则默认 data/clones
        default_dir = str(self.settings.download_dir or (self.data_dir / "clones"))
        self.target_edit = QLineEdit(default_dir)
        btn_browse = QPushButton(tr("浏览…"))
        btn_browse.clicked.connect(self.choose_target)
        l1.addWidget(self.target_edit, 1)
        l1.addWidget(btn_browse)
        opts.addWidget(g1, 1)

        g2 = QGroupBox(tr("克隆模式"))
        l2 = QHBoxLayout(g2)
        self.mode_combo = QComboBox()
        self.mode_combo.addItems([tr("满量克隆（完整历史）"), tr("浅克隆（最新代码）"),
                                  tr("单分支浅克隆（--single-branch）"),
                                  tr("镜像克隆（--mirror）")])
        self.depth_spin = QSpinBox()
        self.depth_spin.setRange(1, 10000)
        self.depth_spin.setValue(1)
        self.depth_spin.setSuffix(" 层")
        self.depth_spin.setEnabled(False)
        self.mode_combo.currentIndexChanged.connect(
            lambda i: self.depth_spin.setEnabled(i in (1, 2)))
        l2.addWidget(self.mode_combo)
        l2.addWidget(self.depth_spin)
        opts.addWidget(g2)
        v.addLayout(opts)

        btns = QHBoxLayout()
        self.btn_start = QPushButton(tr("开始并行下载 / 更新"))
        self.btn_start.setObjectName("primary")
        self.btn_start.setMinimumHeight(38)
        self.btn_start.clicked.connect(self.start_all)
        self.btn_start.setIcon(self._std_icon("play", self.btn_start))
        self.btn_cancel = QPushButton(tr("取消全部"))
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_all)
        self.btn_cancel.setIcon(self._std_icon("stop", self.btn_cancel))
        self.btn_clear = QPushButton(tr("清空列表"))
        self.btn_clear.clicked.connect(self.repo_input.clear)
        self.btn_open = QPushButton(tr("打开克隆目录"))
        self.btn_open.clicked.connect(self.open_target)
        self.btn_open.setIcon(self._std_icon("open_dir", self.btn_open))
        btns.addWidget(self.btn_start, 2)
        btns.addWidget(self.btn_cancel)
        btns.addWidget(self.btn_clear)
        btns.addWidget(self.btn_open)
        btns.addStretch()
        v.addLayout(btns)

        # G45-6：进度表/空态/过滤框/暂停 收敛到 progress_table.py（ProgressTable）
        from .progress_table import ProgressTable
        self.progress_panel = ProgressTable(owner=self, parent=self.tab_download)
        v.addWidget(self.progress_panel, 3)
        # 镜像控件（兼容外部引用/测试）
        self.table = self.progress_panel.table
        self._empty_download = self.progress_panel._empty_download
        self.progress_filter = self.progress_panel.progress_filter

    # ---------------- 仓库管理
    def _build_manage_tab(self):
        # G45-6：管理页 UI/动作收敛到 manage_panel.py（ManagePanel），信号回传
        from .manage_panel import ManagePanel
        v = QVBoxLayout(self.tab_manage)
        self.manage_panel = ManagePanel(owner=self, parent=self.tab_manage)
        v.addWidget(self.manage_panel)
        # 镜像控件（兼容外部引用/测试）
        self.manage_stats = self.manage_panel.manage_stats
        self.manage_model = self.manage_panel.manage_model
        self.manage_table = self.manage_panel.manage_table
        self._hist_delegate = self.manage_panel._hist_delegate
        self._empty_manage = self.manage_panel._empty_manage
        self.manage_desc = self.manage_panel.manage_desc
        self.btn_update_all = self.manage_panel.btn_update_all
        self.btn_cancel_manage = self.manage_panel.btn_cancel_manage
        self.btn_refresh = self.manage_panel.btn_refresh
        self.btn_import_local = self.manage_panel.btn_import_local
        self.btn_delete = self.manage_panel.btn_delete
        self.btn_batch_tag = self.manage_panel.btn_batch_tag
        self.btn_export_selected = self.manage_panel.btn_export_selected
        # 面板信号 → 窗口动作
        self.manage_panel.update_all_requested.connect(self.update_all)
        self.manage_panel.cancel_all_requested.connect(self.cancel_all)
        self.manage_panel.import_local_requested.connect(self.import_local_repos)
        self.manage_panel.manage_double_clicked.connect(self._on_manage_double_clicked)
        self.manage_panel.history_row_clicked.connect(self._on_hist_row_clicked)

    # ---------------- 设置与日志
    def _build_settings_tab(self):
        # G45-6：设置回调收敛到 settings_panel.SettingsPanel（MainWindow 保留同名转发）
        from .settings_panel import SettingsPanel
        if not hasattr(self, "settings_panel"):
            self.settings_panel = SettingsPanel(owner=self, parent=self.tab_settings)
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
    def _save_concurrency(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_concurrency(*args, **kwargs)

    def _save_fetch_timeout(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_fetch_timeout(*args, **kwargs)

    def _save_retries(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_retries(*args, **kwargs)

    def _save_proxy(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_proxy(*args, **kwargs)

    def _detect_proxy_now(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._detect_proxy_now(*args, **kwargs)

    def _save_rate_limit(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_rate_limit(*args, **kwargs)

    def _save_token(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_token(*args, **kwargs)
    def _save_host_tokens(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_host_tokens(*args, **kwargs)

    def _save_mirror(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_mirror(*args, **kwargs)

    def _save_custom_hosts(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_custom_hosts(*args, **kwargs)

    def _save_precheck(self, checked):
        """G38-3 保存远端可达性预检开关。"""
        self.settings.precheck_remote = bool(checked)
        self.settings_store.save(self.settings)

    def _save_single_branch(self, checked):
        """G38-4 保存单分支浅克隆开关。"""
        self.settings.single_branch = bool(checked)
        self.settings_store.save(self.settings)

    def _save_force_ipv4(self, checked):
        """G38-6 保存强制 HTTP/1.1 开关。"""
        self.settings.force_ipv4 = bool(checked)
        self.settings_store.save(self.settings)

    @staticmethod
    @staticmethod
    def _parse_kv_text(text: str) -> dict:
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        from .settings_panel import SettingsPanel
        return SettingsPanel._parse_kv_text(text)

    def _save_auto_clear(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_auto_clear(*args, **kwargs)

    def _save_fetch_unshallow(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_fetch_unshallow(*args, **kwargs)

    def _save_submodule(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_submodule(*args, **kwargs)

    def _save_clipboard_watch(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_clipboard_watch(*args, **kwargs)

    def _save_finish_sound(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_finish_sound(*args, **kwargs)

    def _save_animations(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_animations(*args, **kwargs)

    def _save_auto_update(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_auto_update(*args, **kwargs)

    def _on_clipboard_url(self, url: str):
        """剪贴板检测到仓库地址：写日志 + 状态栏提示 + 填入输入框（不自动启动）。"""
        try:
            self.repo_input.appendPlainText(url + "\n")
            self._emit_log(_fmt_dt(), LogLevel.INFO, f"剪贴板检测到仓库地址：{url}（已填入输入框）")
            self.statusBar().showMessage(f"检测到仓库地址：{url}", 5000)
        except Exception:
            pass

    def _save_theme(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_theme(*args, **kwargs)
    def _themed_qss(self) -> str:
        """G46-7/G46-8：按当前主题 + 强调色 + 字号 + 动效偏好生成 QSS。"""
        from ..ui import theme as _th
        pal = _th.PALETTES.get(getattr(self.settings, "theme", "deep")) or _th.PALETTES["deep"]
        pal = _th.theme_with_accent(pal, getattr(self.settings, "accent_preset", "blue"))
        motion = not bool(getattr(self.settings, "prefers_reduced_motion", False))
        return _th.qss_for_palette(pal, getattr(self.settings, "font_scale", 1.0), motion)

    def _apply_font_scale(self):
        try:
            self.setStyleSheet(self._themed_qss())
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
        """输入框右键菜单：历史回填 / 清空 / G48-7 归档下载。"""
        from .download_tools import repo_input_menu as _rim

        def _extra(menu):
            act = menu.addAction("📦 归档下载（zip，免入库）")
            act.triggered.connect(lambda _=False: self._archive_current_line())
        _rim(self, pos, _fmt_dt, extra_actions=_extra)

    def _archive_current_line(self):
        """G48-7：对输入区光标所在行地址执行归档下载到 data/archives/。"""
        text = self.repo_input.toPlainText()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            self._emit_log(_fmt_dt(), LogLevel.WARN, "输入区为空，无法归档下载。")
            return
        url = lines[0]
        try:
            from gcm.git.archive import download_archive
        except Exception:
            self._emit_log(_fmt_dt(), LogLevel.WARN, "归档模块不可用。")
            return
        dest_dir = self.data_dir / "archives"
        dest = dest_dir / (url.rstrip("/").split("/")[-1].removesuffix(".git") + ".zip")
        dest_dir.mkdir(parents=True, exist_ok=True)

        def _run():
            try:
                download_archive(url, dest)
                self._emit_log(_fmt_dt(), LogLevel.INFO, f"归档下载完成：{dest}")
                self.statusBar().showMessage(f"归档已保存：{dest}")
            except Exception as e:
                self._emit_log(_fmt_dt(), LogLevel.WARN, f"归档下载失败：{e}")

        import threading
        threading.Thread(target=_run, daemon=True).start()
        self.statusBar().showMessage("归档下载中…")

    def _on_adapt_changed(self, n: int, text: str):
        """G48-1：并发自适应状态栏提示。"""
        try:
            self.statusBar().showMessage(text)
            self._emit_log(_fmt_dt(), LogLevel.WARN, text)
        except Exception:
            pass

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
        # G38-4：模式 1=浅克隆、2=单分支浅克隆（都走 shallow 路径，后者加 --single-branch）
        mode_idx = self.mode_combo.currentIndex()
        shallow = mode_idx in (1, 2)
        mirror = mode_idx == 3  # G48-6 镜像克隆
        depth = self.depth_spin.value()
        # 下载完成后自动清空输入框（跟随设置页开关）
        self._launch(specs, target_root=Path(target), shallow=shallow, depth=depth,
                     clear_input=self.settings.auto_clear, mirror=mirror)

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
                multi_root_relay=False, single_branch=None, mirror=False):
        # G38-4：单分支源 = 显式参数 或 设置页开关 或 UI 下拉「单分支浅克隆」（索引2）。
        # 缺陷记录：原先只按 UI 索引推断，设置页 ck_single_branch 开启后对 _launch
        # 重建的 engine 不生效；现三源取或，行为可预测。
        if single_branch is None:
            try:
                ui_mode = self.mode_combo.currentIndex() == 2
            except Exception:
                ui_mode = False
            single_branch = ui_mode or bool(
                getattr(self.settings, "single_branch", False))
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
            host_tokens=getattr(self.settings, "host_tokens", None) or {},  # G38-1
            mirror_prefix=getattr(self.settings, "mirror_prefix", None) or {},  # G38-2
            precheck_remote=bool(getattr(self.settings, "precheck_remote", False)),  # G38-3
            single_branch=bool(single_branch),  # G38-4（UI 模式 2 或设置）
            force_ipv4=bool(getattr(self.settings, "force_ipv4", False)),  # G38-6
            lfs_enabled=bool(getattr(self.settings, "lfs_enabled", False)),  # G48-2
            post_clone_hook=str(getattr(self.settings, "post_clone_hook", "")),  # G48-5
            mirror=bool(mirror),  # G48-6
        )
        self.engine.line.connect(self._on_engine_line)
        self.engine.adapt_changed.connect(self._on_adapt_changed)  # G48-1
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
            mirror=bool(mirror),  # G48-6
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
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._on_engine_line(index, text, level)

    def _prepare_table(self, n):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._prepare_table(n)

    def _update_empty_download(self):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._update_empty_download()

    def _add_table_row(self, index, spec: RepoSpec):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._add_table_row(index, spec)

    def _schedule_progress_filter(self, text=""):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._schedule_progress_filter(text)

    def _filter_progress_rows(self, text=""):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._filter_progress_rows(text)

    def _on_sort_changed(self, section, order):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._on_sort_changed(section, order)

    def pause_selected(self):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel.pause_selected()

    def _progress_table_menu(self, pos):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._progress_table_menu(pos)

    def _retry_table_row(self, row: int):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._retry_table_row(row)

    def _copy_row_detail(self, row: int):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._copy_row_detail(row)

    def _open_row_dir(self, row: int):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._open_row_dir(row)

    def _row_engine_index(self, row: int):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._row_engine_index(row)

    @pyqtSlot(int, str)
    def _on_worker_progress(self, index, percent):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._on_worker_progress(index, percent)

    @pyqtSlot(int, str)
    def _on_worker_progress_detail(self, index, text):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._on_worker_progress_detail(index, text)

    def _flush_detail_batch(self):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._flush_detail_batch()

    @pyqtSlot(int, SyncResult)
    def _on_worker_result(self, index, res: SyncResult):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._on_worker_result(index, res)

    def _animate_result_row(self, index: int, status: SyncStatus):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._animate_result_row(index, status)

    # ------------------------------------------------------------ 槽
    @pyqtSlot()
    def _on_engine_finished(self):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._on_engine_finished()

    def _reset_buttons(self):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._reset_buttons()

    def cancel_all(self):
        if hasattr(self, "engine") and self.engine is not None:
            self.engine.cancel_all()
        self._emit_log(_fmt_dt(), LogLevel.WARN, "已请求取消全部任务…")
        self.statusBar().showMessage("正在取消…")
        # 由结果槽统一复位

    def _any_cancel(self):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._any_cancel()
    def _hotkey_start_cancel(self):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._hotkey_start_cancel()

    def _hotkey_show_window(self):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._hotkey_show_window()
    def _load_db_into_grid(self):
        """G45-6 转发 manage_panel.py（行为零变化）。"""
        return self.manage_panel._load_db_into_grid()

    def _refresh_manage(self):
        """G45-6 转发 manage_panel.py（行为零变化）。"""
        return self.manage_panel._refresh_manage()

    def _on_manage_double_clicked(self, index):
        """G45-6 转发 manage_panel.py（行为零变化）。"""
        return self.manage_panel._on_manage_double_clicked(index)

    def _on_hist_row_clicked(self, row: int):
        """G45-6 转发 manage_panel.py（行为零变化）。"""
        return self.manage_panel._on_hist_row_clicked(row)

    def _on_hist_btn(self):
        """G45-6 转发 manage_panel.py（行为零变化）。"""
        return self.manage_panel._on_hist_btn()

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
        """G45-6 转发 manage_panel.py（行为零变化）。"""
        return self.manage_panel.delete_selected()
    def batch_tag_selected(self):
        """G45-6 转发 manage_panel.py（行为零变化）。"""
        return self.manage_panel.batch_tag_selected()

    def export_selected_csv(self):
        """G45-6 转发 manage_panel.py（行为零变化）。"""
        return self.manage_panel.export_selected_csv()

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

    # ------------------------------------------------------------ G49-6 启动隐入托盘
    def should_auto_hide(self) -> bool:
        """--minimized 启动且系统托盘已安装 → 主窗不弹出（托盘常驻）。"""
        try:
            return bool(self.start_hidden) and \
                getattr(self.tray, "tray", None) is not None
        except Exception:  # noqa: BLE001
            return False

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
