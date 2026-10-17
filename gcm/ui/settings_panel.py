"""设置页面板（G33-8 拆分）：从 MainWindow._build_settings_tab 提取。

职责：构建「启动与后台运行 / 并行与网络 / 外观 / 关于与更新 / 黑匣子日志」全部控件，
控件名与原 MainWindow 属性保持一致（self.spin_concurrency 等），由 MainWindow 组合挂载。
行为零变化：信号连接回调由 MainWindow 提供（_save_* / 对话框入口）。
"""
from __future__ import annotations

import threading
import weakref
from typing import TYPE_CHECKING

from PyQt6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr  # G49-1
from . import main_window as _mw  # noqa: F401 - 迁移回调引用 main_window 模块级符号
from .theme import LogLevel, make_highlighter

if TYPE_CHECKING:  # 仅类型标注（避免循环导入）
    pass
from .main_window import MainWindow

# G68-1：设置页各分组的构建器已拆到 `settings_groups.py`（原 `build_settings_ui` 单函数
# **432 行**，是项目 50 行上限的 8.6 倍）。这里原样再导出，`main_window` 的导入路径不变；
# 三个文本辅助函数（_host_tokens_text 等）随之迁走，仅该处使用。
from .settings_groups import build_settings_ui  # noqa: F401


def build_log_box(owner: MainWindow) -> QGroupBox:
    """构建黑匣子日志区（固定在下，控件挂 owner.log_count/log_view/btn_save_log/btn_clear_log）。"""
    g2 = QGroupBox(tr("黑匣子日志（实时）"))
    g2.setToolTip(tr("应用运行日志实时输出；可导出为文本文件排查问题"))
    l2 = QVBoxLayout(g2)
    tb = QHBoxLayout()
    owner.log_count = QLabel(tr("0 条"))
    owner.log_count.setObjectName("muted")
    tb.addWidget(owner.log_count)
    tb.addStretch()
    owner.btn_save_log = QPushButton(tr("导出日志…"))
    owner.btn_save_log.setToolTip(tr("把当前日志内容导出为文本文件"))
    owner.btn_save_log.clicked.connect(owner.save_log)
    owner.btn_clear_log = QPushButton(tr("清空日志"))
    owner.btn_clear_log.clicked.connect(owner.clear_log)
    tb.addWidget(owner.btn_save_log)
    tb.addWidget(owner.btn_clear_log)
    l2.addLayout(tb)
    owner.log_view = QPlainTextEdit()
    owner.log_view.setObjectName("console")
    owner.log_view.setReadOnly(True)
    owner.log_view.setMaximumBlockCount(4000)
    owner.log_view.setMinimumHeight(100)
    l2.addWidget(owner.log_view, 1)
    # 关键字高亮：`theme.make_highlighter` 早已实现，但此前从未安装到日志视图
    # （README 宣称的「关键字高亮」实际不可见）。保留在 owner 上，避免包装对象被回收。
    try:
        owner.log_highlighter = make_highlighter(owner.log_view.document())
    except Exception:
        owner.log_highlighter = None
    return g2



class SettingsPanel(QWidget):
    """G45-6 设置回调面板：_save_* 等设置持久化回调迁入（MainWindow 保留同名转发）。

    设置控件仍构建在 MainWindow 上（build_settings_ui(owner)）；本面板仅持有回调，
    经 owner 访问窗口属性（settings / settings_store / engine / edit_proxy 等）。
    对外方法名与 MainWindow 保持一致。
    """

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        # owner 允许为 None（测试会构造无宿主的面板单独验证数据逻辑）
        self._owner_ref = (weakref.ref(owner) if owner is not None
                           else (lambda: None))

    @property
    def owner(self):
        """G69-18：**弱引用**持有宿主，避免 Panel↔MainWindow 引用环。

        原实现 `self.owner = owner` 是强引用：MainWindow 持有 Panel、Panel 又持有
        MainWindow → 环 → 只能靠循环 GC 回收 → 回收时机不可控（可能在 Qt 分发
        事件中途）→ 原生崩溃。弱引用后窗口由引用计数立即释放。
        """
        return self._owner_ref()

    @staticmethod
    def _parse_kv_text(text: str) -> dict:
        """解析多行 `key=value` 文本 → dict；空 value 行删除条目，非法行忽略。"""
        out: dict = {}
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().lower()
            value = value.strip()
            if not key or not value:
                continue
            out[key] = value
        return out

    # ---- G53 数据洞察保存（评分卡/告警/历史保留/定时报表）
    def _apply_alert_config(self):
        """把当前设置推给运行中的 AlertScanner（间隔/IM 渠道即时生效）。"""
        scanner = getattr(self.owner, "alert_scanner", None)
        if scanner is None:
            return
        s = self.owner.settings
        try:
            scanner.reconfigure(
                interval_sec=max(60.0, float(getattr(s, "g53_alerts_interval_min", 60) or 60)) * 60.0,
                im_channel=str(getattr(s, "alert_im_channel", "") or ""),
                im_url=str(getattr(s, "alert_im_url", "") or ""),
                im_token=str(getattr(s, "alert_im_token", "") or ""))
        except Exception:
            pass

    def _save_g53_alerts(self, checked):
        """G69-18：切换告警开关必须真实启停扫描器。

        此前只写 settings —— 勾了要重启才生效（与定时报表同一类断链）。
        """
        self.owner.settings.g53_alerts_enabled = bool(checked)
        self.owner.settings_store.save(self.owner.settings)
        scanner = getattr(self.owner, "alert_scanner", None)
        if scanner is None:
            return
        self._apply_alert_config()
        try:
            scanner.start() if checked else scanner.stop()
        except Exception:
            pass

    def _save_g53_interval(self, value):
        self.owner.settings.g53_alerts_interval_min = int(value)
        self.owner.settings_store.save(self.owner.settings)
        self._apply_alert_config()

    def _save_g53_history(self, value):
        self.owner.settings.history_retention_days = int(value)
        if hasattr(self.owner.db, "set_history_retention_days"):
            try:
                self.owner.db.set_history_retention_days(int(value))
            except Exception:
                pass
        self.owner.settings_store.save(self.owner.settings)

    def _clean_g53_history(self):
        """G53-4 立即清理历史（按当前保留天数）。"""
        try:
            days = int(self.owner.settings.history_retention_days or 90)
            n = self.owner.db.prune_history(days) if hasattr(self.owner.db, "prune_history") else 0
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self.owner, "清理完成", f"已清理 {n} 条历史记录（保留 {days} 天）")
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self.owner, "清理失败", str(e))

    def _save_g53_sched(self, checked):
        self.owner.settings.sched_report_enabled = bool(checked)
        self.owner.settings_store.save(self.owner.settings)
        # G69-18：开关必须真的驱动调度器（此前只存字段，调度器从不启动）
        if hasattr(self.owner, "_start_report_scheduler"):
            self.owner._start_report_scheduler()

    def _save_g53_sched_interval(self, value):
        self.owner.settings.sched_report_interval_min = int(value)
        self.owner.settings_store.save(self.owner.settings)
        if hasattr(self.owner, "_start_report_scheduler"):
            self.owner._start_report_scheduler()

    def _save_g53_sched_dir(self):
        edit = getattr(self.owner, "edit_g53_sched_dir", None)
        self.owner.settings.sched_report_dir = edit.text().strip() if edit else ""
        self.owner.settings_store.save(self.owner.settings)
        if hasattr(self.owner, "_start_report_scheduler"):
            self.owner._start_report_scheduler()

    def _save_retry_enabled(self, checked):
        """G69-18：失败自动重试开关（此前字段有、UI 无 → 用户永远开不了）。"""
        self.owner.settings.retry_enabled = bool(checked)
        self.owner.settings_store.save(self.owner.settings)

    def _save_retry_max(self, value):
        self.owner.settings.retry_max = int(value)
        self.owner.settings_store.save(self.owner.settings)

    def _save_watchdog_enabled(self, checked):
        """G69-18：看门狗开关（同上）。"""
        self.owner.settings.watchdog_enabled = bool(checked)
        self.owner.settings_store.save(self.owner.settings)
        wd = getattr(self.owner, "watchdog", None)
        if wd is not None:
            try:
                wd.start() if checked else wd.stop()
            except Exception:
                pass

    def _save_watchdog_interval(self, value):
        self.owner.settings.watchdog_interval_min = int(value)
        self.owner.settings_store.save(self.owner.settings)
        # G69-18：改间隔要立刻作用到运行中的定时器，否则要重启才生效
        wd = getattr(self.owner, "watchdog", None)
        if wd is not None:
            try:
                wd.interval_min = int(value)
            except Exception:
                pass

    def _save_alert_im(self):
        """G69-18：企业 IM 告警渠道/URL/Token（此前无 UI → 不可达）。"""
        ch = getattr(self.owner, "combo_im_channel", None)
        url = getattr(self.owner, "edit_im_url", None)
        tok = getattr(self.owner, "edit_im_token", None)
        self.owner.settings.alert_im_channel = ch.currentData() if ch else ""
        self.owner.settings.alert_im_url = url.text().strip() if url else ""
        self.owner.settings.alert_im_token = tok.text().strip() if tok else ""
        self.owner.settings_store.save(self.owner.settings)
        self._apply_alert_config()

    def _save_g53_stale_days(self, value):
        self.owner.settings.g53_alerts_stale_days = int(value)
        self.owner.settings_store.save(self.owner.settings)

    def _save_g53_disk_min(self, value):
        self.owner.settings.g53_alerts_disk_min_gb = int(value)
        self.owner.settings_store.save(self.owner.settings)

    def _save_concurrency(self, value):
        self.owner.settings.concurrency = int(value)
        if hasattr(self.owner, "engine") and self.owner.engine is not None:
            self.owner.engine.set_concurrency(int(value))
            self.owner.thread_lbl.setText(f"并行线程 {self.owner.engine.concurrency}")
        self.owner.settings_store.save(self.owner.settings)

    def _save_fetch_timeout(self, value):
        self.owner.settings.fetch_timeout = int(value)
        self.owner.settings_store.save(self.owner.settings)

    def _save_retries(self, value):
        self.owner.settings.retries = int(value)
        self.owner.settings_store.save(self.owner.settings)

    def _save_proxy(self):
        self.owner.settings.proxy = self.owner.edit_proxy.text().strip()
        self.owner.settings_store.save(self.owner.settings)

    def _detect_proxy_now(self):
        """G04-3 一键自动检测系统代理并填入（可保存）。"""
        try:
            from ..app.proxy import detect_system_proxy
            val = detect_system_proxy()
            if val:
                self.owner.edit_proxy.setText(val)
                self.owner.settings.proxy = val
                self.owner.settings_store.save(self.owner.settings)
                self.owner._emit_log(_mw._fmt_dt(), LogLevel.INFO, f"已自动检测到代理：{val}")
                self.owner.statusBar().showMessage(f"代理已填入：{val}")
            else:
                self.owner._emit_log(_mw._fmt_dt(), LogLevel.WARN, "未检测到系统代理。")
                self.owner.statusBar().showMessage("未检测到系统代理")
        except Exception as e:
            self.owner._emit_log(_mw._fmt_dt(), LogLevel.WARN, f"自动检测代理失败：{e}")

    def _save_rate_limit(self, value):
        """G04-4 保存下载限速（KiB/s，0=不限）。"""
        self.owner.settings.rate_limit_kbps = int(value or 0)
        self.owner.settings_store.save(self.owner.settings)

    def _save_token(self):
        self.owner.settings.token = self.owner.edit_token.text().strip()
        self.owner.settings_store.save(self.owner.settings)

    # --------------------------------------------------------- G38-1~7 网络设置保存

    def _save_host_tokens(self):
        """G38-1 保存按 host 凭据映射：多行 `host=token` → settings.host_tokens。

        - 逐行按首个 `=` 拆分；key 小写归一、value 去空白
        - value 为空的行 = 删除该 host 条目
        - 无 `=`/空行忽略（用户删除行即移除凭据）
        """
        self.owner.settings.host_tokens = self._parse_kv_text(self.owner.edit_host_tokens.text())
        self.owner.settings_store.save(self.owner.settings)

    def _save_mirror(self):
        """G38-2/G58-2 保存镜像：每行 `host=prefix1,prefix2,...`。

        一个 host 可写多个镜像（顺序即优先级）；同时兼容旧的单值写法。
        """
        self.owner.settings.mirror_prefix = self._parse_mirror_text(
            self.owner.edit_mirror.text())
        self.owner.settings_store.save(self.owner.settings)

    @staticmethod
    def _parse_mirror_text(text: str) -> dict:
        """解析镜像文本 → `{host: str | [str,...]}`（host 小写、去 www 与尾斜杠）。"""
        from ..app.mirrors import normalize_prefix_list

        out: dict = {}
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, _, value = line.partition("=")
            host = key.strip().lower().rstrip("/")
            if host.startswith("www."):
                host = host[4:]
            prefixes = normalize_prefix_list(value)
            if host and prefixes:
                out[host] = list(prefixes) if len(prefixes) > 1 else prefixes[0]
        return out

    def _save_mirror_presets(self, checked):
        """G58-2：是否使用内置国内镜像。"""
        self.owner.settings.mirror_use_presets = bool(checked)
        self.owner.settings_store.save(self.owner.settings)

    def _save_mirror_fallback(self, checked):
        """G58-2：镜像失败是否回退源站。"""
        self.owner.settings.mirror_fallback = bool(checked)
        self.owner.settings_store.save(self.owner.settings)

    def _probe_mirrors(self):
        """G58-2 测速并排序：worker 线程探测，结果经信号回 GUI 线程。"""
        owner = self.owner
        if getattr(owner, "_mirror_probe_busy", False):
            return
        from ..app.mirrors import mirror_prefixes_for
        prefixes = mirror_prefixes_for(
            "github.com", getattr(owner.settings, "mirror_prefix", None) or {},
            use_presets=True)
        if not prefixes:
            owner._emit_log(_mw._fmt_dt(), LogLevel.WARN, tr("没有可测速的镜像"))
            return
        sample = "https://github.com/octocat/Hello-World"
        owner._mirror_probe_busy = True
        owner._emit_log(_mw._fmt_dt(), LogLevel.INFO,
                        tr("开始镜像测速（git ls-remote，8s 超时）…"))
        sig = owner.mirror_probe_done

        def _run() -> None:
            try:
                from ..app.mirror_probe import rank_mirrors
                results = rank_mirrors(sample, prefixes)
            except Exception as exc:  # noqa: BLE001 —— 线程边界，必须回传
                results = [{"url": "", "ok": False, "ms": 0, "error": str(exc),
                            "is_source": False}]
            sig.emit(results, sample)

        threading.Thread(target=_run, daemon=True, name="gcm-mirror-probe").start()

    def _on_mirror_probe_done(self, results, sample: str) -> None:
        """测速结束（GUI 线程）：把可用镜像按延迟写回输入框并留日志。"""
        owner = self.owner
        owner._mirror_probe_busy = False
        usable = [r for r in results or [] if r.get("ok") and not r.get("is_source")
                  and str(r.get("url") or "").endswith("/" + sample)]
        usable.sort(key=lambda r: int(r.get("ms", 0) or 0))
        prefixes = []
        for r in usable:
            url = str(r.get("url") or "")
            prefixes.append(url[: -len("/" + sample)])
        summary = " · ".join(
            f"{str(r.get('url') or '')[: -len('/' + sample)] or '(源站)'} "
            f"{r.get('ms', 0)}ms {'✓' if r.get('ok') else '✗'}"
            for r in (results or []) if r.get("url"))
        if summary:
            owner._emit_log(_mw._fmt_dt(), LogLevel.INFO, tr("镜像测速：") + summary)
        if not prefixes:
            owner._emit_log(_mw._fmt_dt(), LogLevel.WARN,
                            tr("没有可用镜像；已保留原配置，克隆将回退源站"))
            return
        owner.edit_mirror.setText("github.com=" + ", ".join(prefixes))
        self._save_mirror()
        owner._emit_log(_mw._fmt_dt(), LogLevel.INFO,
                        tr("已按测速结果写入镜像（优先使用最快者）"))

    def _save_custom_hosts(self):
        """G38-7 保存常用主机白名单：逗号/换行分隔 → tuple（小写归一，去空）。"""
        text = self.owner.edit_custom_hosts.text() or ""
        parts = [p.strip().lower() for p in text.replace("\n", ",").split(",")]
        self.owner.settings.custom_hosts = tuple(p for p in parts if p)
        self.owner.settings_store.save(self.owner.settings)

    def _save_ssh_key(self):
        """G67-16：保存全局 SSH 私钥路径（G48-3 接线）。

        保存后**立即写回运行中的引擎**，否则要重启才生效（引擎在 _launch 时按
        settings 重建，但用户期望改完即用）。
        """
        self.owner.settings.ssh_key_path = self.owner.edit_ssh_key.text().strip()
        self.owner.settings_store.save(self.owner.settings)
        try:
            self.owner._apply_ssh_key_to_engine()
        except Exception:  # noqa: BLE001 - 引擎可能尚未创建
            pass

    def _pick_ssh_key(self):
        """G67-16：浏览选择私钥文件（失败/取消都不改动现值）。"""
        path, _ = QFileDialog.getOpenFileName(
            self.owner, tr("选择 SSH 私钥"), self.owner.edit_ssh_key.text() or "",
            tr("私钥文件 (*id_* *.pem *.ppk);;所有文件 (*)"))
        if not path:
            return
        self.owner.edit_ssh_key.setText(path)
        self._save_ssh_key()

    def _save_auth_header_type(self, index):
        """G58-5 保存全局认证头类型（host_auth_types["*"]）。"""
        mapping = {0: "", 1: "bearer", 2: "private_token", 3: "basic"}
        hat = dict(getattr(self.owner.settings, "host_auth_types", None) or {})
        val = mapping.get(int(index), "")
        if val:
            hat["*"] = val
        else:
            hat.pop("*", None)
        self.owner.settings.host_auth_types = hat
        self.owner.settings_store.save(self.owner.settings)

    def _save_auto_clear(self, checked):
        self.owner.settings.auto_clear = bool(checked)
        self.owner.settings_store.save(self.owner.settings)

    def _save_fetch_unshallow(self, checked):
        self.owner.settings.fetch_unshallow = bool(checked)
        self.owner.settings_store.save(self.owner.settings)

    def _save_submodule(self, checked):
        self.owner.settings.submodule = bool(checked)
        self.owner.settings_store.save(self.owner.settings)

    def _save_clipboard_watch(self, checked):
        """G09-1 保存剪贴板监听开关并启停 watcher。"""
        self.owner.settings.clipboard_watch = bool(checked)
        self.owner.settings_store.save(self.owner.settings)
        try:
            self.owner.clipboard_watcher._enabled_flag = bool(checked)
            if checked:
                self.owner.clipboard_watcher.start()
                self.owner._emit_log(_mw._fmt_dt(), LogLevel.INFO, "剪贴板监听已开启。")
            else:
                self.owner.clipboard_watcher.stop()
                self.owner._emit_log(_mw._fmt_dt(), LogLevel.INFO, "剪贴板监听已关闭。")
        except Exception:
            pass

    def _save_finish_sound(self, checked):
        """G35-2 保存任务完成提示音开关。"""
        self.owner.settings.finish_sound = bool(checked)
        self.owner.settings_store.save(self.owner.settings)

    def _save_animations(self, checked):
        """G36-6 保存任务完成动效开关。"""
        self.owner.settings.animations = bool(checked)
        self.owner.settings_store.save(self.owner.settings)

    def _save_auto_update(self, value):
        """G37-4 保存自动更新间隔并按新间隔重启定时器。"""
        self.owner.settings.auto_update_minutes = int(value or 0)
        self.owner.settings_store.save(self.owner.settings)
        self.owner._restart_auto_update_timer()

    # ------------------------------------------------------------ G46-5/7/8/10/11
    def _save_accent_preset(self, index):
        """G46-7：保存强调色预设并即时应用（QSS 重生成）。"""
        self.owner.settings.accent_preset = self.owner.accent_combo.itemData(index) or "blue"
        self.owner.settings_store.save(self.owner.settings)
        try:
            self.owner.setStyleSheet(self.owner._themed_qss())
            self.owner._apply_theme_to_children()
        except Exception:
            pass

    def _save_reduced_motion(self, checked):
        """G46-8：减少动态效果开关。"""
        self.owner.settings.prefers_reduced_motion = bool(checked)
        self.owner.settings_store.save(self.owner.settings)
        try:
            self.owner.setStyleSheet(self.owner._themed_qss())
        except Exception:
            pass

    def _reset_font_scale(self):
        """G46-5：字号回 1.0 并即时应用。"""
        self.owner.settings.font_scale = 1.0
        self.owner.settings_store.save(self.owner.settings)
        try:
            self.owner.setStyleSheet(self.owner._themed_qss())
        except Exception:
            pass

    def _reset_defaults(self):
        """G46-10：恢复默认设置（token/geometry 保留），同步可见控件。"""
        from ..db.settings import Settings as _Settings
        old_token = getattr(self.owner.settings, "token", "")
        old_geometry = getattr(self.owner.settings, "geometry", "")
        fresh = _Settings()
        fresh.token = old_token
        fresh.geometry = old_geometry
        self.owner.settings = fresh
        self.owner.settings_store.save(fresh)
        try:
            self.owner.theme_combo.setCurrentIndex(
                self.owner.theme_combo.findData(fresh.theme))
            self.owner.ck_animations.setChecked(bool(fresh.animations))
            self.owner.ck_reduced_motion.setChecked(bool(fresh.prefers_reduced_motion))
            self.owner.accent_combo.setCurrentIndex(
                self.owner.accent_combo.findData(fresh.accent_preset) or 0)
            self.owner.setStyleSheet(self.owner._themed_qss())
        except Exception:
            pass
        try:
            self.owner._emit_log(_mw._fmt_dt(), LogLevel.INFO,
                                 "已恢复默认设置（token/窗口位置保留）")
        except Exception:
            pass

    def _open_help(self):
        """G46-11：打开「使用说明」对话框。"""
        try:
            from .help_dialog import HelpDialog
            HelpDialog(self.owner).exec()
            # G54-4：看过帮助即视为引导清单该项完成
            if hasattr(self.owner, "_mark_checklist"):
                self.owner._mark_checklist("help")
        except Exception:
            pass

    def _open_data_dir(self):
        """打开数据目录（QDesktopServices.openUrl）。"""
        from PyQt6.QtCore import QUrl
        from PyQt6.QtGui import QDesktopServices
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.owner.data_dir)))
        except Exception:
            pass

    def _save_lfs(self, checked):
        """G48-2：Git LFS 开关保存。"""
        self.owner.settings.lfs_enabled = bool(checked)
        self.owner.settings_store.save(self.owner.settings)

    def _toggle_autostart_task(self, checked):
        """G49-6：勾选开机自启 → 写入/删除 Windows 任务计划（联动 start_minimized）。"""
        try:
            from ..app.autostart import set_autostart
            ok, msg = set_autostart(
                bool(checked),
                minimized=bool(getattr(self.owner.settings, "start_minimized", False)))
            self.owner.statusBar().showMessage(msg, 6000)
        except Exception:
            pass

    def _save_start_minimized(self, checked):
        """G49-6：保存「启动时最小化到托盘」；自启已开则刷新任务命令（追加 --minimized）。"""
        self.owner.settings.start_minimized = bool(checked)
        self.owner.settings_store.save(self.owner.settings)
        try:
            if getattr(self.owner, "ck_autostart", None) is not None \
                    and self.owner.ck_autostart.isChecked():
                self._toggle_autostart_task(True)
        except Exception:
            pass

    def _save_language(self, index):
        """G49-1：保存语言（重启后生效）。"""
        key = self.owner.lang_combo.itemData(index) if index >= 0 else "zh"
        key = key or "zh"
        self.owner.settings.language = key
        self.owner.settings_store.save(self.owner.settings)
        try:
            self.owner.statusBar().showMessage(
                "语言已保存，重启后生效（Language saved, restart to apply）", 6000)
        except Exception:
            pass

    def _save_notify_enable(self, checked):
        """G49-7：Webhook 显式开关。"""
        self.owner.settings.notify_webhook_enabled = bool(checked)
        self.owner.settings_store.save(self.owner.settings)

    def _save_notify_webhook(self):
        """G49-7：Webhook URL/Token 保存（URL 留空即关闭发送）。"""
        self.owner.settings.notify_webhook_url = self.owner.edit_webhook_url.text().strip()
        self.owner.settings.notify_webhook_token = self.owner.edit_webhook_token.text().strip()
        self.owner.settings_store.save(self.owner.settings)

    def _run_diag(self):
        """G48-8 / G54-3：后台运行「环境体检 + 网络诊断」，结果回 GUI 线程展示。

        G67-2：网络 IO 在后台线程，但弹窗**必须回 GUI 线程**（emit 信号）。
        G69-18 修复：此前只跑 `run_diagnostics`（网络），而
        `diag.run_env_diagnostics`（环境体检：数据目录/磁盘/git/Python-Qt）与
        `diag.copy_paste_report`（redact 后的可外发报告）**只有测试在引用**
        —— 也就是「一键体检」与「复制诊断文本」两个功能实际不存在。
        现一并接上：体检结果进对话框，可外发文本由 `copy_paste_report` 生成。
        """
        import threading

        def _run():
            try:
                from gcm import __version__
                from gcm.app.diag import (
                    copy_paste_report,
                    grade,
                    run_diagnostics,
                    run_env_diagnostics,
                )
                reports = run_env_diagnostics(self.owner.data_dir)
                reports += run_diagnostics(
                    proxy=str(getattr(self.owner.settings, "proxy", "") or ""),
                    extra_hosts=tuple(getattr(self.owner.settings, "custom_hosts", ()) or ()))
                lines = [f"[{r['status']}] {r['name']}: {r['detail']}" for r in reports]
                title = "体检与网络诊断：" + grade(reports)
                copy_text = copy_paste_report(reports, app_version=str(__version__))
            except Exception as e:  # noqa: BLE001
                lines, title, copy_text = [f"诊断失败：{e}"], "诊断：失败", ""
            # 跨线程：只允许 emit
            try:
                self.owner.diag_finished.emit(title, "\n".join(lines), copy_text)
            except Exception:
                pass
        threading.Thread(target=_run, daemon=True).start()

    def _save_theme(self, index):
        """G05-1 保存主题选择并即时应用到主窗口。"""
        key = self.owner.theme_combo.itemData(index) if index >= 0 else "deep"
        key = key or "deep"
        self.owner.settings.theme = key
        self.owner.settings_store.save(self.owner.settings)
        try:
            from ..ui import theme as _th
            _th.apply_theme(key)
            self.owner.setStyleSheet(_th.qss_for_scale(getattr(self.owner.settings, "font_scale", 1.0)))
            self.owner._apply_theme_to_children()
        except Exception:
            pass

    # --------------------------------------------------------- G10-1 字号缩放
