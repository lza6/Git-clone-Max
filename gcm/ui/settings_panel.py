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
    owner.edit_host_tokens.editingFinished.connect(owner._save_host_tokens)
    f3.addRow("按平台凭据：", owner.edit_host_tokens)

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
