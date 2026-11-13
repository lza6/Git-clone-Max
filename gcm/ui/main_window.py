"""主窗口：三个 Tab（下载中心 / 仓库管理 / 设置 与 黑匣子日志）。"""
from __future__ import annotations

import os
import threading
import time
import weakref
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QCloseEvent
from PyQt6.QtWidgets import (
    QFileDialog,  # noqa: F401  — 测试 mock 引用（download_tools 经本模块访问）
    QFrame,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QScrollArea,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

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


def _make_alert_emitter(owner: MainWindow):
    """G69-18：构造一个「窗口已销毁则静默跳过」的告警发射器。

    为什么不能直接传 `owner.alerts_fired.emit`：后台扫描线程比窗口活得久时
    （测试里直接丢引用，或 closeEvent 后线程仍在收尾），对**已销毁**的 QObject
    调 `emit` 会让进程**原生崩溃**（SIGSEGV / 0xC0000409），而不是抛 Python 异常
    —— 这正是本轮 CI 连续红灯的形态之一。

    这里用弱引用避免让线程强持有窗口（否则窗口永远无法回收），
    并对「C++ 对象已删除」时 PyQt 抛出的 RuntimeError 兜底。
    """
    import weakref

    # 先建引用再定义闭包：虽然闭包延迟绑定也能跑通，但把依赖藏在
    # 「定义顺序」里太脆弱（以后有人调换两行就无声失效）。
    owner_ref = weakref.ref(owner)

    def _emit(alerts):
        w = owner_ref()
        if w is None:
            return
        try:
            w.alerts_fired.emit(alerts)
        except RuntimeError:
            # wrapped C/C++ object has been deleted —— 窗口已销毁，丢弃即可
            pass

    return _emit


class MainWindow(QMainWindow):
    """Git-clone-Max 主窗口。

    G67-2 线程安全约定（重要）：
    以下信号专供**工作线程**回到 GUI 线程使用。Qt 的自动连接在「发射者线程 ≠
    接收者线程」时会走 QueuedConnection，槽函数因此在 GUI 线程执行 —— 这是唯一
    允许的跨线程更新 UI 的方式。
    **禁止**在工作线程里直接触碰 QWidget / QAbstractItemModel（statusBar()、
    LogModel、QMessageBox 等都有 GUI 线程亲和性，违反后表现为随机崩溃或静默错行）。
    """

    # 归档下载完成（dest, 是否成功, 提示文本）
    archive_finished = pyqtSignal(str, bool, str)
    # 完成通知 Webhook 发送结束（状态栏文案）
    notify_sent = pyqtSignal(str)
    # 网络诊断结束（标题, 正文）—— 由 GUI 线程弹 QMessageBox
    # 诊断/体检结束（标题, 正文, 可外发的 redact 文本）—— 由 GUI 线程弹对话框。
    # G69-18：由 2 参数扩为 3 —— `copy_paste_report()` 生成的打码文本
    # 此前算不出来（该函数只在 tests 里被引用），「复制诊断文本」无从实现。
    diag_finished = pyqtSignal(str, str, str)
    # 手动检查更新结束（has_new, ver, url, err）—— 网络请求在工作线程，弹窗回 GUI 线程
    update_checked = pyqtSignal(bool, str, str, str)
    # G67-9：工作线程日志桥（time, LogLevel, text）—— 线程内只 emit，槽回 GUI 线程
    _log_line = pyqtSignal(str, object, str)
    # G69-18：阈值告警命中（list[dict]）—— AlertScanner 在后台线程扫描，
    # 只 emit；通知/日志/状态栏一律回 GUI 线程执行（QObject 线程亲和性）。
    alerts_fired = pyqtSignal(list)
    # G35-10：Star 列表拉取结束（repos, error）—— 网络在 worker 线程，回填在 GUI 线程。
    starred_fetched = pyqtSignal(list, str)
    # G58-2：镜像测速结束（results, sample_url）—— 探测在 worker 线程。
    mirror_probe_done = pyqtSignal(list, str)

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
        # C4-2：启动时若全文索引与 repos 行数不一致则重建（首次升级后会跑一次）
        try:
            self.db.ensure_fts()
        except Exception as e:      # noqa: BLE001 —— 索引不可用不影响启动（搜索降级 LIKE）
            from ..app.applog import warn as _applog_warn  # 注意别叫 _warn：本文件已有同名闭包
            _applog_warn(f"启动时检查全文索引失败（搜索将降级为 LIKE）：{e}")

        # 统一调度引擎：**构造走 engine_factory（唯一映射点）**。
        # v9.12.0（Rev7 C1-2）：这里原本手写着 20 行参数列表，与 engine_factory
        # 的第二份列表慢慢漂移 —— 实测「改设置重启后控件回填正确、引擎仍是默认值」
        # （host_concurrency 改 11 后引擎仍是 4；time_budget 改 20 分钟后引擎仍是 0）。
        # 合并成一处后不再有第二份真相；`enable_retry_queue=False` 见该函数文档。
        from .engine_factory import build_sync_engine
        self.engine = build_sync_engine(
            self, self.data_dir / "clones",
            single_branch=bool(getattr(self.settings, "single_branch", False)),
            mirror=False,
            enable_retry_queue=False,
        )
        self.engine.line.connect(self._on_engine_line)
        self.engine.progress.connect(self._on_worker_progress)
        self.engine.progress_detail.connect(self._on_worker_progress_detail)
        self.engine.result.connect(self._on_worker_result)
        # Rev14 F2：断点续传跳过的仓库也要把行推到终态（否则永远「等待中」）
        self.engine.skipped.connect(self._on_engine_skipped)
        # Rev16 C4：重投批次是"新批次" —— UI 重建行 + 置回运行态。progress_panel
        # 在 __init__ 后段才创建，这里连 self 的薄转发（转发时 getattr 防护）
        self.engine.retry_batch_started.connect(self._on_retry_batch_started)
        self.engine.finished.connect(self._on_engine_finished)
        self.engine.adapt_changed.connect(self._on_adapt_changed)  # G48-1
        # 兼容旧引用（测试/托盘可能读 pool）
        self.pool = self.engine.pool

        self.tasks: list = []               # list[SyncEngine] 兼容引用（实际由 engine 管理）
        self.flags: dict = {}               # index -> CancelFlag（引擎接管后仅兼容辅助）
        self.row_specs: dict = {}           # index -> RepoSpec
        self.busy = False
        self._close_requested = False
        # G70：管理页仓库列表延迟到该页首次激活再加载（500 仓启动省 ~0.7s）
        self._manage_loaded = False
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
        # G71-9：GUI 内置本地只读 API（含 SSE 事件流）的句柄
        self._local_api_server = None
        self._local_api_thread = None
        self._local_api_port = None

        # G67-2：工作线程 → GUI 线程的信号桥（详见类文档的线程安全约定）。
        # 必须在 _build_ui() 之后连接：槽函数要用到 statusBar 与已建好的控件。
        self.archive_finished.connect(self._on_archive_finished)
        self.notify_sent.connect(self._on_notify_sent)
        self.diag_finished.connect(self._on_diag_finished)
        self.update_checked.connect(self._on_update_checked)
        # G67-9：工作线程日志桥 → GUI 线程写模型/视图（跨线程 emit 自动 QueuedConnection）
        self._log_line.connect(self._emit_log)
        # G69-18：告警命中 → GUI 线程弹通知 + 写日志
        self.alerts_fired.connect(self._on_alerts_fired)
        # G35-10：Star 列表拉取结束 → 回填输入区（网络在 worker 线程）
        self.starred_fetched.connect(self._on_starred_fetched)
        # Rev14 R14-02：屏幕增删 / 主屏切换 → 按新屏重算最小尺寸与布局档
        self._wire_screen_watchers()
        # 系统深浅色变化时，仅 theme=auto 需要重刷（手动主题不受影响）
        self._wire_theme_watcher()

        # G05-1 应用持久化主题（默认 deep）+ G10-1 字号缩放
        from ..ui import theme as _th
        _resolved = _th.resolve_theme_name(getattr(self.settings, "theme", "deep"))
        if _resolved:
            _th.apply_theme(_resolved)
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
        # G70：Ctrl+1/2/3 切页签（用控件属性传参，避免 lambda 捕获 self 形成引用环）
        for _i in range(3):
            _sc = QShortcut(QKeySequence(f"Ctrl+{_i + 1}"), self)
            _sc.setProperty("tab_index", _i)
            _sc.activated.connect(self._switch_tab_shortcut)
        # C2-5：命令面板（40+ 功能分散在菜单/页签/右键三层，这是统一搜索入口）
        QShortcut(QKeySequence("Ctrl+K"), self,
                  activated=self.open_command_palette)
        # G70：恢复上次页签，并在切换时持久化
        try:
            self.tabs.setCurrentIndex(
                max(0, min(2, int(getattr(self.settings, "last_tab", 0) or 0))))
        except Exception:
            pass
        self.tabs.currentChanged.connect(self._save_last_tab)
        self.tabs.currentChanged.connect(self._on_tab_activated)
        # Rev14 R14-03：键盘导航（显式 tab 链 + F6 页签循环）
        from .focus_order import install as _install_focus
        _install_focus(self)
        # 初始页签若是「仓库管理」，立即触发一次延迟加载
        self._on_tab_activated(self.tabs.currentIndex())
        # G02-4 URL 历史：同步成功的地址自动留档（data/history.json）
        from ..db.history import UrlHistory
        self.url_history = UrlHistory(self.data_dir / "history.json")
        # Rev12 U-01：操作台账（data/oplog.json）—— 撤销的唯一事实源
        from ..util.oplog import default_oplog
        self.oplog = default_oplog(self.data_dir)
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
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, tr("Git-clone-Max 启动，数据目录：{v}").format(v=self.data_dir))
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, tr("并行线程：{n}（上限 {m}）").format(n=self.engine.concurrency, m=MAX_CONCURRENCY))
        # G70：仓库列表不在此加载（见 _on_tab_activated / _ensure_manage_loaded）
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
        # G71-9：设置开启时在 GUI 进程内启动本地只读 API（SSE 才能看到真实引擎事件）
        QTimer.singleShot(900, self._apply_local_api_settings)

        # 启动后静默检查更新：不阻塞、不弹窗，有新版本仅写日志 + 托盘提示
        QTimer.singleShot(2500, self._check_update_silent)  # G21-3 启动后静默检查更新
        # G33-6 记录本次主窗口启动耗时（含 UI 构建；自检时展示，>3s 提示较慢）
        self._startup_ms = int((time.perf_counter() - self._startup_start) * 1000)
        # G36-1/G36-9：show() 后单次调度——首次运行向导 + 系统深浅色自适应
        QTimer.singleShot(300, self._maybe_show_onboarding)
        # G54-4：清单**不**在这里定时 —— 向导会开嵌套事件循环（dlg.exec()），
        # 600ms 的定时器会在那个嵌套循环里触发，导致清单叠在向导上面。
        # 改为：向导函数返回后再调用（见 _maybe_show_onboarding 末尾）。
        QTimer.singleShot(500, self._maybe_apply_system_theme)
        # G37-4 自动更新定时器（分钟级；engine 非 busy 时触发一键更新）
        # G83 P1-1：自适应退避状态（连续无变更轮数；None=尚无历史）
        self._auto_update_no_change_streak = None
        self._auto_update_batch_had_changes = False
        self._auto_update_timer = QTimer(self)
        self._auto_update_timer.timeout.connect(self._on_auto_update_tick)
        self._restart_auto_update_timer()

        # G52-2/3：跨会话续跑清单 + 看门狗（数据目录 session_runtime.json）
        from ..db.runtime_sessions import RuntimeSessionStore
        self.session_store = RuntimeSessionStore(self.data_dir / "session_runtime.json")
        from ..app.watchdog import Watchdog
        self.watchdog = Watchdog(
            self.data_dir / "session_runtime.json",
            interval_min=int(getattr(self.settings, "watchdog_interval_min", 5) or 5),
            silent=bool(getattr(self.settings, "watchdog_silent", False)),
            engine=self.engine, parent=self)
        self.watchdog.resume_requested.connect(self._on_watchdog_resume)
        if bool(getattr(self.settings, "watchdog_enabled", False)):
            self.watchdog.start()
        # G59-7：阈值告警后台扫描器（G53-2 落地；开启时启动，支持企业 IM webhook 推送）
        self.alert_scanner = None
        try:
            from ..app.alerts import AlertScanner, scan_alerts

            # G69-18：扫描闭包被 AlertScanner 长期持有 —— 直接捕获 self 同样会
            # 构成引用环（scanner ← self ← scanner）。改用弱引用，窗口销毁后
            # 扫描自然返回空，不再需要 GC 才能在窗口死前回收。
            _self_ref = weakref.ref(self)

            def _scan_repos():
                me = _self_ref()
                if me is None:
                    return []
                repos = me.db.list_repos(host=None) if hasattr(me.db, "list_repos") else []
                return scan_alerts(
                    repos or [],
                    lambda rid: me.db.history(rid, 20) if hasattr(me.db, "history") else [],
                    data_dir=me.data_dir,
                    # 注意：这里也必须走 `me` —— 写成 self 会把 self 重新捕获进闭包，
                    # 环就又回来了（本轮实测抓到的最后一处）。
                    stale_days=int(getattr(me.settings, "g53_alerts_stale_days", 30) or 30),
                    disk_min_gb=int(getattr(me.settings, "g53_alerts_disk_min_gb", 5) or 5))

            self.alert_scanner = AlertScanner(
                _scan_repos,
                interval_sec=max(60.0, float(getattr(self.settings, "g53_alerts_interval_min", 60) or 60)) * 60.0,
                im_channel=str(getattr(self.settings, "alert_im_channel", "") or "").strip(),
                im_url=str(getattr(self.settings, "alert_im_url", "") or "").strip(),
                im_token=str(getattr(self.settings, "alert_im_token", "") or "").strip(),
                # G69-18 修复：此前未接 on_alerts → 告警命中后除（多半未配置的）IM 外
                # 用户**全程无感**：不弹通知、不写日志、UI 无任何痕迹。
                # 现改为「worker emit → 主线程槽」：扫描在后台线程，绝不直接碰控件。
                #
                # 用弱引用 + 存活标志双重保护：扫描线程比窗口活得久时（测试里
                # 直接丢引用、或 closeEvent 之后线程仍在收尾），对**已销毁**的
                # QObject 调 emit 会让进程原生崩溃（SIGSEGV），而不是抛 Python 异常。
                on_alerts=_make_alert_emitter(self))
            if bool(getattr(self.settings, "g53_alerts_enabled", False)):
                self.alert_scanner.start()
        except Exception:
            self.alert_scanner = None

        # G69-18 修复：定时报表此前**整条链路是假的** —— 设置页有「启用定时报表」
        # 勾选框，`ReportScheduler` 却无任何生产实例化点（只在 tests 里被构造）。
        # 勾了等于没勾。现按设置真实启动，并在 closeEvent 里随窗口收敛。
        self.report_scheduler = None
        self._start_report_scheduler()
        # G80-B7-1：登记 4 个后台任务（自动更新/看门狗/告警/报表）的开关与间隔，
        # 供「后台任务」面板展示 —— 它们此前在 UI 上完全不可见。
        try:
            from ..app.background_jobs import register_default_jobs
            register_default_jobs(self.settings)
        except Exception as e:  # noqa: BLE001 - 登记失败不该影响启动
            from ..app import applog as _bj_applog
            _bj_applog.warn(f"后台任务登记失败：{e}")

        # G80-B7-3 / S3：定时同步队列编排（有启用的队列才会真正 tick 起来）
        try:
            self._setup_queue_runner()
        except Exception as e:  # noqa: BLE001 - 队列接线失败不该影响启动
            from ..app import applog as _qr_applog
            _qr_applog.warn(f"定时同步队列接线失败：{e}")

        # 凭据存储强度：`crypto_warning()` 会说明当前环境是否只做了「可逆混淆」。
        # 该函数早已实现却从未被调用 —— 而 README 无条件宣称「token 加密落盘」。
        # 兜底后端（xorfixed）下必须让用户在日志里看得见。
        try:
            from ..db.settings import crypto_warning
            _warn = crypto_warning()
            if _warn:
                self._emit_log(_fmt_dt(), LogLevel.WARN, tr("凭据存储：") + _warn)
        except Exception:
            pass

        # G52-2：启动续跑对话框由 show 后统一调度（_schedule_resume_check），
        # 不在 __init__ 裸调度——避免 closeEvent/drain 泵事件时误弹阻塞对话框。

    def _start_report_scheduler(self) -> None:
        """G69-18：按 settings 启动/重启定时报表调度器（幂等）。

        未设 `sched_report_dir` 时落到数据目录下的 `reports/`，避免
        「开关打开了却写进当前工作目录」这种让用户找不到文件的行为。
        """
        try:
            if self.report_scheduler is not None:
                self.report_scheduler.stop()
                self.report_scheduler = None
            if not bool(getattr(self.settings, "sched_report_enabled", False)):
                return
            from ..app.sched_reports import ReportScheduler
            from ..reports import export_csv, export_markdown
            out = str(getattr(self.settings, "sched_report_dir", "") or "").strip()
            out_dir = Path(out) if out else (self.data_dir / "reports")
            fmt = str(getattr(self.settings, "sched_report_format", "csv") or "csv")

            def _gen(dest: Path, _fmt: str) -> int:
                # 报表函数是**同步**的且会查库；调度器在自己的线程里跑，不碰 GUI。
                if _fmt == "csv":
                    return export_csv(self.db, dest)
                return export_markdown(self.db, dest)

            self.report_scheduler = ReportScheduler(
                _gen,
                interval_min=float(getattr(self.settings, "sched_report_interval_min", 1440) or 1440),
                out_dir=out_dir,
                fmt=fmt)
            self.report_scheduler.start()
            self._emit_log(_fmt_dt(), LogLevel.INFO,
                           tr("定时报表已启用：每 {n} 分钟 → {dir}").format(
                           n=int(getattr(self.settings, 'sched_report_interval_min', 1440) or 1440),
                           dir=out_dir))
        except Exception as exc:  # noqa: BLE001
            # 启动失败要留痕，不能静默 —— 否则又是「勾了没反应」
            self.report_scheduler = None
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("定时报表启动失败：{e}").format(e=exc))

    def _restart_auto_update_timer(self):
        """G83：重排自动更新定时器（自适应间隔）。可能失败点只有 QTimer 已销毁。"""
        # 0（关）必须先 stop —— 原实现无条件 stop，回归测试 test_restart_zero_stops 锁定
        self._auto_update_timer.stop()
        mins = self._current_auto_update_minutes()
        if mins > 0 and self._auto_update_timer is not None:
            self._auto_update_timer.start(mins * 60 * 1000)

    def _current_auto_update_minutes(self) -> int:
        """G83 P1-1：自适应间隔 —— 基础值来自设置，连续无变更轮按 1.5× 退避（封顶 4×）。"""
        from ..app.adaptive_interval import next_interval_minutes
        base = int(getattr(self.settings, "auto_update_minutes", 0) or 0)
        streak = getattr(self, "_auto_update_no_change_streak", None)
        return next_interval_minutes(base, streak)

    def _on_auto_update_tick(self):
        """G37-4 定时触发：engine 非 busy 时一键更新全部（busy 跳过顺延下轮）。"""
        if self.busy:
            self._emit_log(_fmt_dt(), LogLevel.INFO, tr("自动更新跳过：任务运行中"))
            return
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM,
                       tr("自动更新触发（间隔 {m} 分钟，基础 {b}）").format(
                       m=self._current_auto_update_minutes(),
                       b=self.settings.auto_update_minutes))
        # 每轮结束重算间隔：本轮是否有变更由 _note_batch_change 在结果槽中记录
        try:
            self.update_all()
        except Exception as e:
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("自动更新失败：{e}").format(e=e))

    def _note_batch_change(self, had_changes: bool) -> None:
        """G83 P1-1：批次结束按是否有变更更新退避计数并重排定时器。"""
        if not bool(getattr(self.settings, "auto_update_minutes", 0)):
            return  # 功能关闭时不维护退避状态
        if had_changes:
            self._auto_update_no_change_streak = 0
        else:
            self._auto_update_no_change_streak = \
                int(getattr(self, "_auto_update_no_change_streak", 0) or 0) + 1
        self._restart_auto_update_timer()

    # ------------------------------------------------------------ G36-1 首次运行向导
    def _checklist_status(self) -> dict:
        """G54-4：推导引导清单各项完成状态（纯读，不改状态）。"""
        from .checklist import compute_status
        try:
            # G69-18：Database 没有 `count_repos`（只有 `count()`）。
            # 原写法用 hasattr 把「方法名拼错」静默降级成「功能不存在」——
            # 结果「已导入仓库」这项**永远不可能完成**，用户导入 1000 个仓库也不变。
            n = self.db.count() if hasattr(self.db, "count") else 0
        except Exception:
            n = 0
        return compute_status(self.settings, n)

    def _mark_checklist(self, key: str) -> None:
        """把某一项标记完成并持久化（仅 network/help 两项需要显式记）。"""
        try:
            done = dict(getattr(self.settings, "checklist_done", None) or {})
            if done.get(key):
                return
            done[key] = True
            self.settings.checklist_done = done
            self.settings_store.save(self.settings)
        except Exception:
            pass

    def _show_checklist(self) -> bool:
        """G54-4：打开引导清单。返回是否**真的展示过**（供调用方决定要不要记「已提示」）。"""
        if not self._can_show_modal():
            self._emit_log(_fmt_dt(), LogLevel.INFO,
                           tr("引导清单") + "：" + str(self._checklist_status()))
            return False
        try:
            from .checklist import ChecklistDialog
            ChecklistDialog(self._checklist_status(), self).exec()
            return True
        except Exception as exc:  # noqa: BLE001
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("引导清单打开失败：{e}").format(e=exc))
            return False

    def _maybe_show_checklist(self) -> None:
        """**仅在首次**自动提示一次清单；之后用户可从设置页主动打开。

        G69-18 修复：原实现没有任何「已提示过」判断 —— 每次启动 600ms 后都会
        弹一个模态清单挡住用户。现用 `settings.checklist_prompted` 持久化标记。
        """
        if getattr(self.settings, "checklist_prompted", False):
            return
        if os.environ.get("QT_QPA_PLATFORM") == "offscreen":
            return
        if not self._can_show_modal():
            return
        # 先弹，成功后再落标记 —— 否则 `_show_checklist()` 一旦抛异常，
        # 标记已经写死，用户永远看不到清单。
        shown = self._show_checklist()
        if shown is False:
            return
        try:
            self.settings.checklist_prompted = True
            self.settings_store.save(self.settings)
        except Exception:
            pass

    def _maybe_show_onboarding(self):
        """首次运行（settings.first_run_done 未置位）→ 显示三步向导。

        跳过或完成均写 first_run_done；目录/模式回填设置。仅真实窗口展示
        （offscreen/测试环境自动跳过，避免模态阻塞）。
        """
        # G54-4：清单提示放在**提前 return 之前** —— 老用户（已走过向导）
        # 升级后也能看一次；是否真的弹由 `checklist_prompted` 决定。
        # 也正因为它放在最前面，不会落进向导的嵌套事件循环里。
        self._maybe_show_checklist()
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
            # G71-5：优先读语义值（full/shallow）；旧版本/外部构造的对象退回文案判断。
            mode = (getattr(dlg, "selected_mode_value", "")
                    or getattr(dlg, "selected_mode", "") or "")
            if mode == "shallow" or str(mode).startswith(tr("浅")) or \
                    str(mode).lower().startswith("shallow"):
                self.mode_combo.setCurrentIndex(1)
            self.settings_store.save(self.settings)
        except Exception as e:
            self._emit_log(_fmt_dt(), LogLevel.WARN,
                           tr("首次运行向导未完成：{e}").format(e=e))

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
                # 不在这里显式 apply：`_themed_qss()` 会解析 auto 并应用一次，
                # 两处都调会让 `apply_theme` 被调用两次（G36 测试实际抓到）。
                self.setStyleSheet(self._themed_qss())
                self._apply_theme_to_children()
                self._rehighlight_log()
                # 保持 settings.theme="auto"：下次启动仍按系统解析，不能写成 light/deep。
                self._emit_log(_fmt_dt(), LogLevel.INFO,
                               tr("已按系统深浅色切换主题：{t}").format(t=target))
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
                           tr("上次会话有 {n} 项未完成，已回填输入区，可直接继续。").format(n=len(recover)))
        if failed_keys:
            extra = [k for k in failed_keys if k and k not in lines]
            if extra:
                lines.extend(extra)
                self._emit_log(_fmt_dt(), LogLevel.WARN,
                               tr("上次有 {n} 项失败，已一并回填（可重试）。").format(n=len(failed_keys)))
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
        """G36-4 当前窗口几何 → "WxH+X+Y@屏名"（含最大化状态前缀 M:）。

        最大化时**连普通几何的位置一起存**：原实现只写 `M:WxH`（2 个数字），
        而 `_restore_geometry` 无条件按 4 个值解包 → `ValueError` 被 `except` 吞掉
        → 最大化用户每次启动都恢复不了（实测复发）。

        Rev14 R14-02：末尾追加 `@屏名`。位置坐标本身不足以判断"这块屏还在不在"
        —— 用户把副屏拔掉后，同样的坐标可能落在另一块屏上，也可能落在虚空里。
        存名字才能在恢复时明确知道"上次那块屏没了"，进而回主屏而不是硬放。
        """
        try:
            _name = self._current_screen_name()
            _suffix = f"@{_name}" if _name else ""
            if self.isMaximized():
                ng = self.normalGeometry()          # 最大化前的普通几何（含位置）
                return f"M:{ng.width()}x{ng.height()}+{ng.x()}+{ng.y()}{_suffix}"
            g = self.geometry()
            return f"{g.width()}x{g.height()}+{g.x()}+{g.y()}{_suffix}"
        except Exception:
            return ""

    def _screen_contains_point(self, x: int, y: int) -> bool:
        """点 (x, y) 是否落在任一**实际存在**的屏幕工作区内。

        G69-17（用户实际遇到）：`QWidget.move()` **不会**矫正离屏坐标 ——
        实测 `move(-21298, -21183)` 之后窗口真的停在那里。于是只要
        `settings.geometry` 里留下了失效坐标（换过显示器 / 改过分辨率 /
        拔掉过外接屏 / 远程桌面会话尺寸不同），每次启动窗口都会**停在屏幕外**：
        进程活着、任务栏有它，但用户什么也看不到 —— 表现的正是「双击打不开」。
        """
        try:
            from PyQt6.QtCore import QPoint
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is None:
                return True                     # 无 GUI（测试替身）：不做约束
            pt = QPoint(x, y)
            return any(s.availableGeometry().contains(pt) for s in app.screens())
        except Exception:
            return True                          # 判定不了就别乱改用户的位置

    def _restore_geometry(self) -> None:
        """G36-4 从 settings.geometry 恢复窗口几何（尺寸/位置/最大化）。"""
        raw = str(getattr(self.settings, "geometry", "") or "")
        if not raw:
            return
        try:
            maximized = raw.startswith("M:")
            if maximized:
                raw = raw[2:]
            # Rev14 R14-02：几何串尾部可能带 `@屏名`（新格式），先剥掉再解数字
            raw, _sep, _stored_screen = raw.partition("@")
            parts = [int(v) for v in raw.replace("x", " ").replace("+", " ").split()]
            if len(parts) == 2:                 # 旧版 `M:WxH` 格式：只有尺寸
                w, h = parts
                x = y = None
            elif len(parts) >= 4:               # 标准 `WxH+X+Y`
                w, h, x, y = parts[:4]
            else:
                return                          # 格式不认识：保持默认几何
            # G70：按当前屏幕工作区夹取尺寸（小屏/高 DPI 不再溢出屏幕）。
            # offscreen 测试环境的虚拟屏（常为 800×600）不代表真实桌面，
            # 跳过夹取以保证几何往返测试仍验证「存储→恢复」本身。
            from .layout import clamp_window_size
            avail = self._available_screen_size()
            offscreen = str(os.environ.get("QT_QPA_PLATFORM", "")).lower() == "offscreen"
            if avail and not offscreen:
                w, h = clamp_window_size(w, h, *avail)
            else:
                w = max(w, self._base_min_size[0])
                h = max(h, self._base_min_size[1])
            self.resize(w, h)
            # Rev14 R14-02：上次所在的屏已不在（拔屏 / 远程桌面换了会话）
            # → 明确告知并回主屏居中，绝不硬放到那块屏原来的坐标上
            _known = {s["name"] for s in self._screen_snapshot()}
            _screen_gone = bool(_stored_screen) and _stored_screen not in _known
            if _screen_gone:
                self._emit_log(
                    _fmt_dt(), LogLevel.WARN,
                    tr("上次所在的显示器「{s}」已不可用，窗口已回到主屏").format(s=_stored_screen))
                self._center_on_primary_screen(w, h)
            elif x is None or y is None or not self._screen_contains_point(x, y):
                # 位置不可用（旧格式 / 屏已不在）→ **居中显示**，绝不放到用户看不见的地方
                self._center_on_primary_screen(w, h)
            else:
                self.move(x, y)
            if maximized:
                self.showMaximized()
        except Exception:
            pass  # 几何串损坏 → 保持默认

    def _center_on_primary_screen(self, w: int, h: int) -> None:
        """把窗口放到主屏工作区中央（位置不可恢复时的安全落点）。"""
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            screen = app.primaryScreen() if app is not None else None
            if screen is None:
                return
            avail = screen.availableGeometry()
            self.move(avail.x() + max(0, (avail.width() - w) // 2),
                      avail.y() + max(0, (avail.height() - h) // 2))
        except Exception:
            pass

    def _available_screen_size(self):
        """G70：主屏**工作区**尺寸（排除任务栏）；无 GUI 返回 None。"""
        try:
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            screen = app.primaryScreen() if app is not None else None
            if screen is None:
                return None
            g = screen.availableGeometry()
            return (g.width(), g.height())
        except Exception:
            return None

    # ------------------------------------------------------------ Rev14 R14-02 多显示器

    def _screen_snapshot(self) -> list[dict]:
        """当前所有屏幕的「名称 + 工作区」快照（`layout.resolve_screen` 的输入）。

        之所以要抽成快照而不是各处直接问 `primaryScreen()`：拔掉副屏之后
        `primaryScreen()` 依然是主屏，窗口却可能停在**已消失的那块屏**上 ——
        必须按窗口当前所在屏的**名字**去匹配，才能发现"这块屏没了"。
        """
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return []
        out: list[dict] = []
        for s in app.screens():
            g = s.availableGeometry()
            out.append({"name": str(s.name() or ""),
                        "available": (int(g.width()), int(g.height()))})
        return out

    def _current_screen_name(self) -> str:
        """窗口当前所在屏幕的名称（拿不到就空串，调用方按"未知"处理）。

        LBYL 而非 try/except：`screen()` 在控件已构造后必存在，唯一可能是
        没有 `QApplication`（纯逻辑测试）—— 那种情况直接返回空串，不算异常。
        """
        from PyQt6.QtWidgets import QApplication
        s = self.screen()
        if s is None:
            app = QApplication.instance()
            if app is None:
                return ""
            s = app.primaryScreen()
        return str(s.name() or "") if s is not None else ""

    def _wire_screen_watchers(self) -> None:
        """把 `QGuiApplication` 的屏幕信号接到 `_on_screen_changed`。

        三个信号各管一件事：`primaryScreenChanged`（主屏换了）、`screenAdded` /
        `screenRemoved`（插拔）。全部用**绑定方法**连接，不用 `lambda` 捕获 self
        —— 否则引用环会让 `del w` 之后无法按引用计数释放（G69-18 踩过）。
        """
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return
        for _name in ("primaryScreenChanged", "screenAdded", "screenRemoved"):
            _sig = getattr(app, _name, None)
            if _sig is not None:
                _sig.connect(self._on_screen_changed)
        self._screen_watchers_wired = True

    def _wire_theme_watcher(self) -> None:
        """监听 Qt 系统配色变化；只有 `theme="auto"` 的窗口需要重刷。"""
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app is None:
            return
        hints = app.styleHints()
        sig = getattr(hints, "colorSchemeChanged", None)
        if sig is not None:
            sig.connect(self._on_system_color_scheme_changed)
            self._theme_watcher_wired = True

    def _on_system_color_scheme_changed(self, *_args) -> None:
        """系统深浅色变化：auto 模式重刷；手动主题不动。"""
        if getattr(self.settings, "theme", "deep") != "auto":
            return
        self._maybe_apply_system_theme()

    def _on_screen_changed(self, *_args) -> None:
        """屏幕集合变化 → 按新屏重算最小尺寸、夹取窗口、重算布局档。"""
        from .layout import clamp_window_size, minimum_for_screen, resolve_screen
        screens = self._screen_snapshot()
        head = resolve_screen(screens, self._current_screen_name())
        min_w, min_h = minimum_for_screen(screens, head["name"])
        self._base_min_size = (min_w, min_h)
        self.setMinimumSize(min_w, min_h)
        aw, ah = head["available"]
        if aw > 0 and ah > 0:
            w, h = clamp_window_size(self.width(), self.height(), aw, ah)
            if (w, h) != (self.width(), self.height()):
                self.resize(w, h)
        # 尺寸可能变了 → 重跑一次布局判定（折叠 / 档位 / 次要列）
        self._apply_layout_for_width(self.width())
        self._apply_layout_mode()
        self._emit_log(_fmt_dt(), LogLevel.INFO,
                       tr("显示器变更：当前屏幕 {name}，工作区 {aw}×{ah}，最小窗口 {w}×{h}").format(
                       name=head["name"] or tr("未知"), aw=aw, ah=ah, w=min_w, h=min_h))

    def _apply_fold_and_min_heights(self, compact: bool) -> None:
        """Rev13 R13-01/02：矮窗口折叠工具行 + 同步各区块最小高度。

        **折叠**：窗口高度 < `layout.tools_fold_height(字号)` 时，把
        打开目录/失败汇总/重试队列/诊断包 收进一个「更多 ▸」下拉菜单 ——
        实测 900×480 与 1366×768@150% 的 911×512 工作区下，这四个按钮
        可见高只有 27/42px（下半截被父级裁掉）。折叠后入口仍在（同名同槽）。

        **为什么高度也要一起改**：折叠后顶部区 minHint 194 + 进度区 minHint 196
        = 390 > splitter 361，两个区一起被压 →「更多 ▸」自己只剩 6/20px。
        所以折叠态下把可滚动的两处（顶部滚动区、进度表）压到各自的最小需求。
        """
        # Rev13 R13-02：矮窗口把工具行（打开目录/失败汇总/重试队列/诊断包）
        # 折叠进「更多 ▸」——否则四点按钮会被父级裁掉下半截（实测 900×480 与
        # 1366×768@150% 的 911×512 工作区下可见高只有 27/42px）。
        # 判定按**窗口高度 vs 实测需求高度**（见 layout.tools_fold_height），
        # 不按宽度：宽度够也照样放不下高度。
        #
        # 异常策略：这里用 **LBYL（hasattr 守卫）** 而不是 try/except ——
        # `test_g80_hygiene` 的异常预算棘轮只降不升，纯外观修正不该加 except。
        from .download_layout import set_tools_row_folded
        from .layout import tools_fold_height
        if getattr(self, "download_actions", None) is not None:
            _scale = getattr(self.settings, "font_scale", 1.0)
            _fold = int(self.height()) < tools_fold_height(_scale)
            set_tools_row_folded(self, _fold)
            self._tools_folded = _fold
        # v9.12.1 / X-4：动作区不许被压扁 —— 构建时 sizeHint 尚未定型（父级会把孩子
        # 压扁，G78 为 chip 踩过同一个坑），所以每次布局切换按**当前** sizeHint 重设
        # 最小高度；矮窗口里让「可滚动」的顶部区先让位，而不是切掉常驻按钮。
        # 折叠后 sizeHint 自然变小，这里跟着走（不写死两套数字）。
        # Rev16 B3：极矮折叠窗（<560，真机 911×512@150%）物理装不下全部 ——
        # 输入框从 112 压到 96（QPlainTextEdit 内部可滚，地址不丢），让出的
        # 16px 保「更多 ▸」完整可见（四个工具入口在那里）。
        _ri = getattr(self, "repo_input", None)
        if _ri is not None:
            _h0 = int(self.height())
            _ri.setMaximumHeight(96 if (_h0 <= 560 and
                                        bool(getattr(self, "_tools_folded", False)))
                                 else 112)
        _act = getattr(self, "download_actions", None)
        if _act is not None:
            # Rev14 F1：取 `sizeHint` 与 `minimumSizeHint` 的**较大者** —— 折叠后
            # 只按 sizeHint（116）设最小高，「更多 ▸」那第二行就只能分到 10/20px
            # （实测 visibleRegion 86×10）。
            _act.setMinimumHeight(max(int(_act.sizeHint().height()),
                                      int(_act.minimumSizeHint().height())))
        if getattr(self, "download_top_scroll", None) is not None:
            # 顶部区是**可滚动**的，所以它是矮窗口里唯一该让位的东西：
            #   ≥ 标准高度  180（完整露出输入区）
            #   compact     120
            #   极矮(<640)   64（让常驻的动作区完整放得下）
            #   Rev13 折叠态 40 —— 折叠后顶部 minHint 194 + 进度区 minHint 196
            #   = 390 > splitter 361（实测 900×480 / 911×512），必须再让出空间，
            #   否则**第一行**的开始/取消也会被裁。
            _h = int(self.height())
            _folded = bool(getattr(self, "_tools_folded", False))
            if _folded:
                # Rev14 F1：折叠态原来是 40，实测那会让「仓库地址」输入框只剩 10px
                # （滚动视口 40，输入框在内容里 y≈30 处）。
                # 但窗口高度 **< 560** 时（真机 1366×768@150% 的工作区夹出来的 900×504）
                # 地址 + 选项 + 动作 + 进度**四块加起来超过整个可用高度**，物理上装不下：
                # 此时保「更多 ▸」可达（四个工具入口在那里），地址退回可滚动 40 ——
                # 这也是 Rev13 当年在同一尺寸上的取舍，实测一致（累计 408 > 369）。
                # 复审 P1 更正：≥560 的折叠态本批曾误抬到 120，字号 1.3/1.6 极限窗
                # （1020×584@1.3 / 900×480@1.6）的「更多 ▸」被裁到 7~15px —— 折叠态
                # 的方向是**让位**，恢复 Rev15 的 110。
                _min = 110 if _h >= 560 else 40
            else:
                # Rev16 B3：180→150 / 120→96 —— 快捷填充行常驻后顶部固定需求 +42px，
                # 1366/1024 的进度表被压到 2 行。顶部是**可滚动区**，让位符合
                # 「可滚动的先吸收压缩」的既有原则；输入框 80px + 校验条仍放得下。
                _min = 156 if not compact else (102 if _h < 640 else 114)
            self.download_top_scroll.setMinimumHeight(_min)

    def _apply_wide_layout(self, wide: bool) -> None:
        """Rev14 R14-01 / R14-07：按断点档位设置密度（行高）与宽屏专属列宽。

        为什么必须做：`layout_mode()` 一直会返回 `"wide"`，但全库唯一消费点是比较
        `in ("compact", "unsupported")` —— 宽屏与主流办公**行为完全相同**，
        `WIDE_WIDTH/WIDE_HEIGHT` 因此是死常量（Rev13 R4）。这里给它可断言的差异：
        进度表行高 `TOKENS.row.default(26)` → `row.wide(28)`（宽屏视距更远）。
        管理页列宽在 L 档的加宽由 `manage_panel.apply_column_budget` 单独负责
        —— 列宽只有一个所有者，两处各设一遍必然互相打架。

        触摸屏上取 `TOKENS.row.touch(34)` 作为下限 —— 26/28px 的行用指尖点不中。
        判定**排除 offscreen 平台**：Qt 的离屏后端会伪造一个 TouchScreen 设备，
        不排除的话所有测试都会跑在"触摸模式"下（见 `layout.has_touchscreen`）。

        幂等：每次都按当前档位重设，不累加。异常策略用 LBYL（`getattr` 链）。
        """
        from .layout import has_touchscreen
        from .palettes import TOKENS

        # 直接两层下标读（不走 `rows = TOKENS["row"]` 别名）：守卫
        # `tests/test_rev14_feedback.TestTokensHaveNoDeadEntries` 只认直接读取，
        # 别名会让它看不见这处耦合，于是"token 有没有人用"就查不出来了。
        row_h = int(TOKENS["row"]["wide"] if wide else TOKENS["row"]["default"])
        touch = has_touchscreen()
        if touch:
            row_h = max(row_h, int(TOKENS["row"]["touch"]))
        # Rev15 R15-08：触摸屏主操作 44px；桌面鼠标保持 38/42，不无差别拉高。
        _btn_h = int(TOKENS["control"]["touch"]) if touch else 38
        for _name in ("btn_start", "btn_cancel", "btn_pause_batch", "btn_clear",
                      "btn_open", "btn_failures", "btn_retry_queue", "btn_diag_bundle"):
            _btn = getattr(self, _name, None)
            if _btn is not None:
                _btn.setMinimumHeight(_btn_h)
        pp = getattr(self, "progress_panel", None)
        table = getattr(pp, "table", None) if pp is not None else None
        if table is not None:
            vh = table.verticalHeader()
            if vh is not None and vh.defaultSectionSize() != row_h:
                vh.setDefaultSectionSize(row_h)

    def _apply_compact_visibility(self, compact: bool) -> None:
        """Rev16 B3：紧凑/折叠态下的结论条、进度表、总览条与工具行让位。

        从 `_apply_layout_mode` 抽出（该函数超 100 行守卫）。
        """
        _bc = getattr(self, "batch_conclusion", None)
        if _bc is not None:
            _bc.set_compact(compact)
            # Rev14 F1：最矮的窗口（折叠态，<720）连结论条也要让位 ——
            # 实测 1020×584@1.3/1.6 下第一行按钮还差 4–10px，结论条 22px+间距
            # 正好补上。齿条仍显示「成功/失败」计数，信息不会丢；窗口一变大就恢复。
            _bc.setVisible(not bool(getattr(self, "_tools_folded", False)))
        # Rev11：紧凑下进度表 minH 120→94 —— 结论条占了顶部 ~24px 后，
        # splitter 两侧 minHint 之和超过可用高度，会把动作区按钮裁 21px。
        _pp = getattr(self, "progress_panel", None)
        if _pp is not None:
            # Rev13：折叠态下进度表也得再让位。Rev16 B3 再校正：折叠态压到 0 的
            # 代价是 1366/1024/900 只能看到 2 行结果（挂机用户主要盯结果）——
            # 折叠态保 3 行（~94px）；真机 900×504 的极限场景由「更多 ▸」+
            # scroll=40 兜底，这里的 94 只在 ≥560 的折叠窗口生效。
            if bool(getattr(self, "_tools_folded", False)):
                _pp.table.setMinimumHeight(94)
            else:
                _pp.table.setMinimumHeight(94 if compact else 120)
        # Rev14 F1：折叠态再让**信息重复**的条（batch_overview 与结论条同义）
        # 与进度区的「搜索 / 自动排序 / 暂停选中」行（34px 正是「更多 ▸」被压的来源）。
        _folded_now = bool(getattr(self, "_tools_folded", False))
        _bo = getattr(_pp, "batch_overview", None) if _pp is not None else None
        if _bo is not None:
            _bo.setVisible(not _folded_now)
        _tr_w = getattr(_pp, "_tools_row", None) if _pp is not None else None
        if _tr_w is not None:
            _tr_w.setVisible(not _folded_now)

    def _apply_layout_mode(self):
        """G70：Compact 下隐藏快捷填充按钮与设置分组导航（功能仍可手输）。"""
        try:
            from .layout import BREAKPOINT_L, breakpoint_name, is_compact_bp
            _bp = breakpoint_name(self.width(), self.height())
            compact = is_compact_bp(_bp)
            # Rev13：折叠判定 + 各区块最小高度同步（抽成独立方法，防本方法超 100 行）
            self._apply_fold_and_min_heights(compact)
            # Rev14 R14-01：L 档的真实差异化行为（此前 wide 是死枚举）
            self._apply_wide_layout(_bp == BREAKPOINT_L)
            for btn in getattr(self, "_quick_fill_buttons", []):
                btn.setVisible(not compact)
            # G74：Compact 下把 chip 滚动区收起来（把高度还给进度表）。
            # G79b 更正：G74 当时连 `quick_label` 一起隐藏，但「编辑」按钮
            # （`btn_edit_quick`，G76 加的，位于滚动区之外）并不会隐藏 ——
            # 实测 1024×768 截图里该行只剩一个**无标签的孤立「编辑」按钮**，
            # 既不是「整行隐藏」，也让人不知道它在编辑什么。现在保留标签，
            # 紧凑模式下这一行明确读作「快捷填充：［编辑］」，小屏仍够得到该入口。
            # 复审 P2 更正：compact **未折叠**时只隐 chip 滚动区（quick_scroll），
            # 保留「编辑/历史」入口（G79b 的既有契约：快捷填充行在紧凑档
            # 读作「快捷填充：［编辑］」）。折叠态（<560 极矮窗）才整行让位。
            _q_scroll = getattr(self, "quick_scroll", None)
            if _q_scroll is not None:
                _q_scroll.setVisible(not compact)
            _q_host = getattr(self, "_quick_row_host", None)
            if _q_host is not None:
                _q_host.setVisible(
                    not bool(getattr(self, "_tools_folded", False)))
            self._apply_compact_visibility(compact)
            # G78：布局模式/缩放变化后，按 chip 真实高度重算滚动区高度
            # （构建时窗口还没定型，父级会把它压扁 → chip 底部被裁）
            try:
                from .download_layout import sync_quick_scroll_height
                sync_quick_scroll_height(self)
            except Exception as exc:  # noqa: BLE001 —— 纯外观修正，不能拖垮布局切换
                self._emit_log(_fmt_dt(), LogLevel.WARN, tr("快捷填充高度同步失败：{e}").format(e=exc))
            # G74/G84-16：顶部区与进度表的高度分配 —— 抽成独立方法（Rev14 F1）
            try:
                # Rev14 V4：原「连算两次」经实测是空转（两次调用之间没有布局回合，
                # 输入完全相同：_h=442 时第一次 [245,196]→[320,121]，第二次 [320,121]→[320,121]）。
                # 真正的首帧收敛靠 `_layout_timer` 首次 0 延迟，不是靠重复调用。
                self._size_download_splitter(compact)
                if (getattr(self, "_last_compact", None) != compact
                        or getattr(self, "_last_split_fold", None)
                        != getattr(self, "_tools_folded", None)):
                    self._last_compact = compact
                    self._last_split_fold = getattr(self, "_tools_folded", None)
                    # 尺寸变化后把顶部滚回起点，避免只露出「下载位置」下半截
                    try:
                        self.download_top_scroll.verticalScrollBar().setValue(0)
                    except Exception:
                        pass
            except Exception:
                pass
            ws = getattr(self, "settings_workspace", None)
            if ws is not None:
                # 窄窗用下拉替代左侧列表，不再把导航整块隐藏（Rev15 R15-04）。
                ws.set_compact_nav(self.width() < 1000)
            else:
                nav = getattr(self, "settings_nav", None)
                if nav is not None:
                    nav.setVisible(self.width() >= 1000)
        except Exception:
            pass

    def _size_download_splitter(self, compact: bool) -> None:
        """分配「顶部常驻区 / 进度表」的高度（Rev14 F1）。

        **优先级**：常驻区（结论条 + 仓库地址 + 下载位置/克隆模式 + 两行动作）
        先拿满自己的 `minimumSizeHint`；剩余全部给进度表；两者之和超过可用高度时，
        进度表退到 `_pp_floor` —— 它本身就是可滚动区，压它不伤可用性。

        **为什么重写**：原实现在「装不下」时**退回 55% 启发式**，于是常驻区被压到
        测量值 `btn_open 132×16/132×42`、`btn_start 219×30/219×42`（第一行按钮被
        切掉一截）。把「下载位置/克隆模式」搬出滚动区后这个缺口从 ~30px 涨到 ~140px，
        必须改成「常驻区优先」。
        """
        if not hasattr(self, "download_splitter"):
            return
        _h = self.download_splitter.height() or self.height() or 0
        if _h <= 200:
            self.download_splitter.setSizes([380, 380])
            return
        # Rev16 B3：快捷填充行搬进常驻链后，顶部固定需求 +42px。再按 55% 给顶部
        # 会把 1366/1024 的进度表压到 2 行（挂机用户主要看结果）。改为：
        # 顶部按**实际需求**给足，但不再保 240 下限 —— 进度表保底 3 行（~3*28+表头）。
        _top = min(380 if compact else 420, int(_h * 0.48))
        _rest = max(120, _h - _top)
        _tc = self.download_splitter.widget(0)
        if _tc is not None:
            _need = int(_tc.minimumSizeHint().height())
            _pp = getattr(self, "progress_panel", None)
            _pp_need = int(_pp.minimumSizeHint().height()) if _pp is not None else 0
            # 进度表保底：表头 + 3 行（挂机场景至少能看到 3 条结果在动）
            _pp_rows_floor = max(_pp_need, 128)
            if _need + _pp_rows_floor <= _h:
                _rest = max(_pp_rows_floor, _h - max(_top, _need))
                _top = max(0, _h - _rest)
            else:
                _pp_floor = max(48, _pp_need // 3)
                _top = max(0, min(_need, max(0, _h - _pp_floor)))
                _rest = max(0, _h - _top)
        self.download_splitter.setSizes([_top, _rest])

    def _goto_settings_section(self, section: str = "network") -> None:
        """C2-4：切到设置页并滚到对应分组（实现见 `ui/settings_nav.py`）。"""
        from .settings_nav import goto_section
        goto_section(self, section)

    def _scroll_to_settings_group(self, row: int):
        """G70：左侧导航点击 → 滚动到对应设置分组。"""
        try:
            groups = (list(getattr(self, "settings_groups", []))
                      + list(getattr(self, "settings_groups_extra", [])))
            if 0 <= int(row) < len(groups):
                self.settings_scroll.ensureWidgetVisible(groups[int(row)], 20, 20)
        except Exception:
            pass

    def _build_ui(self):
        self.setWindowTitle(tr("Git-clone-Max — GitHub 仓库批量并行下载"))
        # G70：窗口下限改为 1020×560（覆盖 1366×768@125% 与 1080p@150%）
        from .layout import WINDOW_DEFAULT, effective_minimum
        self.resize(*WINDOW_DEFAULT)
        min_w, min_h = effective_minimum(
            *(self._available_screen_size() or WINDOW_DEFAULT))
        # Rev12 U-10：菜单栏占一行（实测 23–30px）。它必须从「可用高度」里扣除，
        # 否则最小尺寸下的主操作区会被挤掉 —— 实测守卫
        # tests/test_ui_compact_visibility 在 1020×560 下报 btn_open 只剩 17/42 像素。
        # 这里**抬高下限**而不是压缩控件：下限提高 24px 对真实屏幕几乎无感，
        # 而压小按钮会直接违反「主操作区必须常驻完整可见」。
        min_h = int(min_h) + 24
        self._base_min_size = (min_w, min_h)
        self.setMinimumSize(min_w, min_h)
        # G36-4 恢复上次窗口几何（尺寸+位置）；无记录则保持默认
        try:
            self._restore_geometry()
        except Exception:
            pass

        central = QWidget()
        # G75：主窗背景走墨/纸渐变（一条渐变，不做噪点纹理：离屏与 DPI 缩放成本高）
        central.setObjectName("rootBg")
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # 顶栏 → G75 仪表齿条：铭牌 + 并行线程 + 活跃/成功/失败/用时（等宽读数）
        from .status_strip import StatusStrip
        motion = not bool(getattr(self.settings, "prefers_reduced_motion", False))
        self.status_strip = StatusStrip(
            self, concurrency=self.engine.concurrency, motion=motion)
        # 兼容：settings_panel._save_concurrency 会直接改 thread_lbl 文案
        self.thread_lbl = self.status_strip.thread_lbl
        root.addWidget(self.status_strip)
        # Rev14 R14-10：断网标记（最近 3 次失败全为网络类才显示，恢复即消）
        from .offline_indicator import FailureStreak, OfflineIndicator
        self.offline_indicator = OfflineIndicator(self.status_strip)
        self.status_strip.add_trailing_widget(self.offline_indicator)
        self._offline_streak = FailureStreak()

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

        # Rev12 U-10/U-11：菜单栏（内容全在 ui/menu_bar.py，此处只挂载）
        try:
            from .menu_bar import build_menu_bar
            build_menu_bar(self)
        except Exception as e:  # noqa: BLE001 —— 菜单栏失败不该让整个窗口起不来
            from ..app.applog import warn as _warn
            _warn(f"菜单栏构建失败（其余功能不受影响）：{e}")
        # Rev12 U-06：撤销浮条。**放在 `tabs` 之下的布局里**（两行：页签 + 浮条），
        # 而不是绝对定位盖在中央控件上 —— 实测覆盖式定位会把下载页底部那排按钮
        # （打开克隆目录 / 失败汇总 / 重试队列 / 诊断包）挡住，可用性改进反而
        # 制造可用性缺陷。放进布局后它隐藏时不占高度（hide() 后 sizeHint 为 0）。
        try:
            from .undo_bar import UndoBar
            self.undo_bar = UndoBar(self, central)
            root.addWidget(self.undo_bar, 0)
            self.undo_bar.hide()
        except Exception as e:  # noqa: BLE001 —— 浮条失败只影响撤销入口
            from ..app.applog import warn as _warn
            _warn(f"撤销浮条创建失败：{e}")

        self.statusBar().showMessage(tr("就绪"))
        # G36-5 首次显示空态 overlay（表初始为空）
        # Rev14 R14-09：三态判定收敛到 `manage_panel._update_empty_manage`（唯一来源）
        try:
            self._update_empty_download()
            self.manage_panel._update_empty_manage()
        except Exception:
            pass
        # Rev14 R14-04：无障碍命名（主操作显式命名 + 输入控件按标签推导）。
        # 放在最后：所有控件都已建好，推导才有标签可找。
        self._apply_a11y_names()
        # Rev14 V4：构建末尾先跑一遍布局档 —— 否则 `show()` 后 150ms 的去抖重算
        # 会**第二次**改最小高度/splitter 尺寸，用户能在首帧后看到 31 个控件被挪动
        # （实测 first_frame_moves=31，目标 0）。这里先收敛，去抖那趟就变成空操作。
        self._apply_layout_mode()
        self._apply_layout_for_width(self.width())

    def _apply_a11y_names(self) -> None:
        """Rev14 R14-04：给主操作与输入控件补无障碍名字。

        实测缺口（Rev14 基线）：14 个主操作里 11 个没有 `accessibleName`；
        `QLineEdit` 34/36、`QSpinBox` 19/19、`QComboBox` 8/8 没有名字 ——
        这些控件没有可见文本，读屏只会报「编辑框」，等于功能不可用。

        只在**缺失**时补，已有人工命名不覆盖（优先级见 `ui/a11y.py`）。
        不包 `try/except`：两个函数只做 `QWidget` 属性赋值，没有 I/O 与失败路径；
        异常预算棘轮只降不升，可访问性增强不该占额度。
        """
        from .a11y import apply_main_action_names, autoname_inputs
        self._a11y_named = (apply_main_action_names(self), autoname_inputs(self))

    # ---------------- 下载中心
    def _build_download_tab(self):
        v = QVBoxLayout(self.tab_download)
        v.setSpacing(10)

        # G71-7：顶部区域控件搭建已抽到 download_layout.build_download_top
        # （长函数治理；所有控件仍挂在本窗口，外部引用不变）
        # Rev14 F1/F7：下载位置/克隆模式从滚动区搬到常驻区（实测小屏被切到 6px）
        from .download_layout import (
            build_download_actions,
            build_download_options,
            build_download_top,
        )
        top = build_download_top(self)
        options = build_download_options(self)
        actions = build_download_actions(self)

        # G45-6：进度表/空态/过滤框/暂停 收敛到 progress_table.py（ProgressTable）
        from .progress_table import ProgressTable
        self.progress_panel = ProgressTable(owner=self, parent=self.tab_download)
        # Rev11（V-09）：批次结论条 —— 全屏唯一 display 字号，插在齿条与滚动区之间。
        # 数据来源：运行态/完成态由 progress_table 的批次信号驱动；就绪态由 start_all
        # 在引擎起跑前调 set_ready（R-01，数据来自 plan_batch）。
        from .batch_conclusion import BatchConclusionBar
        self.batch_conclusion = BatchConclusionBar(
            motion=bool(getattr(self.settings, "prefers_reduced_motion", False) is False))
        self.batch_conclusion.review_btn.clicked.connect(self._open_failure_dialog)
        # G70：顶部区域放入可滚动容器 —— 小屏（1366×768@125% / 1080p@150%）下
        # 输入区不会被父容器裁切，用户滚动即可访问全部控件。
        self.download_top_scroll = QScrollArea()
        self.download_top_scroll.setWidgetResizable(True)
        self.download_top_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.download_top_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.download_top_scroll.setWidget(top)
        self.download_top_scroll.setMinimumHeight(180)
        # G74：主操作区固定在滚动区之外 —— 任何分辨率都能看到「开始/取消」
        # v9.12.1 / X-4：动作区（清单行 + 开始/取消 + 工具行）必须常驻且不被压扁，
        # 保存引用供 `_apply_layout_mode` 在每次布局切换后按真实 sizeHint 重设最小高度。
        self.download_actions = actions
        # Rev14 F1/F7：下载位置/克隆模式常驻（与主操作同级的最高频操作）
        self.download_options = options
        top_container = QWidget()
        tc = QVBoxLayout(top_container)
        tc.setContentsMargins(0, 0, 0, 0)
        tc.setSpacing(6)
        tc.addWidget(self.batch_conclusion)
        # Rev16 B3：快捷填充行常驻（原来在 download_top_scroll 里，1366/1024/900
        # 首屏不可见）。放在结论条之后、滚动区之前 —— 一行 42px，高频入口全程可达。
        _quick_host = getattr(self, "_quick_row_host", None)
        if _quick_host is not None:
            tc.addWidget(_quick_host)
        tc.addWidget(self.download_top_scroll, 1)
        tc.addWidget(options)
        tc.addWidget(actions)
        self.download_splitter = QSplitter(Qt.Orientation.Vertical)
        self.download_splitter.setChildrenCollapsible(False)
        self.download_splitter.addWidget(top_container)
        self.download_splitter.addWidget(self.progress_panel)
        self.download_splitter.setStretchFactor(0, 0)
        self.download_splitter.setStretchFactor(1, 1)
        self.download_splitter.setSizes([420, 380])
        v.addWidget(self.download_splitter, 1)
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
            self.mirror_probe_done.connect(self._on_mirror_probe_done)
        """G33-8 拆分：设置页控件构建委托给 settings_panel.py / log_buffer.py。

        控件名（self.spin_concurrency / self.log_view 等）仍挂载在本窗口，
        测试与既有引用零变化；此处只做布局组装。
        """
        v = QVBoxLayout(self.tab_settings)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(10)

        # 设置区：设置滚动区 + 日志区放进可调 QSplitter（Rev15 R15-03）
        from .settings_panel import build_settings_ui
        from .settings_workspace import build_settings_workspace
        self.settings_scroll = build_settings_ui(self)
        self.settings_workspace = build_settings_workspace(self, self.settings_scroll)
        v.addWidget(self.settings_workspace, 1)

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
            lines.append(tr("git：未检测到（请安装 Git 后重启应用）"))
        else:
            lines.append(f"git：{git_path}")
            try:
                proc = subprocess.run(
                    ["git", "--version"],
                    capture_output=True, text=True, timeout=10,
                    encoding="utf-8", errors="replace")
                out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
                lines.append(tr("版本：{v}").format(v=out or tr("未知")))
            except Exception as e:
                lines.append(tr("版本：读取失败（{e}）").format(e=e))
        from PyQt6.QtCore import PYQT_VERSION_STR
        lines.append(f"PyQt6：{PYQT_VERSION_STR}")
        lines.append(tr("数据目录：{v}").format(v=self.data_dir))
        lines.append(tr("当前并发：{n}（上限 {m}）").format(n=self.engine.concurrency, m=MAX_CONCURRENCY))
        # C3-4：分阶段耗时累计（回答"这些批次慢在传输还是别处"）
        try:
            totals = self.db.stage_totals() if hasattr(self.db, "stage_totals") else {}
        except Exception:       # noqa: BLE001 —— 诊断附带项，取不到就跳过
            totals = {}
        if totals:
            shown = " · ".join(f"{k} {v / 1000:.1f}s" for k, v in
                               sorted(totals.items(), key=lambda kv: -kv[1]))
            lines.append(tr("阶段耗时累计：{v}").format(v=shown))
        writable = tr("是") if os.access(self.data_dir, os.W_OK) else tr("否")
        lines.append(tr("数据目录可写：{v}").format(v=writable))
        # G33-6 启动耗时：构造完成即记录；>3s 标注较慢（exe 解包/低配机器场景）
        startup_ms = getattr(self, "_startup_ms", 0)
        slow_note = tr("（启动偏慢，可考虑使用便携版）") if startup_ms > 3000 else ""
        lines.append(tr("启动耗时：{ms} ms{note}").format(ms=startup_ms, note=slow_note))
        QMessageBox.information(self, tr("环境自检"), "\n".join(lines))
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, tr("环境自检完成：{v}").format(v=" / ".join(lines)))
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

        G67-9 线程约定（**必须遵守**）：
        - `log`（QAbstractItemModel）与 `log_count`（QLabel）都是 GUI 对象，
          只在 GUI 线程触碰。
        - 工作线程**不得**直接调本方法 —— 会命中 `TestNoCrossThreadWidgetAccess`
          的 AST 守卫。工作线程请调 `emit_log_from_worker()`。
        - 本方法内部不再做线程判断：调用点已由守卫强制分岔，运行时检查是多余的
          复杂度（且 if 判断本身会让 AST 守卫看不懂而误放行）。
        """
        self._ensure_log_buffer()
        self.log.append(time, level, text)
        # 节流：把待渲染条目并入批量，由 LogBuffer 定时器统一 flush
        from .theme import LogEvent as _LE
        self._log_buffer.append(_LE(time, level, text))

    def emit_log_from_worker(self, time: str, level: LogLevel, text: str) -> None:
        """G67-9：**供工作线程调用**的日志入口（唯一被允许的跨线程日志方式）。

        只 `emit` 信号，不触碰任何控件；`_log_line` 连到 `_emit_log`，Qt 自动
        判定为 QueuedConnection → 槽在 GUI 线程执行。
        """
        self._log_line.emit(time, level, text)

    def _flush_log_batch(self):
        """兼容入口：刷日志缓冲（历史测试/closeEvent 引用）。"""
        buf = getattr(self, "_log_buffer", None)
        if buf is not None:
            buf.flush()

    @pyqtSlot(int)
    def _on_log_appended(self, count):
        self.log_count.setText(tr("{n} 条").replace("{n}", str(count)))

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

    def _save_mirror_fallback(self, *args, **kwargs):
        """G58-2 转发：镜像失败回退源站开关。"""
        return self.settings_panel._save_mirror_fallback(*args, **kwargs)

    def _save_mirror_timeout(self, *args, **kwargs):
        """G58-2 转发：镜像探测超时。"""
        return self.settings_panel._save_mirror_timeout(*args, **kwargs)

    def _probe_mirrors(self, *args, **kwargs):
        """G58-2 转发：测速并排序（worker 线程）。"""
        return self.settings_panel._probe_mirrors(*args, **kwargs)

    def _on_mirror_probe_done(self, results, sample):
        """G58-2：测速结束槽（GUI 线程）→ 设置面板回填。"""
        return self.settings_panel._on_mirror_probe_done(results, sample)

    def _save_custom_hosts(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_custom_hosts(*args, **kwargs)

    def _save_auth_header_type(self, *args, **kwargs):
        """G58-5 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_auth_header_type(*args, **kwargs)

    def _save_ssh_key(self, *args, **kwargs):
        """G67-16 转发 settings_panel.py（G48-3 SSH key 接线）。"""
        return self.settings_panel._save_ssh_key(*args, **kwargs)

    def _pick_ssh_key(self, *args, **kwargs):
        """G67-16 转发 settings_panel.py（浏览选择私钥）。"""
        return self.settings_panel._pick_ssh_key(*args, **kwargs)

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
            self._emit_log(_fmt_dt(), LogLevel.INFO, tr("剪贴板检测到仓库地址：{u}（已填入输入框）").format(u=url))
            self.statusBar().showMessage(tr("检测到仓库地址：{u}").format(u=url), 5000)
        except Exception:
            pass

    def _save_theme(self, *args, **kwargs):
        """G45-6 转发 settings_panel.py（行为零变化）。"""
        return self.settings_panel._save_theme(*args, **kwargs)

    def _switch_tab_shortcut(self):
        """G70：Ctrl+1/2/3 切页签（发送者属性携带目标索引）。"""
        try:
            sender = self.sender()
            idx = int(sender.property("tab_index")) if sender is not None else 0
            self.tabs.setCurrentIndex(max(0, min(self.tabs.count() - 1, idx)))
        except Exception:
            pass

    def _save_last_tab(self, index: int):
        """G70：记住最后使用的页签（内存即时生效，落盘 500ms 防抖）。

        每次切页签都同步写 settings.json 会在跨进程锁竞争时卡 UI；
        关闭窗口时 closeEvent 仍会做最终保存，不会丢。
        """
        try:
            self.settings.last_tab = int(index)
        except Exception:
            return
        if not hasattr(self, "_tab_save_timer"):
            self._tab_save_timer = QTimer(self)
            self._tab_save_timer.setSingleShot(True)
            self._tab_save_timer.setInterval(500)
            self._tab_save_timer.timeout.connect(self._persist_last_tab)
        self._tab_save_timer.start()

    def _persist_last_tab(self):
        try:
            self.settings_store.save(self.settings)
        except Exception:
            pass

    def _on_tab_activated(self, index: int):
        """G70：首次进入管理页加载仓库；首次进入设置页回填开机自启状态。

        Rev14 R14-03：切页签时应用该页的 tab 链（不同页的可聚焦控件集合不同，
        链必须在切换时重设，否则 `Tab` 会走回上一页的旧顺序）。
        """
        try:
            index = int(index)
            if index == 1:
                self._ensure_manage_loaded()
            elif index == 2 and not getattr(self, "_autostart_state_loaded", False):
                self._refresh_autostart_state()
        except Exception:
            pass
        from .focus_order import apply_tab_order
        apply_tab_order(self, index)

    def cycle_tab_focus(self) -> None:
        """`F6`：在三页签之间循环并把焦点交给新页的第一个可聚焦控件（Rev14 R14-03）。

        为什么不是简单地 `setCurrentIndex(+1)`：只切页不给焦点，键盘用户按完 `F6`
        还在原来那一页的控件上，等于"切了但看不出切了"。这里切完页立刻
        `setFocus()` 到该页链头。
        """
        from .focus_order import chain_widgets
        total = self.tabs.count()
        if total <= 0:
            return
        nxt = (self.tabs.currentIndex() + 1) % total
        self.tabs.setCurrentIndex(nxt)
        widgets = chain_widgets(self, nxt)
        if widgets:
            widgets[0].setFocus()

    def _refresh_autostart_state(self):
        """G70：延迟读取任务计划状态（避免启动时同步跑 schtasks）。"""
        try:
            from ..app.autostart import autostart_enabled
            checked = bool(autostart_enabled())
        except Exception:
            return
        cb = getattr(self, "ck_autostart", None)
        if cb is None:
            return
        cb.blockSignals(True)
        try:
            cb.setChecked(checked)
        finally:
            cb.blockSignals(False)
        self._autostart_state_loaded = True

    def _ensure_manage_loaded(self):
        """G70：按需加载管理页模型（显式调用可重复执行）。"""
        if not getattr(self, "_manage_loaded", False):
            self._load_db_into_grid()
            self._manage_loaded = True

    def _themed_qss(self) -> str:
        """G46-7/G46-8：按当前主题 + 强调色 + 字号 + 动效偏好生成 QSS。"""
        from ..ui import theme as _th
        # G75：主题/强调色同步进模块状态 —— 自绘 delegate、日志高亮、托盘图标都读它，
        # 否则会出现"QSS 一套颜色、自绘另一套颜色"的双真相（G75 实测过）。
        theme_name = getattr(self.settings, "theme", "deep")
        # auto 必须先解析成实际主题；直接把 "auto" 交给 apply_theme 会退化成 deep。
        resolved_theme = _th.resolve_theme_name(theme_name)
        if resolved_theme in _th.PALETTES:
            _th.apply_theme(resolved_theme)
        _th.apply_accent_preset(
            getattr(self.settings, "accent_preset", _th.DEFAULT_ACCENT_PRESET))
        pal = _th.current_palette()
        motion = not bool(getattr(self.settings, "prefers_reduced_motion", False))
        qss = _th.qss_for_palette(pal, getattr(self.settings, "font_scale", 1.0), motion)
        # G75：齿条自己画的失败色来自色板，主题切换后必须重取一次
        strip = getattr(self, "status_strip", None)
        if strip is not None:
            strip.set_motion(motion)
            strip.refresh()
        return qss

    def _apply_font_scale(self):
        try:
            self.setStyleSheet(self._themed_qss())
        except Exception:
            pass
        # Rev13 R13-02：折叠阈值依赖字号（580/600/620 对应 1.0/1.3/1.6）——
        # 只重刷 QSS 不会重算布局，字号调大后工具行会**少算**所需高度而被裁。
        # 这里补一次布局重算；无 QApplication / 控件未建时由 _apply_layout_mode
        # 自身的 getattr 守卫静默跳过。
        apply_mode = getattr(self, "_apply_layout_mode", None)
        if callable(apply_mode):
            apply_mode()

    def _rehighlight_log(self):
        """G70：主题/强调色切换后重着色**存量**日志行。

        `QSyntaxHighlighter.highlightBlock` 只在文档变化时被调用；仅读
        `current_palette()` 不会让已有行变色，必须显式 `rehighlight()`。
        """
        try:
            hl = getattr(self, "log_highlighter", None)
            if hl is not None:
                hl.rehighlight()
        except Exception:
            pass

    def zoom_in(self):
        self.settings.font_scale = round(min(1.6, self.settings.font_scale + 0.1), 2)
        self.settings_store.save(self.settings)
        self._apply_font_scale()

    def zoom_out(self):
        # Rev13 R13-02：下限改用 UI 层常量 `FONT_SCALE_MIN_UI`（0.9）。
        # 原来写死 0.8，而 `typography.FONT_SCALE_MIN_UI = 0.9` 声明「UI 层字号下限」
        # 全库零强制 —— 两套口径并存，`Ctrl+-` 能一路压到 0.8，实测 0.8 字号下
        # 紧凑窗口的主操作按钮几乎全裁。现在只保留一个口径。
        from .typography import FONT_SCALE_MIN_UI
        self.settings.font_scale = round(
            max(FONT_SCALE_MIN_UI, self.settings.font_scale - 0.1), 2)
        self.settings_store.save(self.settings)
        self._apply_font_scale()

    def zoom_reset(self):
        self.settings.font_scale = 1.0
        self.settings_store.save(self.settings)
        self._apply_font_scale()

    def _apply_theme_to_children(self):
        """把**当前主题**的 QSS 应用到弹出的对话框（详情/统计等）。

        G70 修复：原实现套的是导入时生成的静态 `theme.QSS`（永远 Deep/1.0/动效开），
        切到 Light/Nord/HC 后打开对话框会变回深色并丢字号缩放。
        """
        try:
            qss = self._themed_qss()
        except Exception:
            return
        for child in self.findChildren(object):
            try:
                if child.__class__.__name__.endswith(("Dialog", "MainWindow")):
                    child.setStyleSheet(qss)
            except Exception:
                pass

    # ------------------------------------------------------------ 更新检查
    def _save_retry_backoff(self, value):
        """G80-B5：退避基数保存回调（写入侧此前不存在）。

        与 `_save_concurrency` 同族：先写 settings、再存盘、再尽量推送给运行中的引擎。
        """
        self.settings.retry_backoff_base_sec = int(value)
        self.settings_store.save(self.settings)
        try:
            eng = getattr(self, "engine", None)
            q = getattr(eng, "retry_queue", None)
            if q is not None and hasattr(q, "set_backoff_base_sec"):
                q.set_backoff_base_sec(int(value))
        except Exception as e:  # noqa: BLE001 - 推送失败不影响已保存的配置
            from ..app.applog import warn as _warn
            _warn(f"退避基数推送到引擎失败（已保存到配置）：{e}")

    def check_update_now(self):
        """手动检查更新（实现在 `update_flow.check_now`，本方法只转调）。"""
        from .update_flow import check_now
        return check_now(self)

    def _setup_queue_runner(self) -> None:
        """G80-B7-3 / S3：定时同步队列的编排接线（复用 _launch，不改引擎）。

        QTimer 每 60s tick 一次：到点且引擎空闲 → 按队列 specs 启动批次并 mark_run。
        引擎忙 → 整轮让路（手动批次优先）。app 层不 import Qt（B6 边界），
        所以 QTimer 在这里创建、`attach_timer` 交给 runner。
        """
        # 引用环防线（G69-18）：runner 回调一律经**弱引用**包装 —— 直接闭包 self
        # 会形成 MainWindow→runner→闭包→MainWindow 的环（守卫会红，实测抓到）。
        import weakref as _weakref

        from PyQt6.QtCore import QTimer

        from ..app.queue_runner import QueueRunner

        self_ref = _weakref.ref(self)

        def _launch_specs(specs) -> int:
            """把队列 specs 启动成一批（复用主窗体的既有批次链）。

            命名说明：方法名刻意避开主窗体同名方法 —— 生命周期守卫用字符串锚点
            定位那个方法体做顺序断言，局部函数/注释里出现同样的前缀会把
            扫描锚点抢走（G80 实测踩到，docstring 里的反引号也算）。
            """
            win = self_ref()
            if win is None:
                return 0
            from gcm.models import RepoSpec
            parsed = [RepoSpec(**dict(x)) for x in specs]
            return win._launch(parsed, target_root=win.target_edit.text().strip()
                               or str(win.data_dir / "clones"), shallow=False,
                               depth=win.depth_spin.value(), clear_input=False)

        self._queue_runner = QueueRunner(
            store=self.db.queue,
            get_settings=lambda: (self_ref() or type("S", (), {"settings": None})()).settings,
            launch=_launch_specs,
            is_busy=lambda: bool(getattr(self_ref() or object(), "busy", True)),
        )
        timer = QTimer(self)
        timer.setInterval(60_000)
        timer.timeout.connect(self._queue_runner_tick)
        self._queue_runner.attach_timer(timer)
        self._queue_runner.set_enabled(True)

    def _queue_runner_tick(self) -> None:
        """QTimer 槽：一次队列检查（异常不外泄，定时器不能死）。"""
        try:
            self._queue_runner.tick()
        except Exception as e:  # noqa: BLE001 - 定时器槽不能把线程带崩
            from ..app.applog import warn as _warn
            _warn(f"定时队列 tick 失败：{e}")

    def show_background_jobs(self):
        """G80-B7-1：打开「后台任务」面板（4 个定时器的 next-run / 上次结果）。"""
        from .background_jobs_panel import BackgroundJobsDialog
        dlg = BackgroundJobsDialog(self, parent=self)
        dlg.exec()

    def verify_downloaded_file(self):
        """G80-B4-2：校验下载到的安装包（实现在 `update_flow.verify_downloaded_file`）。"""
        from .update_flow import verify_downloaded_file
        return verify_downloaded_file(self)

    def _can_show_modal(self) -> bool:
        """当前是否适合弹**模态**对话框（G68-4）。

        模态窗（`QMessageBox.exec()`）必须有人点击才会返回。在没有交互界面的环境
        （offscreen / CI / 已关闭的主窗）里弹模态会**永久阻塞**整个进程 ——
        实测过一次真事故：一个**迟到的** `update_checked` 跨线程信号在
        「窗口已 close()」之后才被派发，`_on_update_checked` 里真实 `QMessageBox.question()`
        把测试进程挂死 7 分钟以上（faulthandler 栈：`main_window.py:930 ← _pump`）。

        判据用 `isVisible()`：窗口已关闭 / 从未显示 → 不适合弹窗。
        **交互环境不受影响**（用户能看见的窗口照常弹），测试里的 mock 断言也不受影响
        （它们在窗口可见时执行）。
        """
        try:
            return bool(self.isVisible())
        except Exception:
            return False

    def _on_update_checked(self, has_new: bool, ver: str, url: str, err: str,
                           sha: str | None = None):
        """`update_checked` 槽（实现在 `update_flow.on_checked`，本方法只转调）。

        保留同名方法：既有测试（`test_update_auto` / `test_g68_settings_ui`）
        直接调用它，改名会静默失去这些覆盖。
        """
        from .update_flow import on_checked
        return on_checked(self, has_new, ver, url, err, sha)

    def _check_update_silent(self):
        """启动后静默检查更新（实现在 `update_flow.check_silent`）。"""
        from .update_flow import check_silent
        return check_silent(self)

    def save_log(self):
        from .download_tools import save_log as _sl
        _sl(self, _fmt_dt)

    # ------------------------------------------------------------ 动作
    def _repo_input_menu(self, pos):
        """输入框右键菜单：历史回填 / 清空 / G48-7 归档下载。"""
        from .download_tools import repo_input_menu as _rim

        def _extra(menu):
            act = menu.addAction(tr("归档下载（zip，免入库）"))
            act.triggered.connect(self._archive_current_line)   # G69-18：绑定方法
        _rim(self, pos, _fmt_dt, extra_actions=_extra)

    def _archive_current_line(self, _checked: bool = False):
        """G48-7：对输入区光标所在行地址执行归档下载到 data/archives/。

        G67-2：网络下载在后台线程，但**结果回传必须经信号回到 GUI 线程** ——
        原实现在工作线程里直接调 `_emit_log`（QAbstractItemModel）与
        `statusBar().showMessage()`（QWidget），二者都有 GUI 线程亲和性。
        """
        text = self.repo_input.toPlainText()
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if not lines:
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("输入区为空，无法归档下载。"))
            return
        # G67-2 附带修复：原实现固定取 lines[0]，与右键菜单文案「光标所在行」不符。
        cursor = self.repo_input.textCursor()
        idx = max(0, min(cursor.blockNumber(), len(lines) - 1))
        url = lines[idx]
        try:
            from gcm.git.archive import download_archive
        except Exception:
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("归档模块不可用。"))
            return
        from gcm.app.url_lib import sanitize_name
        dest_dir = self.data_dir / "archives"
        # G67-2 附带修复：原实现用 URL 末段直接拼文件名（未清洗）→ Windows 非法
        # 字符/超长名会抛 OSError，`..` 等还会落成怪异路径。
        stem = sanitize_name(url.rstrip("/").split("/")[-1].removesuffix(".git"))
        dest = dest_dir / f"{stem}.zip"
        dest_dir.mkdir(parents=True, exist_ok=True)

        def _run():
            try:
                download_archive(url, dest)
                # 跨线程：只允许 emit，不得触碰任何 QWidget/QAbstractItemModel
                self.archive_finished.emit(str(dest), True, tr("归档下载完成：{d}").format(d=dest))
            except Exception as e:
                self.archive_finished.emit(str(dest), False, tr("归档下载失败：{e}").format(e=e))

        import threading
        threading.Thread(target=_run, daemon=True).start()
        self.statusBar().showMessage(tr("归档下载中…"))

    def _on_archive_finished(self, dest: str, ok: bool, message: str):
        """G67-2：归档完成回主线程（GUI 线程）后更新日志与状态栏。"""
        self._emit_log(_fmt_dt(), LogLevel.INFO if ok else LogLevel.WARN, message)
        self.statusBar().showMessage(tr("归档已保存：{d}").format(d=dest) if ok else message)

    def _on_notify_sent(self, message: str):
        """G67-2：完成通知 Webhook 发送结果回主线程后更新状态栏。"""
        self.statusBar().showMessage(message, 5000)

    def _on_alerts_fired(self, alerts):
        """G69-18：阈值告警命中 → 状态栏 + 日志（GUI 线程）。

        修的是「告警引擎跑得挺好，但用户永远看不到」：此前 `AlertScanner`
        没接 `on_alerts`，命中后除（多半未配置的）企业 IM 外无任何出口。
        这里只写日志与状态栏，**不弹模态** —— 告警是后台周期性产物，
        在用户正忙时弹窗打断属负体验（详情可去统计中心/日志查看）。
        """
        if not alerts:
            return
        try:
            titles = [str(a.get("title") or a.get("key") or "")
                      for a in alerts if isinstance(a, dict)]
            summary = "；".join(t for t in titles if t) or f"{len(alerts)} 条"
            self.statusBar().showMessage(tr("阈警：{s}").format(s=summary), 8000)
            self._emit_log(_fmt_dt(), LogLevel.WARN,
                           tr("阈值告警命中 {n} 条：{s}").format(n=len(alerts), s=summary))
        except Exception as exc:  # noqa: BLE001
            # 告警渲染失败绝不能连带影响扫描线程的收尾；
            # 但**不能静默** —— 否则整批告警无声无息，比不显示更难查。
            try:
                self._emit_log(_fmt_dt(), LogLevel.WARN,
                               tr("告警渲染失败（{t}），已跳过本批").format(t=type(exc).__name__))
            except Exception:
                pass

    def _on_diag_finished(self, title: str, body: str, copy_text: str = ""):
        """G67-2 / G69-18：诊断结果回主线程后再弹**报告对话框**。

        原实现在工作线程里 `QMessageBox.information(...)` —— 在工作线程创建并
        exec 模态窗口是 Qt 明确禁止的用法。

        G68-4：窗口已关闭（诊断跑完前人把窗口关了）时不弹模态 —— 无交互界面下
        `exec()` 会永久阻塞进程（见 `_can_show_modal`）。

        G69-18：从只读 `QMessageBox` 换成 `DiagReportDialog` ——
        后者带「复制诊断文本」按钮（G54-5），且用的是已经 redact 过的
        `copy_text`，用户报障时可直接外发。
        """
        # G54-4：跑过一次体检即视为该项完成（清单会实时反映）
        self._mark_checklist("network")
        if not self._can_show_modal():
            try:
                self._emit_log(_fmt_dt(), LogLevel.INFO, f"{title}：{body}")
            except Exception:
                pass
            return
        try:
            from .diag_dialog import DiagReportDialog
            # G71-6：带上数据目录与事件总线，报告对话框可直接导出诊断包
            try:
                from ..app.event_bus import get_event_bus
                _bus = get_event_bus()
            except Exception:
                _bus = None
            DiagReportDialog(title, body, copy_text, self,
                             data_dir=self.data_dir, event_bus=_bus).exec()
        except Exception as exc:  # noqa: BLE001
            # 对话框起不来不能把诊断结果一起吞掉 —— 退化为纯文本
            from PyQt6.QtWidgets import QMessageBox
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("诊断对话框打开失败：{e}").format(e=exc))
            QMessageBox.information(self, title, body)

    def _apply_ssh_key_to_engine(self):
        """G67-16：把设置里的 SSH 私钥路径立即同步到运行中的引擎。

        G48-3 原先只有 service 侧支持（`GIT_SSH_COMMAND`），settings/UI 都没有该字段，
        整条链路恒为空 → 功能实际不可达。接线后改设置需即时生效，不必重启。
        """
        key = str(getattr(self.settings, "ssh_key_path", "") or "").strip()
        eng = getattr(self, "engine", None)
        if eng is not None:
            eng._ssh_key_global = key  # noqa: SLF001 - 引擎按批次读该字段
        return key

    def _show_notify_audit(self) -> None:
        """G67-6：查看通知审计台账（`<data>/notify.log`，字段已打码）。

        该台账记录每一次发送尝试：成功 / 失败 / 因幂等被跳过，以及尝试次数。
        排查「为什么没收到通知」时先看这里 —— 比翻应用日志快得多。
        """
        try:
            from ..app.notify import read_audit
            rows = read_audit(self.data_dir, limit=200)
        except Exception as exc:  # noqa: BLE001
            rows = []
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("读取通知审计失败：{e}").format(e=exc))
        if not rows:
            body = tr("还没有通知记录。启用 Webhook 并完成一次任务后会出现。")
        else:
            lines = []
            for r in rows:
                lines.append(
                    f"{r.get('ts','')}  [{r.get('event','')}]  "
                    f"ok={r.get('ok','')} fail={r.get('fail','')}  "
                    f"attempts={r.get('attempts','-')}  {r.get('url','')}")
            body = "\n".join(lines)
        # G68-4 同类问题：窗口已关闭/不可见时 `exec()` 会**永久阻塞进程**。
        # 不可见时退化为写日志（与 `_on_diag_finished` 的处置一致）。
        if not self._can_show_modal():
            try:
                self._emit_log(_fmt_dt(), LogLevel.INFO, tr("通知审计") + "：" + body)
            except Exception:
                pass
            return
        try:
            from .diag_dialog import DiagReportDialog
            DiagReportDialog(tr("通知审计（最近 200 条）"), body, body, self,
                             data_dir=self.data_dir).exec()
        except Exception as exc:  # noqa: BLE001
            from PyQt6.QtWidgets import QMessageBox
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("通知审计对话框打开失败：{e}").format(e=exc))
            QMessageBox.information(self, tr("通知审计"), body)

    def _on_input_changed(self) -> None:
        """输入变化 → 防抖后重新校验（绑定方法，避免 lambda 引用环）。"""
        try:
            self._validate_timer.start()
        except Exception:
            pass

    def _refresh_url_validation(self) -> None:
        """G54-1：刷新「有效 N / 无效 M」提示条。

        只对**非空、非注释**行计数；无效行最多列 3 条，避免长列表把界面刷爆。
        """
        try:
            from .theme import maybe_validate_url
            raw_lines = [ln.strip() for ln in self.repo_input.toPlainText().splitlines()]
            lines = [ln for ln in raw_lines if ln and not ln.startswith("#")]
        except Exception:
            return
        if not lines:
            self.repo_valid_lbl.setText("")
            self.repo_valid_lbl.setStyleSheet("")
            # G74：无内容时隐藏，否则空标签占一行高度，1366×768 下会让
            # 顶部滚动区凭空多出一条「其实没内容可滚」的滚动条。
            self.repo_valid_lbl.setVisible(False)
            return
        bad: list[tuple[str, str]] = []
        for ln in lines:
            err = maybe_validate_url(ln)
            if err:
                bad.append((ln, err))
        ok = len(lines) - len(bad)
        if not bad:
            self.repo_valid_lbl.setVisible(True)
            self.repo_valid_lbl.setText(
                tr("有效 {n} / 无效 0").replace("{n}", str(ok)))
            self.repo_valid_lbl.setStyleSheet(
                f"color:{self._semantic_color('accent2')};")
            return
        shown = "；".join(ln[:40] for ln, _e in bad[:3])
        more = tr("（等 {n} 条）").replace("{n}", str(len(bad))) if len(bad) > 3 else ""
        self.repo_valid_lbl.setVisible(True)
        self.repo_valid_lbl.setText(
            tr("有效 {ok} / 无效 {bad}：{items}{more}")
            .replace("{ok}", str(ok)).replace("{bad}", str(len(bad)))
            .replace("{items}", shown).replace("{more}", more))
        self.repo_valid_lbl.setStyleSheet(
            f"color:{self._semantic_color('error')};")

    def _on_quick_fill(self, _checked: bool = False) -> None:
        """G35-7：快捷填充按钮 → 把对应仓库地址追加进输入区。

        G69-18：由 lambda 改为绑定方法。PyQt 对 QObject 的绑定方法用弱引用保存，
        不会形成「控件→连接→闭包→窗口→控件」的引用环。
        仓库名经控件属性 `quick_repo` 传入（`self.sender()` 取回）。
        """
        btn = self.sender()
        repo = btn.property("quick_repo") if btn is not None else None
        if repo:
            self.repo_input.appendPlainText(f"https://github.com/{repo}")

    def _edit_quick_repos(self) -> int:
        """G76 + Rev11 V-20：编辑快捷填充（行内编辑器，D-7 只改显示标签）。

        Rev11 起「逗号串弹框」升级为逐条行内编辑 + 落点预览；
        保存路径不变（`settings_store.save`，跨进程锁），只触碰 `quick_repos`。
        """
        from .quick_repos_editor import edit_quick_repos_inline
        return edit_quick_repos_inline(self)

    # ------------------------------------------------------------ G35-10 Star 导入
    def import_starred(self) -> None:
        """按 GitHub 用户名拉取 Star 列表并追加进输入区。

        网络 I/O 在 daemon 线程执行，结果经 `starred_fetched` 信号回到 GUI 线程
        （跨线程 emit 自动 QueuedConnection）。`fetch_starred` 自带分页与
        「返回已拉部分 + 错误信息」语义，不抛异常。
        """
        if getattr(self, "_star_busy", False):
            self._emit_log(_fmt_dt(), LogLevel.INFO, tr("Star 列表正在拉取，请稍候"))
            return
        user, ok = QInputDialog.getText(
            self, tr("导入 GitHub Star"),
            tr("GitHub 用户名（github.com/ 后面那段）："),
            text=str(getattr(self, "_last_star_user", "") or ""))
        user = (user or "").strip()
        if not ok or not user:
            return
        self._last_star_user = user
        self._star_busy = True
        # Rev12 U-15：拉取期间禁用入口按钮（此前只有日志，用户靠猜）。
        # 恢复在 _on_starred_fetched（跨线程信号回 GUI 线程的终点）。
        from .busy import _set_buttons_enabled  # 复用禁用/恢复语义, 不用上下文(跨线程)
        self._star_buttons_saved = _set_buttons_enabled(self, ("btn_star_import",), False)
        from ..app.event_bus import EventType as _ET
        from ..app.event_bus import get_event_bus as _bus
        try:
            _bus().publish(_ET.JOB_STARTED, {"job": "star_import",
                                             "cancellable": "non_cancellable"})
        except Exception as _e:      # noqa: BLE001 —— 观测失败不影响忙态, 但留痕
            from ..app.applog import warn as _warn
            _warn(tr("Star 忙态事件发布失败：{e}").format(e=_e))
        token = str(getattr(self.settings, "token", "") or "").strip()
        self._emit_log(_fmt_dt(), LogLevel.INFO, tr("正在拉取 Star 列表…"))
        # 不把 self 捕进闭包：只捕信号与局部变量；线程结束后闭包随之释放。
        sig = self.starred_fetched

        def _run() -> None:
            # 兜底：`fetch_starred` 内部已把网络异常转成 (partial, err)，
            # 但畸形 JSON（例如顶层是数字/字符串）仍可能在解析时抛 TypeError。
            # 不兜住的话信号永远不发，`_star_busy` 会卡死，用户再点也没反应。
            try:
                from ..app.gh_api import fetch_starred
                repos, err = fetch_starred(user, token)
            except Exception as exc:  # noqa: BLE001 —— 线程边界，必须转成结果
                repos, err = [], str(exc)
            sig.emit(repos, err)

        threading.Thread(target=_run, daemon=True, name="gcm-star-fetch").start()

    @staticmethod
    def _repo_key(text: str) -> str:
        """把 `owner/repo`、HTTPS、SSH、带 .git 的写法归一成同一个比较键。"""
        s = (text or "").strip().lower()
        for prefix in ("https://github.com/", "http://github.com/",
                       "git@github.com:", "github.com/"):
            if s.startswith(prefix):
                s = s[len(prefix):]
                break
        if s.endswith(".git"):
            s = s[:-4]
        return s.strip().strip("/")

    @pyqtSlot(list, str)
    def _on_starred_fetched(self, repos, err) -> None:
        """Star 拉取结束（GUI 线程）：去重后回填输入区。"""
        self._star_busy = False
        # U-15：无条件恢复按钮（对应入口处的禁用；本槽是链路唯一终点）。
        from .busy import _restore_buttons
        _restore_buttons(self, getattr(self, "_star_buttons_saved", {}))
        from ..app.event_bus import EventType as _ET2
        from ..app.event_bus import get_event_bus as _bus
        try:
            _bus().publish(_ET2.JOB_FINISHED, {"job": "star_import",
                                               "result": "failed" if err else "ok",
                                               "cancellable": "non_cancellable"})
        except Exception as _e:      # noqa: BLE001 —— 同上, 留痕
            from ..app.applog import warn as _warn2
            _warn2(tr("Star 忙态结束事件发布失败：{e}").format(e=_e))
        if err:
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("Star 列表获取失败") + "：" + err)
        names = [str(x).strip() for x in (repos or []) if str(x).strip()]
        if not names:
            if not err:
                self._emit_log(_fmt_dt(), LogLevel.INFO, tr("该用户没有 Star 仓库"))
            return
        current = self.repo_input.toPlainText().splitlines()
        seen = {self._repo_key(ln) for ln in current if ln.strip()}
        added = []
        for name in names:
            key = self._repo_key(name)
            if not key or key in seen:
                continue
            seen.add(key)
            added.append(name)
        if not added:
            self._emit_log(_fmt_dt(), LogLevel.INFO, tr("Star 列表均已存在于输入区"))
            return
        text = self.repo_input.toPlainText().rstrip()
        self.repo_input.setPlainText((text + "\n" if text else "") + "\n".join(added))
        self._emit_log(_fmt_dt(), LogLevel.INFO, tr("已导入 Star 仓库到输入区"))

    def _on_mode_changed(self, index: int) -> None:
        """克隆模式变化 → 仅「浅克隆」相关档位启用深度微调（G69-18：绑定方法）。"""
        self.depth_spin.setEnabled(index in (1, 2))

    def _apply_settings_filter(self, keyword: str) -> None:
        """G69-18：设置页搜索过滤（原为 settings_groups 内的闭包，会捕获 owner 成环）。"""
        kw = (keyword or "").strip().lower()
        groups = list(getattr(self, "settings_groups", []) or []) + list(
            getattr(self, "settings_groups_extra", []) or [])
        visible = 0
        nav = getattr(self, "settings_nav", None)
        for i, group in enumerate(groups):
            if not kw:
                vis = True
            else:
                title = group.title().lower()
                labels = [lbl.text().lower() for lbl in group.findChildren(QLabel)]
                vis = kw in title or any(kw in x for x in labels if x)
            group.setVisible(vis)
            if nav is not None and i < nav.count():
                nav.item(i).setHidden(not vis)
            visible += 1 if vis else 0
        empty = getattr(self, "settings_empty_lbl", None)
        if empty is not None:
            # G70：搜索无结果时给出明确空态，而不是一片空白
            empty.setVisible(bool(kw) and visible == 0)

    def _toggle_host_tokens_echo(self, checked: bool) -> None:
        """G44-1：按平台凭据「显示明文」勾选切换掩码（G69-18：绑定方法）。"""
        self.edit_host_tokens.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)

    def _on_adapt_changed(self, n: int, text: str):
        """G48-1：并发自适应状态栏提示。"""
        try:
            rate = getattr(self.engine, "batch_fail_rate", None)
            if rate is not None:
                text = text + tr("（近批失败率 {r}%）").format(r=f"{rate:.0f}")
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
                                   tr("读取拖入文件失败：{p}（{e}）").format(p=p, e=exc))
            elif p.is_dir():
                dirs.append(str(p))
        if added_lines:
            self.repo_input.appendPlainText("\n".join(added_lines) + "\n")
            self._emit_log(_fmt_dt(), LogLevel.INFO,
                           tr("已从拖入文件导入 {n} 行地址。").format(n=len(added_lines)))
        if dirs:
            # 复用本地导入扫描通道（默认扫描拖入目录）
            try:
                from .local_repos_dialog import LocalReposDialog
                dlg = LocalReposDialog(parent=self, root_paths=dirs, db=self.db)
                dlg.exec()
                if dlg.selected:
                    n = dlg.import_selected()
                    self._load_db_into_grid()
                    self._emit_log(_fmt_dt(), LogLevel.INFO, tr("已从拖入目录导入 {n} 个本地仓库").format(n=n))
                    self.statusBar().showMessage(tr("已导入 {n} 个本地仓库").format(n=n))
            except Exception as exc:
                self._emit_log(_fmt_dt(), LogLevel.WARN, tr("拖入目录导入失败：{e}").format(e=exc))

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
            QMessageBox.information(self, tr("提示"), tr("输入区为空，没有可保存的地址。"))
            return
        name, ok = QInputDialog.getText(self, tr("保存任务清单"), tr("清单名称："))
        if not ok or not name.strip():
            return
        try:
            self.task_lists.save(name.strip(), items)
            self._refresh_task_lists()
            self._emit_log(_fmt_dt(), LogLevel.INFO, tr("已保存任务清单：{name}（{n} 行）").format(name=name.strip(), n=len(items)))
            self.statusBar().showMessage(tr("已保存清单：{name}").format(name=name.strip()))
        except Exception as e:
            from .error_dialog import show_error  # U-18：三段式 + 动作
            show_error(self, e, context=tr("保存任务清单"), title=tr("保存失败"))

    def _load_task_list(self):
        """把所选清单的地址填入输入区。"""
        name = self.combo_load_list.currentText()
        if not name:
            QMessageBox.information(self, tr("提示"), tr("请先选择要载入的清单。"))
            return
        items = self.task_lists.items(name)
        if not items:
            QMessageBox.information(self, tr("提示"), tr("清单「{name}」为空或不存在。").format(name=name))
            return
        self.repo_input.appendPlainText("\n".join(items) + "\n")
        self._emit_log(_fmt_dt(), LogLevel.INFO, tr("已载入任务清单：{name}（{n} 行）").format(name=name, n=len(items)))
        self.statusBar().showMessage(tr("已载入清单：{name}").format(name=name))

    def import_readme_list(self):
        """G84-18：从清单文件导入仓库地址（本地解析，不联网）。"""
        from .download_tools import import_readme_list as _ir
        _ir(self)

    def open_batch_preview(self):
        """Rev10 P-02：执行前预览差异表（纯本地预检）。"""
        from .download_tools import open_batch_preview as _obp
        _obp(self)

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
                self, tr("提示"),
                tr("没有可用的 GitHub 地址。\n\n有效示例：\n"
                   "https://github.com/vercel-labs/skills\ngit@github.com:microsoft/azure-skills.git\n"
                   "vercel-labs/agent-skills"))
            return
        if invalid:
            shown = "\n".join(f"  - {line}" for line in invalid)
            ret = QMessageBox.question(
                self, tr("无效地址（忽略并继续？）"),
                tr("{n} 行不是有效的 GitHub 仓库地址：\n{shown}\n\n"
                   "继续将只同步有效行，是否忽略无效行？").format(n=len(invalid), shown=shown),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
            self._emit_log(_fmt_dt(), LogLevel.WARN,
                            tr("已忽略 {n} 行无效地址：{list}").format(n=len(invalid), list=", ".join(invalid)))
        target = self.target_edit.text().strip()
        if not target:
            QMessageBox.warning(self, tr("提示"), tr("请先选择下载目录。"))
            return
        try:
            Path(target).mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, tr("提示"), tr("无法创建下载目录：{e}").format(e=e))
            return
        # G38-4：模式 1=浅克隆、2=单分支浅克隆（都走 shallow 路径，后者加 --single-branch）
        mode_idx = self.mode_combo.currentIndex()
        shallow = mode_idx in (1, 2)
        mirror = mode_idx == 3  # G48-6 镜像克隆
        depth = self.depth_spin.value()
        # Rev11-C R-01：就绪清单 —— 用户点「开始」后、引擎起跑前，结论条先展示
        # 「这批会发生什么」（与 CLI --dry-run / 预览差异表同一份 plan_batch 纯函数）。
        try:
            from ..app.batch_plan import plan_batch
            _plan = plan_batch("\n".join(str(s.url_https) for s in specs), target)
            _c = _plan.counts
            self.batch_conclusion.set_ready(
                clone=_c.get("clone", 0), update=_c.get("update", 0),
                protected=_c.get("duplicate", 0) + _c.get("invalid", 0),
                blocked=_c.get("blocked", 0) + _c.get("replace", 0))
        except Exception as e:      # noqa: BLE001 —— 就绪展示失败不得拖垮启动
            from ..app.applog import warn as _warn
            _warn(f"就绪清单计算失败（不影响启动）：{e}")
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
            QMessageBox.information(self, tr("提示"), tr("有任务正在运行，请先取消或等待完成。"))
            return
        rows = self.db.list_pending_updates()  # 全 host（已排除黑名单 excluded=1）
        if not rows:
            QMessageBox.information(self, tr("提示"), tr("数据库中没有已记录的仓库，请先到「下载中心」添加。"))
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
        # G69-4 修复：**先判 busy 再 pop**。原实现先 `pop(0)` 再 `if self.busy: return`，
        # 被 pop 掉的那一组根目录再也不会被拉取（每次调用丢掉一组，用户完全看不到报错）。
        if self.busy:
            return  # 当前组仍在运行，等 finished 槽再拉下一组
        root, rows = self._multi_root_update.pop(0)
        specs = self._rows_to_specs(rows)
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, tr("一键更新：处理根目录 {root}（{n} 个仓库）").format(root=root, n=len(specs)))
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
        # Rev14 V4：跳过计数按**批次**统计 —— 不清零的话，第一批跳过 3 个、
        # 第二批全成功时状态栏/托盘/Webhook 仍会报「跳过 3」（审查实测复现）。
        self._skipped_count = 0
        # Rev14 R14-10 修正：新批次开始时清掉上一批的「断网」判定 —— 否则断网批次
        # 结束后标记会一直亮着，直到**下一次成功**才灭（用户只是看看界面不会消）。
        _st = getattr(self, "_offline_streak", None)
        if _st is not None:
            _st.note_success()
            _ind = getattr(self, "offline_indicator", None)
            if _ind is not None:
                _ind.set_offline(False)
        # Rev14 R14-08：从"点开始"到第一条结果之间显示骨架，避免整表空白
        self.progress_panel.set_busy_preview(True)
        self._skeleton_on = True
        # G75：仪表齿条清零并起表（活跃数由进度信号累加）
        try:
            self.status_strip.begin(len(specs))
        except Exception as exc:  # noqa: BLE001 —— 齿条只是观测件，不能拖垮下载链路
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("仪表齿条未能启动：{e}").format(e=exc))
        # Rev11（V-09）：结论条切入运行态（动效受 reduced_motion 支配，组件内自判）。
        # getattr 兜底而非 try/except pass（异常预算棘轮只降不升）。
        _bc = getattr(self, "batch_conclusion", None)
        if _bc is not None:
            _bc.begin(len(specs))
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        # C2-6：批次启动即可暂停（暂无待发任务时点它只会给出提示）
        self.btn_pause_batch.setEnabled(True)
        self.btn_pause_batch.setText(tr("暂停"))
        self.btn_update_all.setEnabled(False)
        self.btn_cancel_manage.setEnabled(True)
        self.repo_input.setEnabled(False)
        self.mode_combo.setEnabled(False)
        self.depth_spin.setEnabled(False)

        self._emit_log(_fmt_dt(), LogLevel.SYSTEM,
                        tr("开始并行同步 {n} 个仓库（{mode}）").format(
                        n=len(specs),
                        mode=(tr("浅克隆 depth=") + str(depth)) if shallow else tr("满量")))
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM, tr("根目录：{v}").format(v=target_root))

        # 下载完成后自动清空输入框（默认开启）
        self._clear_after_finish = clear_input and not multi_root_relay
        self._multi_root_relay = multi_root_relay

        # 统一调度：engine 重建 root（根目录可变；超时/重试/代理/token 来自设置）
        #
        # G69-3 修复（永久死锁，实测可达）：**替换引擎前必须先让旧引擎收敛**。
        # `SyncEngine` 是 Python 子类且持有 `QThreadPool(self)` 这个 C++ 子对象；
        # 旧引擎若仍有在跑的 worker，Python 侧引用归零时会**持 GIL** 去析构 C++ 对象，
        # 而 `~QThreadPool` 要等 worker 结束、worker 结束又要重新取 GIL → 互相死等，
        # 进程永久卡死（faulthandler 实测：worker 卡在 run 里等 GIL、主线程卡在 `del engine`；
        # 对照组「worker 已结束」时 del 用时 0.00s）。
        # 旧引擎在本函数正常路径下应是空闲的，但仍要兜一层：自动重投定时器（`_retry_tick`）
        # 可能刚刚自起了一批（那批对 `owner.busy` 不可见），此时旧引擎正在跑。
        old_engine = getattr(self, "engine", None)
        if old_engine is not None:
            try:
                # G69-13：用 `discard()` 而不是 `shutdown()` —— 后者内部 `drain()` 会
                # `processEvents()`，在 UI 槽里泵事件会把刚排队的 finished 重入进来
                # （触碰正在被替换的控件）。`discard()` 阻塞式等线程池排空，同样能避免
                # 「析构 QThreadPool ⟷ worker 抢 GIL」死锁，且无重入。
                old_engine.discard()
            except Exception:
                pass
        # G71-1：引擎事件发布到进程级总线（SSE / 诊断包共享同一条流）
        try:
            from ..app.event_bus import get_event_bus
            _event_bus = get_event_bus()
        except Exception:
            _event_bus = None
        # G71-7：引擎参数映射已抽取到 engine_factory（长函数治理，行为零变化）
        from .engine_factory import build_sync_engine
        self.engine = build_sync_engine(
            self, target_root, single_branch=bool(single_branch),
            mirror=bool(mirror), event_bus=_event_bus)
        self.watchdog.set_engine(self.engine)
        self.engine.line.connect(self._on_engine_line)
        self.engine.adapt_changed.connect(self._on_adapt_changed)  # G48-1
        self.engine.progress.connect(self._on_worker_progress)
        self.engine.progress_detail.connect(self._on_worker_progress_detail)
        self.engine.result.connect(self._on_worker_result)
        # Rev14 F2：断点续传跳过的仓库也要把行推到终态（否则永远「等待中」）
        self.engine.skipped.connect(self._on_engine_skipped)
        # Rev16 C4：重投批次是"新批次" —— UI 重建行 + 置回运行态。progress_panel
        # 在 __init__ 后段才创建，这里连 self 的薄转发（转发时 getattr 防护）
        self.engine.retry_batch_started.connect(self._on_retry_batch_started)
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
        # Rev14 F2 修正：`progress_rows.install_row` 只用 `setItem` 写格子，**不会**
        # 把行数撑起来（`QTableWidget.setItem` 对不存在的行是静默空操作）。所以这里
        # 必须先 `_prepare_table` 建行，否则「预填」一直是白做工 —— 全部被跳过时
        # 表格就一行都没有（用户看不到是哪个仓库被跳过）。
        self._prepare_table(len(specs))
        self.progress_panel.begin_bulk()
        for i, spec in enumerate(specs):
            self._add_table_row(i, spec)
            self.row_specs[i] = spec
            self.flags[i] = CancelFlag()
        self.progress_panel.end_bulk()

        fetch_depth = depth if shallow else 0
        launched = self.engine.launch(
            specs,
            host_by_key=host_by_key,
            fetch_depth=fetch_depth,
            unshallow=getattr(self.settings, "fetch_unshallow", False),
            clear_input=clear_input,
            check_existing=True,
            mirror=bool(mirror),  # G48-6
            # Rev14 F2：GUI 需要知道哪些被「已完成」预检跳过 → 行才能到终态。
            # CLI 保持默认 False，`results[]` 与退出码契约零变化。
            report_skipped=True,
        )
        if launched == 0:
            # 全部被跳过：行在预填阶段就已建好（`_prepare_table(len(specs))`），
            # `_on_engine_skipped` 也已把它们推到「已跳过」。这里**不需要**再重建 ——
            # 重建反而会抹掉刚写好的终态（审查指出这段兜底比它兜的错更危险，已删）。
            self.busy = False
            self.progress_panel.set_busy_preview(False)
            self._reset_buttons()
            self._on_engine_finished()
            # Rev14 F3：`engine.launch()` 在「全部被跳过」时会**同步** finished，
            # 上面的 `_on_engine_finished` 已经写好批次小结；原先这里继续往下执行，
            # 又被 `并行同步中：N 个仓库…` 覆盖，用户看到的是「还在跑」。
            return
        else:
            # G21-1：表格行与 engine 去重后的索引严格对齐（去重/断点跳过都会
            # 改变唯一清单），旧行全部废弃重建，避免暂停/进度按错行
            n = len(self.engine.specs)
            self._prepare_table(n)
            self.progress_panel.begin_bulk()
            for i in range(n):
                spec = self.engine.specs[i]
                self._add_table_row(i, spec)
                self.row_specs[i] = spec
            self.progress_panel.end_bulk()
            self._pending_count = n
            self.statusBar().showMessage(tr("并行同步中：{n} 个仓库…").format(n=n))
            return

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
        # G83 P1-1：记录本批次是否出现过真实变更（updated/cloned），
        # 批次结束后用于自适应退避（不改变任何既有转发行为）。
        # SyncAction 是确定的枚举导入，这里唯一可能失败的是测试桩缺 action ——
        # 用 AttributeError 窄异常，不进裸吞预算。
        try:
            from ..models import SyncAction
            if res.action in (SyncAction.UPDATED, SyncAction.CLONED):
                self._auto_update_batch_had_changes = True
        except AttributeError:
            pass
        # Rev11（V-09）：结论条与表格共用同一份终态流（零引擎契约变更）。
        # getattr 兜底而非 try/except pass（异常预算棘轮只降不升）。
        _bc = getattr(self, "batch_conclusion", None)
        if _bc is not None:
            _bc.note_result(str(getattr(res.status, "value", res.status)))
        # Rev14 R14-08：第一条结果到达即撤掉骨架（此后表格已有真实行）
        if getattr(self, "_skeleton_on", False):
            self._skeleton_on = False
            self.progress_panel.set_busy_preview(False)
        # Rev14 R14-10：离线判定（用唯一分类器，不另写关键字表）
        self._note_network_streak(res)
        return self.progress_panel._on_worker_result(index, res)

    def _on_engine_skipped(self, key: str, message: str) -> None:
        """把「已完成，跳过」推到对应表格行（Rev14 F2）。

        为什么用 key 而不是 index：被跳过的 spec **不进** `engine.specs`，
        也就没有 index（index 是调度序，不是输入序）。用 `owner/repo` 匹配
        `row_specs` 才是稳定的。找不到对应行（例如部分跳过导致表格按 engine.specs
        重建）就只累计计数，由批次小结里的「跳过 N」兜住 —— 不让信息消失。
        """
        self._skipped_count = int(getattr(self, "_skipped_count", 0)) + 1
        for i, spec in getattr(self, "row_specs", {}).items():
            if f"{getattr(spec, 'owner', '')}/{getattr(spec, 'repo', '')}" != key:
                continue
            table = self.progress_panel.table
            if i >= table.rowCount():
                continue
            from PyQt6.QtCore import Qt as _Qt
            st_item = table.item(i, 2)
            if st_item is not None:
                st_item.setText(tr("已跳过"))
                st_item.setData(_Qt.ItemDataRole.UserRole, "skipped")
            det = table.item(i, 4)
            if det is not None:
                det.setText(message)
            act = table.item(i, 3)
            if act is not None and not (act.text() or "").strip("—"):
                act.setText("—")
            # **不要 break**：预填按「每条输入」建行，同一地址贴两遍就有两行；
            # 只标第一行会让第二行永远停在「等待中」（审查实测 [已跳过, 等待中]）。

    def _note_network_streak(self, res: SyncResult) -> None:
        """按单条结果更新「断网」标记（Rev14 R14-10）。

        成功 → 清零；失败 → 交给 `app.errors.classify_error` 分类后入窗口。
        不弹窗、不阻塞：这是状态指示，网络恢复不需要用户确认。
        """
        streak = getattr(self, "_offline_streak", None)
        ind = getattr(self, "offline_indicator", None)
        if streak is None or ind is None:
            return
        status = str(getattr(res.status, "value", res.status))
        if status == "success":
            streak.note_success()
        elif status == "failed":
            from ..app.errors import classify_error
            info = classify_error(str(getattr(res.message, "", "") or ""),
                                  str(getattr(res.detail, "", "") or ""))
            was_offline = streak.is_offline
            now_offline = streak.note_failure(getattr(info, "category", "other"))
            if now_offline and not was_offline:
                self.statusBar().showMessage(tr(
                    "网络似乎不可用：最近几次失败都是网络类错误，恢复后会自动消失"), 8000)
        else:
            return
        ind.set_offline(streak.is_offline)

    def _animate_result_row(self, index: int, status: SyncStatus):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._animate_result_row(index, status)

    # ------------------------------------------------------------ 槽
    @pyqtSlot()
    # ------------------------------------------------------------ G52-4 进度 GC 模块
    @staticmethod
    def _progress_gc_mod():
        """G52-4：进度文件治理模块（惰性导入，避免拖慢启动）。"""
        try:
            from ..db import progress_gc
            return progress_gc
        except Exception:
            return None

    # ------------------------------------------------------------ G52-5 失败汇总
    # ------------------------------------------------------------ G71-9 本地 API
    def _apply_local_api_settings(self) -> None:
        """按设置启停 GUI 内置本地 API（只读 + SSE）。

        **为什么在 GUI 进程内**：EventBus 是进程内单例；独立 `--serve` 进程
        看不到 GUI 引擎发布的事件。只有同进程托管，`/api/events` 才是真流。

        G71-11（审查 R4）：
        - **先起新服、成功后再停旧服** —— 新端口被占用时旧服务继续可用，
          不会出现「改端口后 API 直接消失」；
        - 端口未变化时不重启（避免每次设置保存都空转）；
        - 旧服务的 `shutdown()`/`join()` 放到后台线程，不阻塞 GUI（每次约 0.5s）。
        """
        if getattr(self, "_close_requested", False):
            self._stop_local_api(wait=True)
            return
        if not bool(getattr(self.settings, "local_api_enabled", False)):
            self._stop_local_api(wait=True)
            return
        # 0 = 内核分配（仅测试/临时实例用；设置页 spin 下限 1024）
        try:
            port = int(getattr(self.settings, "local_api_port", 8765))
        except (TypeError, ValueError):
            port = 8765
        if port < 0:
            port = 8765
        old = getattr(self, "_local_api_server", None)
        old_th = getattr(self, "_local_api_thread", None)
        if old is not None and getattr(self, "_local_api_port", None) == port:
            return                       # 端口未变，保持现状
        try:
            from ..app.event_bus import get_event_bus
            from ..http_api import create_server
            httpd = create_server(
                f"127.0.0.1:{port}", root=self.data_dir, data_dir=self.data_dir,
                allow_mutate=False, event_bus=get_event_bus())
        except Exception as e:  # noqa: BLE001 —— 端口占用/权限不足不能拖垮 GUI
            self._emit_log(_fmt_dt(), LogLevel.WARN,
                           tr("本地 API 启动失败（127.0.0.1:{port}）：{e}").format(port=port, e=e))
            return                       # 旧服务（若有）保持运行
        th = threading.Thread(target=httpd.serve_forever, daemon=True,
                              name="gcm-local-api")
        th.start()
        self._local_api_server = httpd
        self._local_api_thread = th
        self._local_api_port = port
        if old is not None:
            self._shutdown_local_api_async(old, old_th)
        self._emit_log(_fmt_dt(), LogLevel.SYSTEM,
                       tr("本地只读 API 已启动：http://127.0.0.1:{port}（GET /api/events 为 SSE 事件流）").format(port=port))

    @staticmethod
    def _shutdown_local_api_async(httpd, th) -> None:
        """后台停止一个 API 实例（不阻塞 GUI 线程）。"""
        def _run() -> None:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
            if th is not None:
                try:
                    th.join(2.0)
                except Exception:
                    pass

        threading.Thread(target=_run, daemon=True,
                         name="gcm-local-api-stop").start()

    def _stop_local_api(self, wait: bool = False) -> None:
        """停止 GUI 内置本地 API（幂等）。

        `wait=True` 时同步等 serve_forever 退出（关窗/用户主动关闭用；
        最坏约 0.5s）。端口切换走 `wait=False`，由后台线程收敛。
        """
        httpd = getattr(self, "_local_api_server", None)
        th = getattr(self, "_local_api_thread", None)
        self._local_api_server = None
        self._local_api_thread = None
        self._local_api_port = None
        if httpd is None:
            return
        if wait:
            try:
                httpd.shutdown()
            except Exception:
                pass
            try:
                httpd.server_close()
            except Exception:
                pass
            if th is not None:
                try:
                    th.join(2.0)
                except Exception:
                    pass
        else:
            self._shutdown_local_api_async(httpd, th)

    def _open_failure_dialog(self):
        """打开「失败原因聚合」对话框（按错误类别聚合 + 可重试该类别）。"""
        try:
            from .failure_panel import FailureDialog
            dlg = FailureDialog(owner=self, parent=self)
            dlg.retry_category.connect(self._on_failure_category_retry)
            # C2-4：把「去设置 / 诊断包」两个真实动作接上（原来失败面板只有一句文字建议）
            dlg.open_settings_requested.connect(self._goto_settings_section)
            dlg.diagnostics_requested.connect(self._export_diag_bundle)
            dlg.exec()
        except Exception as e:
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("失败汇总打开失败：{e}").format(e=e))

    def _open_retry_dialog(self):
        """G71-4：打开「失败重试队列」对话框（可查看/立即重试/移除）。"""
        if not self._can_show_modal():
            try:
                eng = getattr(self, "engine", None)
                q = getattr(eng, "retry_queue", None) if eng is not None else None
                n = q.count() if q is not None else 0
                self._emit_log(_fmt_dt(), LogLevel.INFO,
                               tr("重试队列：{n} 条").format(n=n))
            except Exception:
                pass
            return
        try:
            from .retry_panel import RetryQueueDialog
            RetryQueueDialog(owner=self, parent=self).exec()
        except Exception as e:
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("重试队列打开失败：{e}").format(e=e))

    def _diag_status_report(self) -> str:
        """构造诊断包的文本报告（纯读；不触发网络/子进程）。"""
        from .. import __version__
        lines = [
            f"Git-clone-Max {__version__}",
            tr("生成时间：{t}").format(t=_fmt_dt()),
            f"数据目录：{self.data_dir}",
            tr("下载目录：{v}").format(v=getattr(self.settings, "download_dir", "") or tr("(未设置)")),
            tr("引擎：busy={b} concurrency={c}").format(
            b=bool(getattr(self, "busy", False)),
            c=getattr(getattr(self, "engine", None), "concurrency", "-")),
        ]
        try:
            st = self.db.stats_overview()
            lines.append(tr("数据库：{v}").format(v=st))
        except Exception as e:  # noqa: BLE001
            lines.append(tr("数据库统计失败：{e}").format(e=e))
        try:
            eng = getattr(self, "engine", None)
            q = getattr(eng, "retry_queue", None) if eng is not None else None
            if q is not None:
                lines.append(tr("重试队列：{v}").format(v=q.stats()))
            else:
                lines.append(tr("重试队列：未启用"))
        except Exception as e:  # noqa: BLE001
            lines.append(tr("重试队列统计失败：{e}").format(e=e))
        try:
            from ..app.event_bus import get_event_bus
            lines.append(tr("事件总线：{v}").format(v=get_event_bus().stats()))
        except Exception as e:  # noqa: BLE001
            lines.append(tr("事件总线统计失败：{e}").format(e=e))
        return "\n".join(lines)

    def _export_diag_bundle(self):
        """G71-6：导出诊断 zip（用户选路径；失败给可读提示）。"""
        if not self._can_show_modal():
            self._emit_log(_fmt_dt(), LogLevel.INFO, self._diag_status_report())
            return
        try:
            from ..app.diag_bundle import build_diag_bundle, default_bundle_name
            default = str(Path.home() / "Desktop" / default_bundle_name())
            path, _ = QFileDialog.getSaveFileName(
                self, tr("导出诊断包"), default, tr("Zip 压缩包 (*.zip)"))
            if not path:
                return
            if not str(path).lower().endswith(".zip"):
                path = str(path) + ".zip"
            from ..app.event_bus import get_event_bus
            from .busy import busy as _busy
            # C2-3：诊断包要遍历事件/日志并压 zip（几百 MB 数据时数秒），置忙并禁用入口
            with _busy(self, "diag_bundle", buttons=("btn_diag_bundle",)):
                out = build_diag_bundle(
                    path, data_dir=self.data_dir,
                    report_text=self._diag_status_report(),
                    event_bus=get_event_bus(),
                    extra_manifest={"tab": self.tabs.currentIndex() if hasattr(self, "tabs") else 0})
            self._emit_log(_fmt_dt(), LogLevel.SYSTEM, tr("诊断包已导出：{v}").format(v=out))
            QMessageBox.information(self, tr("诊断包已导出"), str(out))
        except Exception as e:  # noqa: BLE001
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("诊断包导出失败：{e}").format(e=e))
            try:
                from .error_dialog import show_error  # U-18：三段式 + 动作
                show_error(self, e, context=tr("导出诊断包"), title=tr("导出失败"))
            except Exception:
                pass

    def _on_failure_category_retry(self, category):
        """按失败类别重试：从 progress.json failed 记录中筛出该类并重新调度。"""
        try:
            from ..db.repo_db import load_progress
            from .failure_panel import categorize_failure
            prog = load_progress(self.progress_path)
            rows = prog.get("failed") or []
            hits = [r for r in rows
                    if categorize_failure(str(r.get("message") or "")) == category]
            if not hits:
                self._emit_log(_fmt_dt(), LogLevel.INFO, tr("该类无失败记录，跳过重试"))
                return
            specs = []
            for r in hits:
                key = str(r.get("key") or "")
                try:
                    from ..app.url_lib import parse_any_repo_url
                    sp = parse_any_repo_url(key)
                except Exception:
                    sp = None
                if sp is not None:
                    specs.append(sp)
            if specs and not self.busy:
                root = Path(getattr(self.settings, "download_dir", "") or self.data_dir / "clones")
                self._launch(specs, target_root=root, shallow=False, depth=1,
                             clear_input=False, single_branch=False)
        except Exception as e:
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("失败类别重试失败：{e}").format(e=e))

    # ------------------------------------------------------------ G52-2 启动续跑对话框
    def _schedule_resume_check(self) -> None:
        """窗口真实 show 后调度续跑检测（只在可见时弹，避免测试/关闭路径误弹）。"""
        try:
            if self.isVisible():
                QTimer.singleShot(600, self._maybe_show_resume_dialog)
        except Exception:
            pass

    def _maybe_show_resume_dialog(self):
        """启动时检测 session_runtime.json 有未完成任务 → 弹「续跑/清空」对话框。

        仅当窗口可见（真实应用启动）才弹；测试/closeEvent/drain 期间不可见 → 跳过。
        """
        try:
            if not self.isVisible():
                return
            store = getattr(self, "session_store", None)
            if store is None or not store.is_resumable():
                return
            specs = store.to_repo_specs()
            if not specs:
                return
            names = "、".join(f"{s.owner}/{s.repo}" for s in specs[:5])
            if len(specs) > 5:
                names += "…"
            ret = QMessageBox.question(
                self,
                tr("检测到未完成任务"),
                tr("上次会话有 {n} 个任务未完成：\n{names}\n\n是否续跑？").format(n=len(specs), names=names),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes)
            if ret == QMessageBox.StandardButton.Yes:
                root = Path(getattr(self.settings, "download_dir", "") or self.data_dir / "clones")
                self._launch(specs, target_root=root, shallow=False, depth=1,
                             clear_input=False, single_branch=False)
            elif ret == QMessageBox.StandardButton.No:
                store.clear()
        except Exception:
            pass

    # ------------------------------------------------------------ G52-3 看门狗续跑
    def _on_watchdog_resume(self, specs):
        """看门狗检测到未完成任务 → 自动续跑（静默/非静默）。"""
        try:
            if self.busy or not specs:
                return
            if not getattr(self.watchdog, "silent", False):
                self._emit_log(_fmt_dt(), LogLevel.SYSTEM,
                               tr("看门狗检测到 {n} 个未完成任务，自动续跑").format(n=len(specs)))
            root = Path(getattr(self.settings, "download_dir", "") or self.data_dir / "clones")
            self._launch(specs, target_root=root, shallow=False, depth=1,
                         clear_input=False, single_branch=False)
        except Exception as e:
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("看门狗续跑失败：{e}").format(e=e))

    def _on_retry_batch_started(self, n: int):
        """Rev16 C4：重投批次开始 → 转发给进度表（progress_panel 可能尚未创建）。"""
        panel = getattr(self, "progress_panel", None)
        if panel is not None:
            panel._on_retry_batch_started(n)

    def _on_engine_finished(self):
        """G45-6 转发 progress_table.py（行为零变化；G52-3 复位看门狗）。"""
        # G83 P1-1：批次结束 → 结算本轮变更并重排自适应定时器。
        # _note_batch_change 内部全部为 getattr/int 运算 + QTimer.start（有 None 防护），
        # 无可抛外部调用 —— 不加 try（except 预算不回涨）。
        had = bool(getattr(self, "_auto_update_batch_had_changes", False))
        self._auto_update_batch_had_changes = False
        self._note_batch_change(had)
        # Rev14 R14-08：零结果批次也要撤掉骨架（否则占位块会一直盖着空表）
        if getattr(self, "_skeleton_on", False):
            self._skeleton_on = False
            self.progress_panel.set_busy_preview(False)
        try:
            if getattr(self, "watchdog", None) is not None:
                self.watchdog.reset()
        except Exception:
            pass
        # Rev11（V-09）：批次结束 → 结论条切成绩单态（getattr 兜底，同上）
        _bc = getattr(self, "batch_conclusion", None)
        if _bc is not None:
            _bc.finish()
        return self.progress_panel._on_engine_finished()

    def _reset_buttons(self):
        """G45-6 转发 progress_table.py（行为零变化）。"""
        return self.progress_panel._reset_buttons()

    def open_command_palette(self):
        """C2-5：命令面板入口（实现在 `ui/command_palette.py`）。"""
        try:
            from .command_palette import open_palette
            open_palette(self)
        except Exception as e:  # noqa: BLE001 —— 面板打不开不该影响主流程
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("命令面板打开失败：{e}").format(e=e))

    def toggle_batch_pause(self):
        """C2-6：暂停 / 继续本批次（实现见 `ui/batch_actions.py`）。"""
        from .batch_actions import toggle_batch_pause
        return toggle_batch_pause(self)

    def cancel_all(self):
        # A5-4/U-26：运行中取消整批不可恢复，二次确认（默认 No）；空闲时直接放行。
        # busy 判定与 closeEvent/start_all 同口径（UI.busy 或 engine.busy 任一为真即运行中）
        running = bool(getattr(self, "busy", False)
                       or getattr(getattr(self, "engine", None), "busy", False))
        if running:
            ret = QMessageBox.question(
                self, tr("取消全部任务"),
                tr("正在运行的任务将被取消（已完成的仓库保留），未开始的仓库不再继续。\n\n"
                   "确定取消全部吗？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
        if hasattr(self, "engine") and self.engine is not None:
            self.engine.cancel_all()
        self._emit_log(_fmt_dt(), LogLevel.WARN, tr("已请求取消全部任务…"))
        self.statusBar().showMessage(tr("正在取消…"))
        # 由结果槽统一复位

    def confirm_clear_input(self):
        """A5-4/U-18：清空输入区；>20 行时先确认（默认 No）。

        Rev12 U-08：清空后把原文写进台账并弹撤销浮条 —— 输入区常是用户手打的
        几百行地址，本仓库此前唯一能找回的方式是「保存过清单」。
        """
        n = self.repo_input.blockCount() if hasattr(self.repo_input, "blockCount") else 0
        if n > 20:
            ret = QMessageBox.question(
                self, tr("清空输入区"),
                tr("输入区有 {n} 行仓库地址，清空后不可恢复。\n\n继续吗？").format(n=n),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                return
        text = self.repo_input.toPlainText()
        self.repo_input.clear()
        if text.strip():
            try:
                rec = self.oplog.record(
                    "input_clear", tr("{n} 行地址").format(n=len(text.splitlines())),
                    summary=tr("清空输入区"), before={"text": text}, undoable=True)
                self.offer_undo(rec, tr("已清空输入区"))
            except Exception as e:  # noqa: BLE001 —— 台账失败不该影响清空本身
                self._emit_log(_fmt_dt(), LogLevel.WARN, tr("操作台账写入失败：{e}").format(e=e))

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
            QMessageBox.information(self, tr("提示"), tr("有任务正在运行，请稍后再导入。"))
            return
        # G70：扫描根目录依赖管理页模型，确保已加载
        self._ensure_manage_loaded()
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
            self._emit_log(_fmt_dt(), LogLevel.INFO, tr("已导入 {n} 个本地仓库").format(n=n))
            self.statusBar().showMessage(tr("已导入 {n} 个本地仓库").format(n=n))

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
            QMessageBox.information(self, tr("同步历史"), "\n".join(lines) or tr("仓库不存在或已删除"))
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

    # ------------------------------------------------------------ G57-1 窗口语义档
    @staticmethod
    def _semantic_color(key: str) -> str:
        """取当前主题的语义色（accent2=成功绿 / error=错误红）。

        G69-18：校验条原先写死 Deep 主题的十六进制色 —— Light 主题下
        旧 Primer 绿对白底对比度约 2.4:1（AA 要求 4.5:1），几乎看不清。
        Rev11（V-08）：兜底值不再写死任何 Primer/灰色字面量 —— 直接从
        PALETTES["deep"] 取（模块级常量，无 QApplication 也可用）。
        LBYL（getattr 链）而非嵌套 try —— 异常预算棘轮只降不升。
        """
        from . import theme as _th
        pal = getattr(_th, "current_palette", lambda: {})()
        pal = pal if isinstance(pal, dict) else getattr(_th, "PALETTES", {}).get("deep", {})
        return str(pal.get(key) or "")

    def _on_layout_tick(self) -> None:
        """G69-18：布局防抖定时器到点 → 按当前宽度重排（绑定方法，不成环）。"""
        if not getattr(self, "_layout_bootstrapped", False):
            self._layout_bootstrapped = True
            # 首帧收敛完成 → 后续恢复 150ms 防抖
            timer = getattr(self, "_layout_timer", None)
            if timer is not None:
                timer.setInterval(150)
        self._apply_layout_for_width(self.width())
        # G70：同时按宽/高切换 Compact（隐藏快捷填充与设置导航）
        self._apply_layout_mode()

    def _apply_layout_for_width(self, w: int) -> None:
        """按窗口宽度折叠管理页次要列（**Rev13 R13-03 重写**）。

        原实现有三个真缺陷（本轮实测确认，不是推测）：
        1. `QTableView.horizontalHeaderItem()` **不存在**（那是 `QTableWidget` 的 API）
           → 每次都抛 `AttributeError`，被外层 `except` 吞掉 → **从来没折叠过任何列**；
        2. 它匹配的表头 `"标签"` 在现模型 8 列里**不存在**（仓库/作者/本地路径/上次同步/
           HEAD/历史/健康/大小）；
        3. 阈值 `w < 768` **不可达** —— `effective_minimum()` 的下限是 900
           （`layout.ABSOLUTE_MIN`），窗口不可能窄过 900。

        现在：用 `model().headerData()` 读列名（真实来源），阈值取
        `layout.COMPACT_WIDTH = 1100`（S 档上限），
        只折叠「大小 / 健康」两列（真正的次要数据，主列与「历史」入口不动）。
        """
        from PyQt6.QtCore import Qt as _Qt

        from .layout import COMPACT_WIDTH
        # 异常策略：LBYL（getattr 链）—— 异常预算棘轮只降不升，纯外观修正不加 except。
        table = getattr(self, "manage_table", None)
        if table is None:
            return
        model = table.model()
        if model is None:
            return
        # Rev15 R15-07：S 档（<1100）也隐「大小/健康」，把宽度还给仓库名/路径。
        narrow = int(w) < int(COMPACT_WIDTH)
        hide_names = (tr("大小"), tr("健康"))
        for col in range(model.columnCount()):
            name = model.headerData(
                col, _Qt.Orientation.Horizontal,
                _Qt.ItemDataRole.DisplayRole)
            table.setColumnHidden(col, bool(narrow) and str(name or "") in hide_names)
        # Rev14 R14-06：定宽列按档位重排（窄窗压到下限、L 档偏宽、其余用用户保存值）
        from .layout import BREAKPOINT_L, breakpoint_name
        panel = getattr(self, "manage_panel", None)
        if panel is not None:
            bp = breakpoint_name(int(w), self.height())
            panel.apply_column_budget(narrow, bp == BREAKPOINT_L)

    def resizeEvent(self, e):
        """G57-1 窗口语义档（防抖 150ms）+ G36-5 空态 overlay 跟随表格定位。"""
        try:
            if not hasattr(self, "_layout_timer"):
                from PyQt6.QtCore import QTimer as _QT
                self._layout_timer = _QT(self)
                self._layout_timer.setSingleShot(True)
                # Rev14 V4：**首次**布局用 0 延迟 —— 实测 show() 后 150ms 的去抖
                # 重算会把 24 个控件再挪一次（first_frame_moves=24，全部落在 152ms）。
                # 首帧那一次必须在用户看到之前收敛；之后的拖动/缩放才需要防抖。
                self._layout_timer.setInterval(
                    150 if getattr(self, "_layout_bootstrapped", False) else 0)
                # G69-18 **关键**：这里原先是 lambda 捕获 self ——
                # 平时不 show() 时窗口能按引用计数释放，但一旦 show()，
                # resizeEvent 就会创建这个定时器，lambda 立刻把环重新接上，
                # 窗口又只能等循环 GC。改用绑定方法（PyQt 对 QObject 的绑定方法
                # 用弱引用），show() 之后也仍然按引用计数释放。
                self._layout_timer.timeout.connect(self._on_layout_tick)
            self._layout_timer.start()
        except Exception:
            pass
        # Rev12 U-06：撤销浮条贴在中央控件底部，窗口尺寸变化时必须跟随，
        # 否则拉大窗口后浮条停在原位置（正是 Rev6 U-19 空态 overlay 踩过的坑）。
        try:
            self._position_undo_bar()
        except Exception:  # noqa: BLE001 —— 重定位失败不影响 resize 本身
            pass

    # ------------------------------------------------------------ G49-6 启动隐入托盘
    def should_auto_hide(self) -> bool:
        """--minimized 启动且系统托盘已安装 → 主窗不弹出（托盘常驻）。"""
        try:
            return bool(self.start_hidden) and \
                getattr(self.tray, "tray", None) is not None
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------ 关闭
    def _closing_feedback(self) -> None:
        """G84-22：关窗收尾的可见反馈（先画出来，再阻塞）。

        顺序很重要：`setEnabled(False)` → 状态栏文案 → `processEvents()` 一次 →
        隐藏窗口。前两步让用户知道"它在停"，第三步把它真的画出来，最后隐藏，
        这样之后的阻塞等待发生在**看不见的窗口**上，不会再被误读成"卡死"。
        任何一步失败都不影响关闭本身。
        """
        try:
            self.setEnabled(False)
            self.statusBar().showMessage(tr("正在停止后台任务…"))
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None:
                app.processEvents()      # 让"正在停止"真的画出来
            self.hide()
        except Exception as e:      # noqa: BLE001 —— 反馈失败绝不能拦住关闭
            from ..app.applog import warn as _warn
            _warn(f"关窗反馈渲染失败（继续正常关闭）：{e}")

    def _shutdown_engine_on_close(self) -> None:
        """关窗时的引擎收尾（G84-22 / R7 / R8；从 `closeEvent` 抽出以守住 100 行）。

        三件事：
        1. 先给可见反馈（`_closing_feedback`：置灰 + 状态栏 + 隐藏），再阻塞；
        2. 用 `inspect.signature` 判一次能否传预算，**不靠 `except TypeError` 猜**
           （TypeError 也可能来自 shutdown 内部，那样会跑第二遍 shutdown）；
        3. 预算 1.5s(drain) + 4s(waitForDone) = 5.5s：不敢再短 —— 取消后的 worker
           不收敛就析构 QThreadPool 会以 `0xC0000409` 崩溃（G69-21 实测）；
           反馈已把窗口藏起来，用户不会再把阻塞读成"卡死"。
        """
        try:
            if not hasattr(self, "engine") or self.engine is None:
                return
            self._closing_feedback()
            import inspect

            try:
                params = set(inspect.signature(self.engine.shutdown).parameters)
            except (TypeError, ValueError):
                params = set()
            if {"drain_ms", "wait_ms"} <= params:
                self.engine.shutdown(drain_ms=1500, wait_ms=4000)
            else:
                self.engine.shutdown()
        except Exception as err:      # noqa: BLE001 —— 关闭路径任何异常都不许拦住退出
            # 注意别用 `e`：closeEvent 的形参就叫 `e`（QCloseEvent），在 except 里
            # 遮蔽它会让后面 `e.accept()` 读到"已删除变量"（mypy 抓到过）。
            from ..app.applog import warn as _warn
            _warn(f"引擎收尾异常（继续关闭，进度文件由 G52-2 续跑兜底）：{err}")

    def closeEvent(self, e: QCloseEvent):
        if self.busy:
            ret = QMessageBox.question(
                self, tr("确认退出"),
                tr("有任务正在运行，退出将中断（已下载内容不会丢失）。确定退出？"),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if ret != QMessageBox.StandardButton.Yes:
                e.ignore()
                return
        if hasattr(self, "engine") and self.engine is not None:
            # G69-21：先让还在跑的镜像测速线程尽快退出（它在 UI 线程之外持窗口引用）
            try:
                cancel_evt = getattr(self, "_mirror_probe_cancel", None)
                if cancel_evt is not None:
                    cancel_evt.set()
            except Exception:
                pass
            # G22-1：取消并等待在跑 worker 收敛由 `engine.shutdown()` 统一负责
            # （它内部 drain(4000) + waitForDone(8000)）。
            # G69-22：这里不再额外 drain(8000) —— 实测两段叠加会让关窗最坏
            # 阻塞 8+4+8=20s，且警告文案因 GUI 线程被占无法绘制。
            pass
        self._close_requested = True
        # G71-9：先停本地 API（释放端口 + 唤醒 SSE 长连接线程）
        try:
            self._stop_local_api(wait=True)
        except Exception:
            pass
        # G59-7：停止告警后台扫描器
        try:
            if getattr(self, "alert_scanner", None) is not None:
                self.alert_scanner.stop()
        except Exception:
            pass
        # G69-18：停止定时报表线程（否则关窗后仍会周期性写文件）
        try:
            if getattr(self, "report_scheduler", None) is not None:
                self.report_scheduler.stop()
        except Exception:
            pass
        # flush 剩余节流日志，避免关窗瞬间最后若干行（如"全部任务结束"）丢失
        try:
            self._flush_log_batch()
        except Exception:
            pass
        # G69-18：停掉日志渲染节流定时器。
        # 窗口销毁后若 Qt 事件队列里还排着指向它的 QTimerEvent，
        # 下一次 processEvents() 会在 activateTimers 里解引用已释放对象 → 原生崩溃。
        try:
            if getattr(self, "_log_buffer", None) is not None:
                self._log_buffer.stop()
        except Exception:
            pass
        # G69-18：托盘 tooltip 节流定时器同理（TrayController 现已是 QObject）
        try:
            if getattr(self, "tray", None) is not None:
                self.tray.stop()
        except Exception:
            pass
        # 引擎统一收尾：取消剩余任务、flush 进度、清空未启动队列
        # G84-22：先给**可见反馈**再阻塞。原来直接进 `shutdown()`（drain 4s +
        # waitForDone 8s），GUI 线程被占满 12s —— 用户看到的是"点了叉没反应"，
        # 于是再点几次，甚至去任务管理器强杀（那才是真的会留下半成品目录）。
        # 现在：先把窗口藏起来 + 状态栏写"正在停止…"，泵一次事件让它真的画出来，
        # 再把预算收紧到 drain 1500 + wait 4000（合计 5.5s，见 _shutdown_engine_on_close）。
        self._shutdown_engine_on_close()
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

    # ================================================================ Rev12 可用性接线
    # 尼尔森原则③（撤销）、④（平台惯例菜单栏）、⑩（帮助随时可得）。
    # 全部为**转发**：真实现分别在 ui/undo_bar.py、ui/menu_bar.py、app/undo.py，
    # 主窗只做入口，避免继续往 2980 行的类里加逻辑（CLAUDE.md §6 的增量约束）。

    def menu_undo(self):
        """编辑 → 撤销：撤销最近一条**可撤销**操作。"""
        try:
            from ..app.undo import UndoError, undo_record
            records = self.oplog.recent(1, undoable_only=True)
            if not records:
                self.statusBar().showMessage(tr("没有可撤销的操作"), 4000)
                return
            rec = records[0]
            try:
                summary = undo_record(self.oplog, self, rec)
            except UndoError as e:
                self.statusBar().showMessage(str(e), 6000)
                self._emit_log(_fmt_dt(), LogLevel.WARN, tr("撤销失败：{e}").format(e=e))
                return
            self.statusBar().showMessage(summary, 5000)
            self._emit_log(_fmt_dt(), LogLevel.INFO, summary)
        except Exception as e:  # noqa: BLE001 —— 撤销入口故障不该影响主流程
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("撤销入口异常：{e}").format(e=e))

    def undo_op(self, record_id: str) -> tuple[bool, str]:
        """按 id 撤销（UndoBar 调用）。返回 (是否成功, 给用户看的文案)。"""
        from ..app.undo import UndoError, undo_record
        rec = self.oplog.get(record_id)
        if rec is None:
            return False, tr("找不到这条操作记录（可能已被清理）")
        try:
            summary = undo_record(self.oplog, self, rec)
        except UndoError as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001 —— 逆做失败要给用户可读原因，不抛栈
            return False, tr("撤销失败：{err}").format(err=e)
        self._emit_log(_fmt_dt(), LogLevel.INFO, summary)
        return True, summary

    def offer_undo(self, record, text: str) -> None:
        """展示撤销浮条。`record` 是 `util.oplog.OpRecord`（只取 id）。"""
        try:
            bar = getattr(self, "undo_bar", None)
            if bar is not None and record is not None:
                bar.offer(record.id, text)
        except Exception as e:  # noqa: BLE001 —— 浮条失败不影响操作本身已完成
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("撤销浮条显示失败：{e}").format(e=e))

    def open_oplog_dialog(self):
        """编辑 → 操作历史…：列出最近可撤销记录。"""
        try:
            from .undo_bar import build_oplog_dialog
            build_oplog_dialog(self).exec()
        except Exception as e:  # noqa: BLE001
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("操作历史打开失败：{e}").format(e=e))

    def menu_goto_tab(self):
        """视图 → 切页签（读 sender 的 tab_index 属性，避免 lambda 捕获 self）。"""
        try:
            act = self.sender()
            idx = int(act.property("tab_index")) if act is not None else 0
            self.tabs.setCurrentIndex(max(0, min(self.tabs.count() - 1, idx)))
        except Exception as e:  # noqa: BLE001
            self._emit_log(_fmt_dt(), LogLevel.WARN, tr("切换页签失败：{e}").format(e=e))

    def open_help(self):
        """帮助 → 使用说明（F1）。与设置页「使用说明」走**同一个**槽，防漂移。"""
        return self.settings_panel._open_help()

    def open_data_dir(self):
        """文件 → 打开数据目录。复用设置页实现（唯一真相）。"""
        return self.settings_panel._open_data_dir()

    def show_about(self):
        """帮助 → 关于：版本 + 项目地址 + 数据目录。"""
        import platform

        from .. import __version__
        lines = [
            f"Git-clone-Max {__version__}",
            tr("批量并行克隆 + 增量更新 + 入库追踪"),
            "",
            f"Python {platform.python_version()} · {platform.system()} {platform.release()}",
            f"{tr('数据目录')}：{self.data_dir}",
        ]
        QMessageBox.information(self, tr("关于"), "\n".join(lines))

    def _position_undo_bar(self) -> None:
        """按「中央控件底部按钮行高度」定位撤销浮条（Rev12 U-06）。

        为什么不能写死偏移：下载页底部有一排按钮（打开克隆目录 / 失败汇总 /
        重试队列 / 诊断包 …），实测写死 24px 会把它们整排盖住 —— 可用性改进
        反而制造可用性缺陷。这里取最后一个 QPushButton 的底边作为避让基准，
        取不到时退回默认 24px（布局异常也不该让浮条消失）。
        """
        bar = getattr(self, "undo_bar", None)
        cw = self.centralWidget()
        if bar is None or cw is None:
            return
        offset = 24
        try:
            from PyQt6.QtWidgets import QPushButton
            lowest = 0
            for btn in cw.findChildren(QPushButton):
                # 必须排除浮条**自身及其子控件** —— findChildren 会递归到撤销/关闭
                # 按钮，它们的坐标是相对浮条的，混进来会把避让算成 0（实测过）。
                if btn is bar or bar.isAncestorOf(btn) or not btn.isVisible():
                    continue
                # 只关心「贴近底部」的按钮行，中间的按钮不参与
                geo = btn.geometry()
                top = btn.mapTo(cw, geo.topLeft()).y()
                bottom = top + geo.height()
                if bottom > cw.height() - 160:
                    lowest = max(lowest, bottom)
            if lowest:
                offset = cw.height() - lowest + 8
        except Exception as e:  # noqa: BLE001 —— 定位失败退回默认值即可
            from ..app.applog import warn as _warn
            _warn(f"撤销浮条避让计算失败，使用默认偏移：{e}")
            offset = 24
        bar._bottom_offset = offset
        bar.reposition(cw.rect(), bottom_offset=offset)
