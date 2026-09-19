"""设置页面板（G33-8 拆分）：从 MainWindow._build_settings_tab 提取。

职责：构建「启动与后台运行 / 并行与网络 / 外观 / 关于与更新 / 黑匣子日志」全部控件，
控件名与原 MainWindow 属性保持一致（self.spin_concurrency 等），由 MainWindow 组合挂载。
行为零变化：信号连接回调由 MainWindow 提供（_save_* / 对话框入口）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from . import main_window as _mw  # noqa: F401 - 迁移回调引用 main_window 模块级符号
from .theme import LogLevel

if TYPE_CHECKING:  # 仅类型标注（避免循环导入）
    from ..db.settings import Settings
    from .main_window import MainWindow


def _host_tokens_text(settings: Settings) -> str:
    """host_tokens（dict）→ 设置页多行文本 "host=token"。"""
    ht = getattr(settings, "host_tokens", None) or {}
    return "\n".join(f"{k}={v}" for k, v in ht.items() if k)


def _mirror_text(settings: Settings) -> str:
    """mirror_prefix（dict）→ 设置页多行文本 "host=prefix"。"""
    mp = getattr(settings, "mirror_prefix", None) or {}
    return "\n".join(f"{k}={v}" for k, v in mp.items() if k)


def _custom_hosts_text(settings: Settings) -> str:
    """custom_hosts（tuple）→ 逗号分隔文本。"""
    ch = getattr(settings, "custom_hosts", None) or ()
    return ", ".join(str(h) for h in ch)


def build_settings_ui(owner: MainWindow) -> QScrollArea:
    """在 owner 上构建设置页全部控件并返回滚动区（挂到 owner.settings_scroll）。

    所有控件以 owner.<name> 暴露（与历史 MainWindow 属性一致），信号连接回 owner 回调。
    """
    settings: Settings = owner.settings

    settings_scroll = QScrollArea()
    settings_scroll.setWidgetResizable(True)
    settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
    settings_scroll.setMinimumHeight(180)
    scroll_widget = QWidget()
    settings_scroll.setWidget(scroll_widget)
    sv = QVBoxLayout(scroll_widget)
    sv.setContentsMargins(12, 12, 12, 12)
    sv.setSpacing(10)

    # ---- G36-7 设置搜索：顶部搜索框，输入关键字隐藏不匹配分组
    search_row = QHBoxLayout()
    search_row.addWidget(QLabel("🔍 设置搜索"))
    owner.settings_search = QLineEdit()
    owner.settings_search.setPlaceholderText("搜索设置项（分组标题 / 行内标签）…")
    owner.settings_search.setClearButtonEnabled(True)
    search_row.addWidget(owner.settings_search, 1)
    sv.addLayout(search_row)

    # 分组收集 + 过滤闭包：空关键字全显；否则标题或行内 QLabel 文本匹配才可见
    groups: list[QGroupBox] = []
    owner.settings_groups = groups  # 供测试/外部断言分组可见性

    def _apply_settings_filter(keyword: str) -> None:
        """按关键字过滤设置分组：标题含关键字 或 任一行标签含关键字。"""
        kw = keyword.strip().lower()
        for group in groups:
            if not kw:
                group.setVisible(True)
                continue
            title = group.title().lower()
            labels = [lbl.text().lower() for lbl in group.findChildren(QLabel)]
            matched = kw in title or any(kw in t for t in labels if t)
            group.setVisible(matched)

    owner.settings_search.textChanged.connect(_apply_settings_filter)

    # ---- 启动与后台运行
    g1 = QGroupBox("启动与后台运行（开机自启 / 托盘）")
    g1.setToolTip("开机自启、下载完成后自动清空输入框 等启动行为")
    l1 = QHBoxLayout(g1)
    owner.ck_autostart = QCheckBox("开机自启（写入任务计划：登录时启动一次）")
    l1.addWidget(owner.ck_autostart)
    owner.ck_auto_clear = QCheckBox("下载完成后自动清空输入框")
    owner.ck_auto_clear.setChecked(bool(settings.auto_clear))
    owner.ck_auto_clear.stateChanged.connect(owner._save_auto_clear)
    l1.addWidget(owner.ck_auto_clear)
    # G09-1 剪贴板监听：检测到 git 地址提示加入队列
    owner.ck_clipboard = QCheckBox("监听剪贴板（检测到仓库地址自动提示）")
    owner.ck_clipboard.setChecked(bool(getattr(settings, "clipboard_watch", False)))
    owner.ck_clipboard.stateChanged.connect(owner._save_clipboard_watch)
    l1.addWidget(owner.ck_clipboard)
    # G35-2 完成提示音：全部任务结束响一声（挂机用户感知「跑完了」）
    owner.ck_finish_sound = QCheckBox("任务完成提示音")
    owner.ck_finish_sound.setChecked(bool(getattr(settings, "finish_sound", True)))
    owner.ck_finish_sound.setToolTip("全部任务完成时响一声提示音（QApplication.beep）")
    owner.ck_finish_sound.stateChanged.connect(owner._save_finish_sound)
    l1.addWidget(owner.ck_finish_sound)
    # G36-6 完成动效：成功绿/失败红背景淡出（低配/离屏自动关）
    owner.ck_animations = QCheckBox("任务完成动效")
    owner.ck_animations.setChecked(bool(getattr(settings, "animations", True)))
    owner.ck_animations.setToolTip("任务结束时行背景色 1.2s 淡出（成功绿/失败红）")
    owner.ck_animations.stateChanged.connect(owner._save_animations)
    l1.addWidget(owner.ck_animations)
    l1.addStretch()
    sv.addWidget(g1)
    groups.append(g1)

    # ---- 并行与网络设置（持久化到 settings.json）
    g3 = QGroupBox("并行与网络（并发 / 超时 / 重试 / 代理 / Token）")
    g3.setToolTip("并发数、超时、重试、代理与私有仓库认证 等网络相关设置")
    f3 = QFormLayout(g3)
    f3.setContentsMargins(10, 10, 10, 10)
    f3.setHorizontalSpacing(16)
    f3.setVerticalSpacing(10)
    f3.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    f3.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

    owner.spin_concurrency = QSpinBox()
    owner.spin_concurrency.setRange(1, 32)
    owner.spin_concurrency.setValue(int(settings.concurrency))
    # MAX_CONCURRENCY 为 main_window 模块常量（惰性导入避免循环引用）
    from .main_window import MAX_CONCURRENCY as _MAX_C
    owner.spin_concurrency.setToolTip(
        f"同时并行下载/更新的仓库数（1–{_MAX_C}）")
    owner.spin_concurrency.valueChanged.connect(owner._save_concurrency)
    f3.addRow("并发数（1–32）：", owner.spin_concurrency)

    owner.spin_fetch_timeout = QSpinBox()
    owner.spin_fetch_timeout.setRange(10, 3600)
    owner.spin_fetch_timeout.setValue(int(settings.fetch_timeout))
    owner.spin_fetch_timeout.setToolTip("访问仓库远程信息（fetch/克隆）的超时时间，单位秒")
    owner.spin_fetch_timeout.valueChanged.connect(owner._save_fetch_timeout)
    f3.addRow("fetch 超时（秒）：", owner.spin_fetch_timeout)

    owner.spin_retries = QSpinBox()
    owner.spin_retries.setRange(0, 5)
    owner.spin_retries.setValue(int(settings.retries))
    owner.spin_retries.setToolTip("网络故障时自动重试次数（0 = 只尝试一次）")
    owner.spin_retries.valueChanged.connect(owner._save_retries)
    f3.addRow("自动重试（次）：", owner.spin_retries)

    owner.edit_proxy = QLineEdit(settings.proxy)
    owner.edit_proxy.setPlaceholderText("http://127.0.0.1:7890（留空不代理）")
    owner.edit_proxy.setToolTip("网络代理地址，形如 http://127.0.0.1:7890；留空则不使用代理")
    owner.edit_proxy.editingFinished.connect(owner._save_proxy)
    f3.addRow("HTTP 代理：", owner.edit_proxy)
    # G04-3 自动检测系统代理（环境变量 / Windows 注册表）
    owner.btn_detect_proxy = QPushButton("自动检测")
    owner.btn_detect_proxy.setToolTip("读取系统代理（环境变量 / Windows 注册表）填入")
    owner.btn_detect_proxy.clicked.connect(owner._detect_proxy_now)
    f3.addRow("", owner.btn_detect_proxy)

    # G04-4 下载限速（KiB/s）
    owner.spin_rate = QSpinBox()
    owner.spin_rate.setRange(0, 100000)
    owner.spin_rate.setValue(int(getattr(settings, "rate_limit_kbps", 0) or 0))
    owner.spin_rate.setSuffix(" KiB/s")
    owner.spin_rate.setSpecialValueText("不限速")
    owner.spin_rate.setToolTip("低于该速率持续 30s 视为卡死中止（0 = 不限速）")
    owner.spin_rate.valueChanged.connect(owner._save_rate_limit)
    f3.addRow("限速：", owner.spin_rate)

    owner.ck_unshallow = QCheckBox("浅克隆仓库更新时拉全量历史")
    owner.ck_unshallow.setChecked(bool(settings.fetch_unshallow))
    owner.ck_unshallow.setToolTip("浅克隆仓库增量 fetch 时拉取全量历史，避免后续增量因深度不足失败")
    owner.ck_unshallow.stateChanged.connect(owner._save_fetch_unshallow)
    f3.addRow("浅克隆更新：", owner.ck_unshallow)

    owner.ck_submodule = QCheckBox("克隆时拉取子模块（--recurse-submodules）")
    owner.ck_submodule.setChecked(bool(getattr(settings, "submodule", False)))
    owner.ck_submodule.setToolTip("含子模块的仓库克隆后工作区完整；开启会增加克隆耗时")
    owner.ck_submodule.stateChanged.connect(owner._save_submodule)
    f3.addRow("子模块：", owner.ck_submodule)

    # G37-4 自动更新间隔（分钟，0=关）
    owner.spin_auto_update = QSpinBox()
    owner.spin_auto_update.setRange(0, 10080)  # 0 或 1 分钟 ~ 7 天
    owner.spin_auto_update.setValue(int(getattr(settings, "auto_update_minutes", 0) or 0))
    owner.spin_auto_update.setSuffix(" 分钟")
    owner.spin_auto_update.setSpecialValueText("关闭")
    owner.spin_auto_update.setToolTip("定时自动一键更新全部（任务运行中自动跳过）；0=关闭")
    owner.spin_auto_update.valueChanged.connect(owner._save_auto_update)
    f3.addRow("自动更新：", owner.spin_auto_update)

    owner.edit_token = QLineEdit(settings.token)
    owner.edit_token.setPlaceholderText("私有仓库认证令牌（可选，留空不传递）")
    # G44-1：token 输入框默认掩码（Password），防旁人/截图泄露
    owner.edit_token.setEchoMode(QLineEdit.EchoMode.Password)
    owner.edit_token.setToolTip(
        "GitHub 个人访问令牌（Fine-grained/PAT），访问私有仓库时使用；留空不传递")
    owner.edit_token.editingFinished.connect(owner._save_token)
    f3.addRow("GitHub Token：", owner.edit_token)

    # G38-1 按 host 凭据（每行 "host=token"，留空不发送）
    owner.edit_host_tokens = QLineEdit(_host_tokens_text(settings))
    owner.edit_host_tokens.setPlaceholderText("gitlab.com=glpat-xxx（每行一个 host=token）")
    owner.edit_host_tokens.setToolTip(
        "按平台主机配置私有仓库凭据：每行 `host=token`（gitlab.com 用 PRIVATE-TOKEN 头）")
    # G44-1：host=token 多行文本域同样默认掩码（Password），避免凭据明文可见
    owner.edit_host_tokens.setEchoMode(QLineEdit.EchoMode.Password)
    owner.edit_host_tokens.editingFinished.connect(owner._save_host_tokens)
    f3.addRow("按平台凭据：", owner.edit_host_tokens)

    # G44-1：显示明文开关——勾选时 host_tokens 明文显示、取消恢复掩码（新控件 ck_show_host_tokens）
    owner.ck_show_host_tokens = QCheckBox("显示明文")
    owner.ck_show_host_tokens.setToolTip("勾选后按平台凭据明文显示；默认掩码，防止旁人看到 token")
    owner.ck_show_host_tokens.setChecked(False)

    def _toggle_host_tokens_echo(checked: bool) -> None:
        """G44-1 按「显示明文」勾选状态切换 host_tokens 输入框掩码。"""
        owner.edit_host_tokens.setEchoMode(
            QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)

    owner.ck_show_host_tokens.toggled.connect(_toggle_host_tokens_echo)
    f3.addRow("", owner.ck_show_host_tokens)

    # G38-2 镜像前缀（每行 "host=prefix"，仅 HTTPS 生效）
    owner.edit_mirror = QLineEdit(_mirror_text(settings))
    owner.edit_mirror.setPlaceholderText("github.com=https://ghproxy.com（每行一个）")
    owner.edit_mirror.setToolTip("按平台配置镜像前缀（仅 https 克隆生效；SSH 不拼）")
    owner.edit_mirror.editingFinished.connect(owner._save_mirror)
    f3.addRow("镜像前缀：", owner.edit_mirror)

    # G38-7 常用主机（自建 GitLab 等，短格式可直接解析）
    owner.edit_custom_hosts = QLineEdit(_custom_hosts_text(settings))
    owner.edit_custom_hosts.setPlaceholderText("mygit.example.com（逗号或换行分隔）")
    owner.edit_custom_hosts.setToolTip(
        "登记自建主机后，短格式 `mygit.example.com/a/b` 可直接解析为 HTTPS 克隆地址")
    owner.edit_custom_hosts.editingFinished.connect(owner._save_custom_hosts)
    f3.addRow("常用主机：", owner.edit_custom_hosts)

    # G38-3/4/6 网络兼容开关
    owner.ck_precheck = QCheckBox("克隆前做远端可达性预检（10s）")
    owner.ck_precheck.setChecked(bool(getattr(settings, "precheck_remote", False)))
    owner.ck_precheck.setToolTip("断网时 10s 内明确提示，不白等超时")
    owner.ck_precheck.stateChanged.connect(owner._save_precheck)
    f3.addRow("远端预检：", owner.ck_precheck)

    owner.ck_single_branch = QCheckBox("浅克隆只拉目标分支（--single-branch）")
    owner.ck_single_branch.setChecked(bool(getattr(settings, "single_branch", False)))
    owner.ck_single_branch.setToolTip("浅克隆时减少 refs 传输量；增量更新兼容")
    owner.ck_single_branch.stateChanged.connect(owner._save_single_branch)
    f3.addRow("单分支：", owner.ck_single_branch)

    owner.ck_force_ipv4 = QCheckBox("强制 HTTP/1.1（IPv6 兼容）")
    owner.ck_force_ipv4.setChecked(bool(getattr(settings, "force_ipv4", False)))
    owner.ck_force_ipv4.setToolTip("部分网络环境下 HTTP/2 兼容问题可用此项规避")
    owner.ck_force_ipv4.stateChanged.connect(owner._save_force_ipv4)
    f3.addRow("HTTP 版本：", owner.ck_force_ipv4)
    sv.addWidget(g3)
    groups.append(g3)

    # ---- 外观（G05-1 多主题）
    g5 = QGroupBox("外观（主题）")
    f5 = QFormLayout(g5)
    f5.setContentsMargins(10, 10, 10, 10)
    f5.setHorizontalSpacing(16)
    f5.setVerticalSpacing(10)
    f5.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    owner.theme_combo = QComboBox()
    from ..ui.theme import THEMES as _THEMES
    for key, label in _THEMES.items():
        owner.theme_combo.addItem(label, key)
    # G36-9 跟随系统深浅色（auto = 启动/系统变化时按注册表自动切 light/deep）
    owner.theme_combo.addItem("跟随系统（自动）", "auto")
    cur = getattr(settings, "theme", "deep")
    idx = owner.theme_combo.findData(cur)
    if idx >= 0:
        owner.theme_combo.setCurrentIndex(idx)
    owner.theme_combo.currentIndexChanged.connect(owner._save_theme)
    f5.addRow("界面主题：", owner.theme_combo)
    # G46-7 强调色预设
    owner.accent_combo = QComboBox()
    for _k, _lbl in (("blue", "蓝色（默认）"), ("violet", "紫色"), ("teal", "青色")):
        owner.accent_combo.addItem(_lbl, _k)
    _acc = getattr(settings, "accent_preset", "blue")
    _ai = owner.accent_combo.findData(_acc)
    if _ai >= 0:
        owner.accent_combo.setCurrentIndex(_ai)
    owner.accent_combo.currentIndexChanged.connect(owner.settings_panel._save_accent_preset)
    f5.addRow("强调色：", owner.accent_combo)
    # G46-8 减少动态效果
    owner.ck_reduced_motion = QCheckBox("减少动态效果（关闭完成动效/过渡）")
    owner.ck_reduced_motion.setChecked(bool(getattr(settings, "prefers_reduced_motion", False)))
    owner.ck_reduced_motion.stateChanged.connect(owner.settings_panel._save_reduced_motion)
    f5.addRow("动效：", owner.ck_reduced_motion)
    # G46-5 恢复默认字号
    owner.btn_font_reset = QPushButton("恢复默认字号")
    owner.btn_font_reset.clicked.connect(owner.settings_panel._reset_font_scale)
    f5.addRow("字号：", owner.btn_font_reset)
    # G46-10 恢复默认设置（token/窗口位置保留）
    owner.btn_reset_defaults = QPushButton("恢复默认设置（token 保留）")
    owner.btn_reset_defaults.setToolTip("除 token 与窗口位置外全部回默认值")
    owner.btn_reset_defaults.clicked.connect(owner.settings_panel._reset_defaults)
    f5.addRow("重置：", owner.btn_reset_defaults)
    sv.addWidget(g5)
    groups.append(g5)

    # ---- 关于与更新
    g4 = QGroupBox("关于与更新")
    g4.setToolTip("版本信息与自检工具")
    f4 = QFormLayout(g4)
    f4.setContentsMargins(10, 10, 10, 10)
    f4.setHorizontalSpacing(16)
    f4.setVerticalSpacing(10)
    f4.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    f4.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    try:
        from .. import __version__ as _ver
    except Exception:
        _ver = "unknown"
    owner.lbl_version = QLabel(_ver)
    owner.lbl_version.setObjectName("muted")
    f4.addRow("当前版本：", owner.lbl_version)
    h4 = QHBoxLayout()
    owner.btn_check_update = QPushButton("检查更新")
    owner.btn_check_update.clicked.connect(owner.check_update_now)
    h4.addWidget(owner.btn_check_update)
    owner.btn_selftest = QPushButton("自检环境")
    owner.btn_selftest.setToolTip("检测 git / PyQt6 / 数据目录 / 当前并发 是否正常可用")
    owner.btn_selftest.clicked.connect(owner._run_selftest)
    h4.addWidget(owner.btn_selftest)
    # G07-1 统计中心入口
    owner.btn_statistics = QPushButton("📊 统计中心")
    owner.btn_statistics.setToolTip("查看仓库总数 / 同步次数 / 成功率 / 平台分布")
    owner.btn_statistics.clicked.connect(owner.show_statistics)
    h4.addWidget(owner.btn_statistics)
    # G07-3 报表导出入口
    owner.btn_export = QPushButton("📄 导出报表")
    owner.btn_export.setToolTip("导出 CSV（Excel 友好）或 Markdown 报表")
    owner.btn_export.clicked.connect(owner.export_report)
    h4.addWidget(owner.btn_export)
    h4.addStretch()
    f4.addRow("环境自检：", h4)
    # G46-11 使用说明 + 打开数据目录
    h_help = QHBoxLayout()
    owner.btn_help = QPushButton("📖 使用说明")
    owner.btn_help.setToolTip("语法速查：@tag / host=token / 镜像 / 快捷键 / 拖拽 / 清单 / Star 导入")
    owner.btn_help.clicked.connect(owner.settings_panel._open_help)
    h_help.addWidget(owner.btn_help)
    owner.btn_open_data = QPushButton("打开数据目录")
    owner.btn_open_data.setToolTip("日志 / 备份 / 任务清单 所在目录")
    owner.btn_open_data.clicked.connect(owner.settings_panel._open_data_dir)
    h_help.addWidget(owner.btn_open_data)
    h_help.addStretch()
    f4.addRow("帮助：", h_help)
    sv.addWidget(g4)
    groups.append(g4)

    sv.addStretch()
    return settings_scroll


def build_log_box(owner: MainWindow) -> QGroupBox:
    """构建黑匣子日志区（固定在下，控件挂 owner.log_count/log_view/btn_save_log/btn_clear_log）。"""
    g2 = QGroupBox("黑匣子日志（实时）")
    g2.setToolTip("应用运行日志实时输出；可导出为文本文件排查问题")
    l2 = QVBoxLayout(g2)
    tb = QHBoxLayout()
    owner.log_count = QLabel("0 条")
    owner.log_count.setObjectName("muted")
    tb.addWidget(owner.log_count)
    tb.addStretch()
    owner.btn_save_log = QPushButton("导出日志…")
    owner.btn_save_log.setToolTip("把当前日志内容导出为文本文件")
    owner.btn_save_log.clicked.connect(owner.save_log)
    owner.btn_clear_log = QPushButton("清空日志")
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
    return g2



class SettingsPanel(QWidget):
    """G45-6 设置回调面板：_save_* 等设置持久化回调迁入（MainWindow 保留同名转发）。

    设置控件仍构建在 MainWindow 上（build_settings_ui(owner)）；本面板仅持有回调，
    经 owner 访问窗口属性（settings / settings_store / engine / edit_proxy 等）。
    对外方法名与 MainWindow 保持一致。
    """

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner

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
        """G38-2 保存镜像前缀映射：多行 `host=prefix` → settings.mirror_prefix。"""
        self.owner.settings.mirror_prefix = self._parse_kv_text(self.owner.edit_mirror.text())
        self.owner.settings_store.save(self.owner.settings)

    def _save_custom_hosts(self):
        """G38-7 保存常用主机白名单：逗号/换行分隔 → tuple（小写归一，去空）。"""
        text = self.owner.edit_custom_hosts.text() or ""
        parts = [p.strip().lower() for p in text.replace("\n", ",").split(",")]
        self.owner.settings.custom_hosts = tuple(p for p in parts if p)
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
