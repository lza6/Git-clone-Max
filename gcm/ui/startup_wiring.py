"""启动期后台装配（Rev17 T4）——从 `MainWindow.__init__` 纯搬移。

**为什么独立模块**：`__init__` 269 行全库第一，其中「引擎构建之后的 4 个后台
服务装配 + 启动期定时器 + 凭据提示」约 120 行与控件装配无关，是可整体搬移的
一段。照 `engine_factory.py` 先例：本模块只做装配、不持有状态，返回 None
（所有状态挂在 owner 上，与搬移前完全一致）。

**铁律**：纯搬移不改逻辑。搬移来源行以注释标出原 `__init__` 区段名；
引用环纪律沿袭原实现（`_scan_repos` 闭包用弱引用 `me`，不用 lambda 捕获 self）。
守卫：`TestStartupEngineReflectsSettings`（4 例）+ `TestG69_18NoMainWindowRefCycle`
必须原样绿 —— 搬移不改这两个测试的断言路径。
"""
from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 仅类型检查用
    from .main_window import MainWindow

from ..app.applog import warn as _applog_warn
from ..i18n import tr
from ..models import SyncResult, SyncStatus  # noqa: F401  （类型注解兼容）
from .theme import LogLevel


def wire_background_services(owner: MainWindow) -> None:
    """装配 4 个后台服务 + 启动定时器（原 `__init__` 「跨会话续跑」至「凭据提示」段）。

    纯搬移边界：从 `# G52-2/3：跨会话续跑清单` 注释起，到 `crypto_warning` 段止。
    """
    from ..app.watchdog import Watchdog
    from ..db.runtime_sessions import RuntimeSessionStore

    # G52-2/3：跨会话续跑清单 + 看门狗（数据目录 session_runtime.json）
    owner.session_store = RuntimeSessionStore(owner.data_dir / "session_runtime.json")
    owner.watchdog = Watchdog(
        owner.data_dir / "session_runtime.json",
        interval_min=int(getattr(owner.settings, "watchdog_interval_min", 5) or 5),
        silent=bool(getattr(owner.settings, "watchdog_silent", False)),
        engine=owner.engine, parent=owner)
    owner.watchdog.resume_requested.connect(owner._on_watchdog_resume)
    if bool(getattr(owner.settings, "watchdog_enabled", False)):
        owner.watchdog.start()
    # G59-7：阈值告警后台扫描器（G53-2 落地；开启时启动，支持企业 IM webhook 推送）
    owner.alert_scanner = None
    try:
        # G69-18：扫描闭包被 AlertScanner 长期持有 —— 直接捕获 self 同样会
        # 构成引用环（scanner ← self ← scanner）。改用弱引用，窗口销毁后
        # 扫描自然返回空，不再需要 GC 才能在窗口死前回收。
        import weakref

        from ..app.alerts import AlertScanner, scan_alerts
        _self_ref = weakref.ref(owner)

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

        owner.alert_scanner = AlertScanner(
            _scan_repos,
            interval_sec=max(60.0, float(getattr(owner.settings, "g53_alerts_interval_min", 60) or 60)) * 60.0,
            im_channel=str(getattr(owner.settings, "alert_im_channel", "") or "").strip(),
            im_url=str(getattr(owner.settings, "alert_im_url", "") or "").strip(),
            im_token=str(getattr(owner.settings, "alert_im_token", "") or "").strip(),
            # G69-18 修复：此前未接 on_alerts → 告警命中后除（多半未配置的）IM 外
            # 用户**全程无感**：不弹通知、不写日志、UI 无任何痕迹。
            # 现改为「worker emit → 主线程槽」：扫描在后台线程，绝不直接碰控件。
            #
            # 用弱引用 + 存活标志双重保护：扫描线程比窗口活得久时（测试里
            # 直接丢引用、或 closeEvent 之后线程仍在收尾），对**已销毁**的
            # QObject 调 emit 会让进程原生崩溃（SIGSEGV），而不是抛 Python 异常。
            on_alerts=owner._make_alert_emitter_ref())
        if bool(getattr(owner.settings, "g53_alerts_enabled", False)):
            owner.alert_scanner.start()
    except Exception:
        owner.alert_scanner = None

    # G69-18 修复：定时报表此前**整条链路是假的** —— 设置页有「启用定时报表」
    # 勾选框，`ReportScheduler` 却无任何生产实例化点（只在 tests 里被构造）。
    # 勾了等于没勾。现按设置真实启动，并在 closeEvent 里随窗口收敛。
    owner.report_scheduler = None
    owner._start_report_scheduler()
    # G80-B7-1：登记 4 个后台任务（自动更新/看门狗/告警/报表）的开关与间隔，
    # 供「后台任务」面板展示 —— 它们此前在 UI 上完全不可见。
    try:
        from ..app.background_jobs import register_default_jobs
        register_default_jobs(owner.settings)
    except Exception as e:  # noqa: BLE001 - 登记失败不该影响启动
        _applog_warn(f"后台任务登记失败：{e}")

    # G80-B7-3 / S3：定时同步队列编排（有启用的队列才会真正 tick 起来）
    try:
        owner._setup_queue_runner()
    except Exception as e:  # noqa: BLE001 - 队列接线失败不该影响启动
        _applog_warn(f"定时同步队列接线失败：{e}")

    # 凭据存储强度：`crypto_warning()` 会说明当前环境是否只做了「可逆混淆」。
    # 该函数早已实现却从未被调用 —— 而 README 无条件宣称「token 加密落盘」。
    # 兜底后端（xorfixed）下必须让用户在日志里看得见。
    try:
        from ..db.settings import crypto_warning
        _warn = crypto_warning()
        if _warn:
            owner._emit_log(time.strftime("%Y-%m-%d %H:%M:%S"), LogLevel.WARN,
                            tr("凭据存储：") + _warn)
    except Exception:
        pass


def wire_auto_update_timer(owner: MainWindow) -> None:
    """自适应自动更新定时器（原 `__init__` 「G37-4 自动更新」段，纯搬移）。"""
    from PyQt6.QtCore import QTimer

    # G37-4 自动更新定时器（分钟级；engine 非 busy 时触发一键更新）
    # G83 P1-1：自适应退避状态（连续无变更轮数；None=尚无历史）
    owner._auto_update_no_change_streak = None
    owner._auto_update_batch_had_changes = False
    owner._auto_update_timer = QTimer(owner)
    owner._auto_update_timer.timeout.connect(owner._on_auto_update_tick)
    owner._restart_auto_update_timer()


def record_startup_elapsed(owner: MainWindow) -> None:
    """启动耗时写回（原 `__init__` 「G33-6 记录」行，纯搬移）。"""
    owner._startup_ms = int((time.perf_counter() - owner._startup_start) * 1000)
