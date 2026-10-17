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
            host_auth_types=getattr(self.settings, "host_auth_types", None) or {},  # G58-5
            precheck_remote=bool(getattr(self.settings, "precheck_remote", False)),  # G38-3
            single_branch=bool(getattr(self.settings, "single_branch", False)),  # G38-4
            force_ipv4=bool(getattr(self.settings, "force_ipv4", False)),  # G38-6
            lfs_enabled=bool(getattr(self.settings, "lfs_enabled", False)),  # G48-2
            post_clone_hook=str(getattr(self.settings, "post_clone_hook", "")),  # G48-5
            ssh_key=str(getattr(self.settings, "ssh_key_path", "")),  # G48-3/G67-16 接线
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
        # G54-4：清单**不**在这里定时 —— 向导会开嵌套事件循环（dlg.exec()），
        # 600ms 的定时器会在那个嵌套循环里触发，导致清单叠在向导上面。
        # 改为：向导函数返回后再调用（见 _maybe_show_onboarding 末尾）。
        QTimer.singleShot(500, self._maybe_apply_system_theme)
        # G37-4 自动更新定时器（分钟级；engine 非 busy 时触发一键更新）
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
                           f"定时报表已启用：每 {int(getattr(self.settings, 'sched_report_interval_min', 1440) or 1440)} "
                           f"分钟 → {out_dir}")
        except Exception as exc:  # noqa: BLE001
            # 启动失败要留痕，不能静默 —— 否则又是「勾了没反应」
            self.report_scheduler = None
            self._emit_log(_fmt_dt(), LogLevel.WARN, f"定时报表启动失败：{exc}")

    def _restart_auto_update_timer(self):
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
            self._emit_log(_fmt_dt(), LogLevel.WARN, f"引导清单打开失败：{exc}")
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
        """G36-4 当前窗口几何 → "WxH+X+Y"（含最大化状态前缀 M:）。

        最大化时**连普通几何的位置一起存**：原实现只写 `M:WxH`（2 个数字），
        而 `_restore_geometry` 无条件按 4 个值解包 → `ValueError` 被 `except` 吞掉
        → 最大化用户每次启动都恢复不了（实测复发）。
        """
        try:
            if self.isMaximized():
                ng = self.normalGeometry()          # 最大化前的普通几何（含位置）
                return f"M:{ng.width()}x{ng.height()}+{ng.x()}+{ng.y()}"
            g = self.geometry()
            return f"{g.width()}x{g.height()}+{g.x()}+{g.y()}"
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
            parts = [int(v) for v in raw.replace("x", " ").replace("+", " ").split()]
            if len(parts) == 2:                 # 旧版 `M:WxH` 格式：只有尺寸
                w, h = parts
                x = y = None
            elif len(parts) >= 4:               # 标准 `WxH+X+Y`
                w, h, x, y = parts[:4]
            else:
                return                          # 格式不认识：保持默认几何
            # 防小屏溢出：只接受最小尺寸以上
            w = max(w, 1020)
            h = max(h, 700)
            self.resize(w, h)
            if x is None or y is None or not self._screen_contains_point(x, y):
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
        # G54-1 实时校验条：输入/粘贴后显示「有效 N / 无效 M」，
        # 并指出前几条无效行。解析用与克隆链路同一个 parse_any_repo_url。
        self.repo_valid_lbl = QLabel("")
        self.repo_valid_lbl.setWordWrap(True)
        # 内容含用户粘贴的原文，强制纯文本，避免 <...> 被当富文本渲染
        self.repo_valid_lbl.setTextFormat(Qt.TextFormat.PlainText)
        b.addWidget(self.repo_valid_lbl)
        self._validate_timer = QTimer(self)
        self._validate_timer.setSingleShot(True)
        self._validate_timer.setInterval(300)      # 输入防抖，避免每次按键都解析
        self._validate_timer.timeout.connect(self._refresh_url_validation)
        self.repo_input.textChanged.connect(self._on_input_changed)
        # G02-4 右键菜单：从最近历史回填 / 清空历史
        self.repo_input.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.repo_input.customContextMenuRequested.connect(self._repo_input_menu)
        # G35-3 拖拽导入：.txt/.csv → 读入每行地址；目录 → 本地扫描检测
        self.repo_input.setAcceptDrops(True)
        # G69-18：**不能**把绑定方法直接挂成控件属性 —— 控件持有绑定方法、
        # 绑定方法持有窗口、窗口又持有控件 → 引用环。改为弱引用包装，
        # 窗口销毁后 handler 自动失效（Qt 不会再调它，因为控件也随之销毁）。
        _self_ref = weakref.ref(self)

        def _drag_enter(e):
            me = _self_ref()
            if me is not None:
                me._repo_input_drag_enter(e)

        def _drop(e):
            me = _self_ref()
            if me is not None:
                me._repo_input_drop(e)

        self.repo_input.dragEnterEvent = _drag_enter
        self.repo_input.dropEvent = _drop
        quick = QHBoxLayout()
        quick.addWidget(QLabel(tr("快捷填充：")))
        # G35-7 快捷填充可配置：跟随 settings.quick_repos（持久化，可编辑）
        for repo in getattr(self.settings, "quick_repos", None) or ():
            btn = QPushButton(repo)
            # G69-18：**不要**用 lambda 捕获 self —— 控件持有连接、连接持有闭包、
            # 闭包持有 MainWindow，而 MainWindow 又持有控件 → 引用环 → 只能靠循环 GC
            # 回收 → 回收时机不可控（可能在 Qt 分发事件中途）→ 原生崩溃。
            # PyQt 对**绑定方法**用弱引用（不建环），参数经控件属性传递。
            btn.setProperty("quick_repo", repo)
            btn.clicked.connect(self._on_quick_fill)
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
        # G69-18：绑定方法替代 lambda（避免引用环，见 _on_quick_fill 处说明）
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
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
        # G52-5：失败原因聚合面板入口
        self.btn_failures = QPushButton(tr("失败汇总"))
        self.btn_failures.clicked.connect(self._open_failure_dialog)
        btns.addWidget(self.btn_failures)
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
        """手动检查更新：网络请求放工作线程，结果经 `update_checked` 回 GUI 线程弹窗。

        G67-9：原实现在按钮回调里**同步**跑 `check_latest()`，而它内部失败时会
        `time.sleep(2)` 重试一次 —— 点击后界面最长冻结 2s+（弱网/被墙时更久）。
        现在与 G67-2 的 archive/notify/diag 同款：worker 只发信号，不碰任何控件。
        """
        if getattr(self, "_update_checking", False):
            return  # 防连点：worker 未回前不重复发起
        self._update_checking = True
        try:
            self.statusBar().showMessage("正在检查更新…", 3000)
        except Exception:
            pass

        def _worker():
            try:
                from ..app.updater import check_latest
                has_new, ver, url, err = check_latest()
            except Exception as e:  # noqa: BLE001 —— 兜底成 err，绝不从线程抛出
                has_new, ver, url, err = False, "", "", f"检查更新失败：{e}"
            # 跨线程 emit：Qt 自动走 QueuedConnection，槽在 GUI 线程执行
            self.update_checked.emit(bool(has_new), str(ver or ""), str(url or ""), str(err or ""))

        threading.Thread(target=_worker, daemon=True, name="gcm-update-check").start()

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

    def _on_update_checked(self, has_new: bool, ver: str, url: str, err: str):
        """`update_checked` 槽（GUI 线程）：负责弹窗与打开下载页。"""
        self._update_checking = False
        from .. import __version__
        msg = (err or (f"发现新版本 {ver}，可到设置页下载" if has_new
                       else f"已是最新版本（{__version__}）"))
        if not self._can_show_modal():
            # 没有可见窗口 → 不弹模态（会永久阻塞），改为写日志留痕
            try:
                self._emit_log(_fmt_dt(), LogLevel.INFO, f"检查更新：{msg}")
            except Exception:
                pass
            return
        try:
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
        except Exception as e:  # noqa: BLE001
            QMessageBox.information(self, "检查更新", f"检查更新失败：{e}")

    def _check_update_silent(self):
        """启动后静默后台检查更新：不阻塞、不弹窗；有新版本写日志 + 托盘提示。

        与手动按钮 check_update_now 共用 check_latest，只是结果呈现方式不同
        （err 静默返回；发现新版只记日志，需要时用户可去设置页手动操作）。

        G67-9：整段（含 HTTP 与其内部 2s 重试）挪进 daemon 线程 —— 原先由
        `QTimer.singleShot(2500, ...)` 在 **GUI 线程**调用，启动后 2.5s 的主线程
        同步网络请求会让窗口短暂无响应（弱网时更明显）。线程内不做任何 Qt 调用，
        仅经 `_emit_log`（已具备线程安全约定）与 tray 通知回主线程。
        """
        def _worker():
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
                    # G67-9：**工作线程内只能走 emit 桥**，不得直接调 _emit_log
                    self.emit_log_from_worker(
                        _fmt_dt(), LogLevel.INFO,
                        f"发现新版本 {ver}（当前 {__version__}），可到设置页检查更新。")
                    try:
                        if getattr(self, "tray", None) is not None:
                            self.tray.notify("Git-clone-Max 有新版本",
                                             f"最新 {ver}，可下载更新")
                    except Exception:
                        pass
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True, name="gcm-update-silent").start()

    def save_log(self):
        from .download_tools import save_log as _sl
        _sl(self, _fmt_dt)

    # ------------------------------------------------------------ 动作
    def _repo_input_menu(self, pos):
        """输入框右键菜单：历史回填 / 清空 / G48-7 归档下载。"""
        from .download_tools import repo_input_menu as _rim

        def _extra(menu):
            act = menu.addAction("📦 归档下载（zip，免入库）")
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
            self._emit_log(_fmt_dt(), LogLevel.WARN, "输入区为空，无法归档下载。")
            return
        # G67-2 附带修复：原实现固定取 lines[0]，与右键菜单文案「光标所在行」不符。
        cursor = self.repo_input.textCursor()
        idx = max(0, min(cursor.blockNumber(), len(lines) - 1))
        url = lines[idx]
        try:
            from gcm.git.archive import download_archive
        except Exception:
            self._emit_log(_fmt_dt(), LogLevel.WARN, "归档模块不可用。")
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
                self.archive_finished.emit(str(dest), True, f"归档下载完成：{dest}")
            except Exception as e:
                self.archive_finished.emit(str(dest), False, f"归档下载失败：{e}")

        import threading
        threading.Thread(target=_run, daemon=True).start()
        self.statusBar().showMessage("归档下载中…")

    def _on_archive_finished(self, dest: str, ok: bool, message: str):
        """G67-2：归档完成回主线程（GUI 线程）后更新日志与状态栏。"""
        self._emit_log(_fmt_dt(), LogLevel.INFO if ok else LogLevel.WARN, message)
        self.statusBar().showMessage(f"归档已保存：{dest}" if ok else message)

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
            self.statusBar().showMessage(f"阈警：{summary}", 8000)
            self._emit_log(_fmt_dt(), LogLevel.WARN,
                           f"阈值告警命中 {len(alerts)} 条：{summary}")
        except Exception as exc:  # noqa: BLE001
            # 告警渲染失败绝不能连带影响扫描线程的收尾；
            # 但**不能静默** —— 否则整批告警无声无息，比不显示更难查。
            try:
                self._emit_log(_fmt_dt(), LogLevel.WARN,
                               f"告警渲染失败（{type(exc).__name__}），已跳过本批")
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
            DiagReportDialog(title, body, copy_text, self).exec()
        except Exception as exc:  # noqa: BLE001
            # 对话框起不来不能把诊断结果一起吞掉 —— 退化为纯文本
            from PyQt6.QtWidgets import QMessageBox
            self._emit_log(_fmt_dt(), LogLevel.WARN, f"诊断对话框打开失败：{exc}")
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
            self._emit_log(_fmt_dt(), LogLevel.WARN, f"读取通知审计失败：{exc}")
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
            DiagReportDialog(tr("通知审计（最近 200 条）"), body, body, self).exec()
        except Exception as exc:  # noqa: BLE001
            from PyQt6.QtWidgets import QMessageBox
            self._emit_log(_fmt_dt(), LogLevel.WARN, f"通知审计对话框打开失败：{exc}")
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
            return
        bad: list[tuple[str, str]] = []
        for ln in lines:
            err = maybe_validate_url(ln)
            if err:
                bad.append((ln, err))
        ok = len(lines) - len(bad)
        if not bad:
            self.repo_valid_lbl.setText(
                tr("有效 {n} / 无效 0").replace("{n}", str(ok)))
            self.repo_valid_lbl.setStyleSheet(
                f"color:{self._semantic_color('accent2')};")
            return
        shown = "；".join(ln[:40] for ln, _e in bad[:3])
        more = tr("（等 {n} 条）").replace("{n}", str(len(bad))) if len(bad) > 3 else ""
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

    def _on_mode_changed(self, index: int) -> None:
        """克隆模式变化 → 仅「浅克隆」相关档位启用深度微调（G69-18：绑定方法）。"""
        self.depth_spin.setEnabled(index in (1, 2))

    def _apply_settings_filter(self, keyword: str) -> None:
        """G69-18：设置页搜索过滤（原为 settings_groups 内的闭包，会捕获 owner 成环）。"""
        from PyQt6.QtWidgets import QLabel
        kw = (keyword or "").strip().lower()
        groups = list(getattr(self, "settings_groups", []) or []) + list(
            getattr(self, "settings_groups_extra", []) or [])
        for group in groups:
            if not kw:
                group.setVisible(True)
                continue
            title = group.title().lower()
            labels = [lbl.text().lower() for lbl in group.findChildren(QLabel)]
            group.setVisible(kw in title or any(kw in x for x in labels if x))

    def _toggle_host_tokens_echo(self, checked: bool) -> None:
        """G44-1：按平台凭据「显示明文」勾选切换掩码（G69-18：绑定方法）。"""
        self.edit_host_tokens.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)

    def _on_adapt_changed(self, n: int, text: str):
        """G48-1：并发自适应状态栏提示。"""
        try:
            rate = getattr(self.engine, "batch_fail_rate", None)
            if rate is not None:
                text = f"{text}（近批失败率 {rate:.0f}%）"
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
        # G69-4 修复：**先判 busy 再 pop**。原实现先 `pop(0)` 再 `if self.busy: return`，
        # 被 pop 掉的那一组根目录再也不会被拉取（每次调用丢掉一组，用户完全看不到报错）。
        if self.busy:
            return  # 当前组仍在运行，等 finished 槽再拉下一组
        root, rows = self._multi_root_update.pop(0)
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
            host_auth_types=getattr(self.settings, "host_auth_types", None) or {},  # G58-5
            precheck_remote=bool(getattr(self.settings, "precheck_remote", False)),  # G38-3
            single_branch=bool(single_branch),  # G38-4（UI 模式 2 或设置）
            force_ipv4=bool(getattr(self.settings, "force_ipv4", False)),  # G38-6
            lfs_enabled=bool(getattr(self.settings, "lfs_enabled", False)),  # G48-2
            post_clone_hook=str(getattr(self.settings, "post_clone_hook", "")),  # G48-5
            ssh_key=str(getattr(self.settings, "ssh_key_path", "")),  # G48-3/G67-16 接线
            mirror=bool(mirror),  # G48-6
            # G52-1 失败自动重试队列（设置开启且网络类错误才入队）
            retry_queue_path=self.data_dir / "retry_queue.json",
            retry_enabled=bool(getattr(self.settings, "retry_enabled", False)),
            retry_max=int(getattr(self.settings, "retry_max", 2) or 2),
            retry_backoff_base_sec=int(getattr(self.settings, "retry_backoff_base_sec", 30) or 30),
            # G52-2 跨会话续跑清单
            session_store=self.session_store,
            # G52-3 看门狗参数（MainWindow 持有 Watchdog 实例）
            watchdog_enabled=bool(getattr(self.settings, "watchdog_enabled", False)),
            watchdog_interval_min=int(getattr(self.settings, "watchdog_interval_min", 5) or 5),
            # G52-4 进度文件治理
            progress_gc=self._progress_gc_mod(),
            # G52-6 并发自适应记忆
            concurrency_state_path=self.data_dir / "concurrency_state.json",
        )
        self.watchdog.set_engine(self.engine)
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
    def _open_failure_dialog(self):
        """打开「失败原因聚合」对话框（按错误类别聚合 + 可重试该类别）。"""
        try:
            from .failure_panel import FailureDialog
            dlg = FailureDialog(owner=self, parent=self)
            dlg.retry_category.connect(self._on_failure_category_retry)
            dlg.exec()
        except Exception as e:
            self._emit_log(_fmt_dt(), LogLevel.WARN, f"失败汇总打开失败：{e}")

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
                self._emit_log(_fmt_dt(), LogLevel.INFO, "该类无失败记录，跳过重试")
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
            self._emit_log(_fmt_dt(), LogLevel.WARN, f"失败类别重试失败：{e}")

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
                tr(f"上次会话有 {len(specs)} 个任务未完成：\n{names}\n\n是否续跑？"),
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
                               f"看门狗检测到 {len(specs)} 个未完成任务，自动续跑")
            root = Path(getattr(self.settings, "download_dir", "") or self.data_dir / "clones")
            self._launch(specs, target_root=root, shallow=False, depth=1,
                         clear_input=False, single_branch=False)
        except Exception as e:
            self._emit_log(_fmt_dt(), LogLevel.WARN, f"看门狗续跑失败：{e}")

    def _on_engine_finished(self):
        """G45-6 转发 progress_table.py（行为零变化；G52-3 复位看门狗）。"""
        try:
            if getattr(self, "watchdog", None) is not None:
                self.watchdog.reset()
        except Exception:
            pass
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

    # ------------------------------------------------------------ G57-1 窗口语义档
    @staticmethod
    def _semantic_color(key: str) -> str:
        """取当前主题的语义色（accent2=成功绿 / error=错误红）。

        G69-18：校验条原先写死 Deep 主题的十六进制色 —— Light 主题下
        `#3fb950` 对白底对比度约 2.4:1（AA 要求 4.5:1），几乎看不清。
        """
        try:
            from . import theme as _th
            return str(_th.current_palette().get(key) or "#888888")
        except Exception:
            return "#888888"

    def _on_layout_tick(self) -> None:
        """G69-18：布局防抖定时器到点 → 按当前宽度重排（绑定方法，不成环）。"""
        self._apply_layout_for_width(self.width())

    def _apply_layout_for_width(self, w: int) -> None:
        """G57-1：按窗口宽度切换布局语义（S<768 / M / D / XL）。窄窗折叠次要列。"""
        try:
            narrow = w < 768
            if hasattr(self, "manage_table"):
                for col in range(self.manage_table.columnCount()):
                    hdr = self.manage_table.horizontalHeaderItem(col)
                    hide = narrow and hdr is not None and hdr.text() in ("大小", "健康", "标签")
                    self.manage_table.setColumnHidden(col, bool(hide))
        except Exception:
            pass

    def resizeEvent(self, e):
        """G57-1 窗口语义档（防抖 150ms）+ G36-5 空态 overlay 跟随表格定位。"""
        try:
            if not hasattr(self, "_layout_timer"):
                from PyQt6.QtCore import QTimer as _QT
                self._layout_timer = _QT(self)
                self._layout_timer.setSingleShot(True)
                self._layout_timer.setInterval(150)
                # G69-18 **关键**：这里原先是 lambda 捕获 self ——
                # 平时不 show() 时窗口能按引用计数释放，但一旦 show()，
                # resizeEvent 就会创建这个定时器，lambda 立刻把环重新接上，
                # 窗口又只能等循环 GC。改用绑定方法（PyQt 对 QObject 的绑定方法
                # 用弱引用），show() 之后也仍然按引用计数释放。
                self._layout_timer.timeout.connect(self._on_layout_tick)
            self._layout_timer.start()
        except Exception:
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
