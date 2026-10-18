"""设置页各分组控件的构建（G68-1：从 `settings_panel.build_settings_ui` 拆出）。

拆分理由：`build_settings_ui` 单函数 **432 行**（项目上限 50 行，8.6 倍），且把「壳 +
6 个分组 + 数据洞察组」全塞在一处 —— 改任何一项都要在 400 行里定位。拆成
「壳 + 7 个分组构建器」后每个函数 15~180 行，各自独立可读、可单测。

控件名仍全部挂在 `owner` 上（`owner.spin_concurrency` 等），**行为零变化**：
`settings_panel.build_settings_ui` 原样再导出，`main_window` 的导入路径不变。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr  # G49-1
from .main_window import MainWindow

if TYPE_CHECKING:  # 仅类型标注（避免循环导入）
    from ..db.settings import Settings

def _host_tokens_text(settings: Settings) -> str:
    """host_tokens（dict）→ 设置页多行文本 "host=token"。"""
    ht = getattr(settings, "host_tokens", None) or {}
    return "\n".join(f"{k}={v}" for k, v in ht.items() if k)


def _mirror_text(settings: Settings) -> str:
    """mirror_prefix（dict）→ 设置页文本 "host=prefix1, prefix2"（多备援用逗号）。"""
    mp = getattr(settings, "mirror_prefix", None) or {}
    lines = []
    for k, v in mp.items():
        if not k:
            continue
        if isinstance(v, (list, tuple)):
            val = ", ".join(str(x) for x in v if str(x).strip())
        else:
            val = str(v or "").strip()
        if val:
            lines.append(f"{k}={val}")
    return "\n".join(lines)


def _custom_hosts_text(settings: Settings) -> str:
    """custom_hosts（tuple）→ 逗号分隔文本。"""
    ch = getattr(settings, "custom_hosts", None) or ()
    return ", ".join(str(h) for h in ch)


def _build_settings_shell(owner: MainWindow):
    """构建设置页外壳：滚动区 + 搜索框 + groups 登记表，返回三者供分组挂载。"""
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
    search_row.addWidget(QLabel(tr("🔍 设置搜索")))
    owner.settings_search = QLineEdit()
    owner.settings_search.setPlaceholderText(tr("搜索设置项（分组标题 / 行内标签）…"))
    owner.settings_search.setClearButtonEnabled(True)
    search_row.addWidget(owner.settings_search, 1)
    sv.addLayout(search_row)

    # 分组收集 + 过滤闭包：空关键字全显；否则标题或行内 QLabel 文本匹配才可见
    groups: list[QGroupBox] = []
    owner.settings_groups = groups  # 供测试/外部断言分组可见性
    owner.settings_groups_extra = []  # G53-2 扩展组（不进 groups，避免破坏既有解构）

    # G69-18：过滤逻辑移到 MainWindow._apply_settings_filter（绑定方法不建引用环）。
    owner.settings_search.textChanged.connect(owner._apply_settings_filter)
    return (settings_scroll, sv, groups)

def _build_group_startup(owner: MainWindow, settings: Settings):
    """启动与后台运行（开机自启 / 托盘 / 自动清空 / 剪贴板 / 音效 / 动效）。"""
    g1 = QGroupBox(tr("启动与后台运行（开机自启 / 托盘）"))
    g1.setToolTip(tr("开机自启、下载完成后自动清空输入框 等启动行为"))
    l1 = QHBoxLayout(g1)
    owner.ck_autostart = QCheckBox(tr("开机自启（写入任务计划：登录时启动一次）"))
    # G49-6：初始回填任务计划真实状态；变更时写入任务计划（联动 start_minimized）
    try:
        from ..app.autostart import autostart_enabled
        owner.ck_autostart.setChecked(autostart_enabled())
    except Exception:
        pass
    owner.ck_autostart.stateChanged.connect(owner.settings_panel._toggle_autostart_task)
    l1.addWidget(owner.ck_autostart)
    owner.ck_start_min = QCheckBox(tr("启动时最小化到托盘"))
    owner.ck_start_min.setChecked(bool(getattr(settings, "start_minimized", False)))
    owner.ck_start_min.setToolTip(tr("启动后隐藏主窗口（仅托盘常驻；需托盘可用）"))
    owner.ck_start_min.stateChanged.connect(owner.settings_panel._save_start_minimized)
    l1.addWidget(owner.ck_start_min)
    owner.ck_auto_clear = QCheckBox(tr("下载完成后自动清空输入框"))
    owner.ck_auto_clear.setChecked(bool(settings.auto_clear))
    owner.ck_auto_clear.stateChanged.connect(owner._save_auto_clear)
    l1.addWidget(owner.ck_auto_clear)
    # G09-1 剪贴板监听：检测到 git 地址提示加入队列
    owner.ck_clipboard = QCheckBox(tr("监听剪贴板（检测到仓库地址自动提示）"))
    owner.ck_clipboard.setChecked(bool(getattr(settings, "clipboard_watch", False)))
    owner.ck_clipboard.stateChanged.connect(owner._save_clipboard_watch)
    l1.addWidget(owner.ck_clipboard)
    # G35-2 完成提示音：全部任务结束响一声（挂机用户感知「跑完了」）
    owner.ck_finish_sound = QCheckBox(tr("任务完成提示音"))
    owner.ck_finish_sound.setChecked(bool(getattr(settings, "finish_sound", True)))
    owner.ck_finish_sound.setToolTip(tr("全部任务完成时响一声提示音（QApplication.beep）"))
    owner.ck_finish_sound.stateChanged.connect(owner._save_finish_sound)
    l1.addWidget(owner.ck_finish_sound)
    # G36-6 完成动效：成功绿/失败红背景淡出（低配/离屏自动关）
    owner.ck_animations = QCheckBox(tr("任务完成动效"))
    owner.ck_animations.setChecked(bool(getattr(settings, "animations", True)))
    owner.ck_animations.setToolTip(tr("任务结束时行背景色 1.2s 淡出（成功绿/失败红）"))
    owner.ck_animations.stateChanged.connect(owner._save_animations)
    l1.addWidget(owner.ck_animations)
    l1.addStretch()
    return g1

def _build_group_network(owner: MainWindow, settings: Settings):
    """并行与网络（并发 / 超时 / 重试 / 代理 / 限速 / 凭据 / 镜像 / 自建主机）。"""
    g3 = QGroupBox(tr("并行与网络（并发 / 超时 / 重试 / 代理 / Token）"))
    g3.setToolTip(tr("并发数、超时、重试、代理与私有仓库认证 等网络相关设置"))
    f3 = QFormLayout(g3)
    f3.setContentsMargins(10, 10, 10, 10)
    f3.setHorizontalSpacing(16)
    f3.setVerticalSpacing(10)
    f3.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    f3.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

    _network_rows_basic(owner, settings, f3)
    _network_rows_credentials(owner, settings, f3)
    return g3


def _network_rows_basic(owner: MainWindow, settings: Settings, f3: QFormLayout) -> None:
    """网络组第一段：并发 / 超时 / 重试 / 代理 / 限速 / 深浅克隆 / 子模块 / LFS / 自动更新。"""
    owner.spin_concurrency = QSpinBox()
    owner.spin_concurrency.setRange(1, 32)
    owner.spin_concurrency.setValue(int(settings.concurrency))
    # MAX_CONCURRENCY 为 main_window 模块常量（惰性导入避免循环引用）
    from .main_window import MAX_CONCURRENCY as _MAX_C
    owner.spin_concurrency.setToolTip(
        f"同时并行下载/更新的仓库数（1–{_MAX_C}）")
    owner.spin_concurrency.valueChanged.connect(owner._save_concurrency)
    f3.addRow(tr("并发数（1–32）："), owner.spin_concurrency)

    owner.spin_fetch_timeout = QSpinBox()
    owner.spin_fetch_timeout.setRange(10, 3600)
    owner.spin_fetch_timeout.setValue(int(settings.fetch_timeout))
    owner.spin_fetch_timeout.setToolTip(tr("访问仓库远程信息（fetch/克隆）的超时时间，单位秒"))
    owner.spin_fetch_timeout.valueChanged.connect(owner._save_fetch_timeout)
    f3.addRow(tr("fetch 超时（秒）："), owner.spin_fetch_timeout)

    owner.spin_retries = QSpinBox()
    owner.spin_retries.setRange(0, 5)
    owner.spin_retries.setValue(int(settings.retries))
    owner.spin_retries.setToolTip(tr("网络故障时自动重试次数（0 = 只尝试一次）"))
    owner.spin_retries.valueChanged.connect(owner._save_retries)
    f3.addRow(tr("自动重试（次）："), owner.spin_retries)

    owner.edit_proxy = QLineEdit(settings.proxy)
    owner.edit_proxy.setPlaceholderText(tr("http://127.0.0.1:7890（留空不代理）"))
    owner.edit_proxy.setToolTip(tr("网络代理地址，形如 http://127.0.0.1:7890；留空则不使用代理"))
    owner.edit_proxy.editingFinished.connect(owner._save_proxy)
    f3.addRow(tr("HTTP 代理："), owner.edit_proxy)
    # G04-3 自动检测系统代理（环境变量 / Windows 注册表）
    owner.btn_detect_proxy = QPushButton(tr("自动检测"))
    owner.btn_detect_proxy.setToolTip(tr("读取系统代理（环境变量 / Windows 注册表）填入"))
    owner.btn_detect_proxy.clicked.connect(owner._detect_proxy_now)
    f3.addRow("", owner.btn_detect_proxy)

    # G04-4 下载限速（KiB/s）
    owner.spin_rate = QSpinBox()
    owner.spin_rate.setRange(0, 100000)
    owner.spin_rate.setValue(int(getattr(settings, "rate_limit_kbps", 0) or 0))
    owner.spin_rate.setSuffix(" KiB/s")
    owner.spin_rate.setSpecialValueText(tr("不限速"))
    owner.spin_rate.setToolTip(tr("低于该速率持续 30s 视为卡死中止（0 = 不限速）"))
    owner.spin_rate.valueChanged.connect(owner._save_rate_limit)
    f3.addRow(tr("限速："), owner.spin_rate)

    owner.ck_unshallow = QCheckBox(tr("浅克隆仓库更新时拉全量历史"))
    owner.ck_unshallow.setChecked(bool(settings.fetch_unshallow))
    owner.ck_unshallow.setToolTip(tr("浅克隆仓库增量 fetch 时拉取全量历史，避免后续增量因深度不足失败"))
    owner.ck_unshallow.stateChanged.connect(owner._save_fetch_unshallow)
    f3.addRow(tr("浅克隆更新："), owner.ck_unshallow)

    owner.ck_submodule = QCheckBox(tr("克隆时拉取子模块（--recurse-submodules）"))
    owner.ck_submodule.setChecked(bool(getattr(settings, "submodule", False)))
    owner.ck_submodule.setToolTip(tr("含子模块的仓库克隆后工作区完整；开启会增加克隆耗时"))
    owner.ck_submodule.stateChanged.connect(owner._save_submodule)
    f3.addRow(tr("子模块："), owner.ck_submodule)

    owner.ck_lfs = QCheckBox(tr("Git LFS（-c filter.lfs.required=false）"))
    owner.ck_lfs.setChecked(bool(getattr(settings, "lfs_enabled", False)))
    owner.ck_lfs.setToolTip(tr("G48-2：克隆/更新时追加 -c filter.lfs.required=false；LFS 文件需另行 lfs pull"))
    owner.ck_lfs.stateChanged.connect(owner.settings_panel._save_lfs)
    # G37-4 自动更新间隔（分钟，0=关）
    owner.spin_auto_update = QSpinBox()
    owner.spin_auto_update.setRange(0, 10080)  # 0 或 1 分钟 ~ 7 天
    owner.spin_auto_update.setValue(int(getattr(settings, "auto_update_minutes", 0) or 0))
    owner.spin_auto_update.setSuffix(" 分钟")
    owner.spin_auto_update.setSpecialValueText(tr("关闭"))
    owner.spin_auto_update.setToolTip(tr("定时自动一键更新全部（任务运行中自动跳过）；0=关闭"))
    owner.spin_auto_update.valueChanged.connect(owner._save_auto_update)
    f3.addRow(tr("自动更新："), owner.spin_auto_update)


def _network_rows_credentials(owner: MainWindow, settings: Settings, f3: QFormLayout) -> None:
    """网络组第二段：Token / 按平台凭据 / 认证头类型 / SSH 私钥 / 镜像 / 自建主机 / 预检 / 单分支 / IPv4。"""
    owner.edit_token = QLineEdit(settings.token)
    owner.edit_token.setPlaceholderText(tr("私有仓库认证令牌（可选，留空不传递）"))
    # G44-1：token 输入框默认掩码（Password），防旁人/截图泄露
    owner.edit_token.setEchoMode(QLineEdit.EchoMode.Password)
    owner.edit_token.setToolTip(
        tr("GitHub 个人访问令牌（Fine-grained/PAT），访问私有仓库时使用；留空不传递"))
    owner.edit_token.editingFinished.connect(owner._save_token)
    f3.addRow(tr("GitHub Token："), owner.edit_token)

    # G38-1 按 host 凭据（每行 "host=token"，留空不发送）
    owner.edit_host_tokens = QLineEdit(_host_tokens_text(settings))
    owner.edit_host_tokens.setPlaceholderText(tr("gitlab.com=glpat-xxx（每行一个 host=token）"))
    owner.edit_host_tokens.setToolTip(
        tr("按平台主机配置私有仓库凭据：每行 `host=token`（gitlab.com 用 PRIVATE-TOKEN 头）"))
    # G44-1：host=token 多行文本域同样默认掩码（Password），避免凭据明文可见
    owner.edit_host_tokens.setEchoMode(QLineEdit.EchoMode.Password)
    owner.edit_host_tokens.editingFinished.connect(owner._save_host_tokens)
    f3.addRow(tr("按平台凭据："), owner.edit_host_tokens)

    # G44-1：显示明文开关——勾选时 host_tokens 明文显示、取消恢复掩码（新控件 ck_show_host_tokens）
    owner.ck_show_host_tokens = QCheckBox(tr("显示明文"))
    owner.ck_show_host_tokens.setToolTip(tr("勾选后按平台凭据明文显示；默认掩码，防止旁人看到 token"))
    owner.ck_show_host_tokens.setChecked(False)

    # G69-18：同上，移到 MainWindow._toggle_host_tokens_echo
    owner.ck_show_host_tokens.toggled.connect(owner._toggle_host_tokens_echo)
    f3.addRow("", owner.ck_show_host_tokens)

    # G58-5 认证头类型（Bearer / PRIVATE-TOKEN / Basic）；空=自动（按平台默认）
    owner.cb_auth_header = QComboBox()
    owner.cb_auth_header.addItems([tr("自动（按平台默认）"), "Bearer", "PRIVATE-TOKEN", "Basic"])
    owner.cb_auth_header.setToolTip(
        tr("自建 GitLab/Gitea 等可显式选择认证头类型；空值按平台默认（github=Bearer / gitlab=PRIVATE-TOKEN）"))
    _hat = str((getattr(settings, "host_auth_types", None) or {}).get("*", "") or "").lower().strip()
    _idx = {"": 0, "bearer": 1, "private_token": 2, "basic": 3}.get(_hat, 0)
    owner.cb_auth_header.setCurrentIndex(_idx)
    owner.cb_auth_header.currentIndexChanged.connect(owner._save_auth_header_type)
    f3.addRow(tr("认证头类型："), owner.cb_auth_header)

    # G67-16 指定 SSH key（G48-3 接线）：原先 service 侧已支持 GIT_SSH_COMMAND，
    # 但 settings 里没有该字段、UI 也没有入口 → 整条链路永远为空、功能实际不可达。
    _ssh_row = QWidget()
    _ssh_lay = QHBoxLayout(_ssh_row)
    _ssh_lay.setContentsMargins(0, 0, 0, 0)
    owner.edit_ssh_key = QLineEdit(str(getattr(settings, "ssh_key_path", "") or ""))
    owner.edit_ssh_key.setPlaceholderText(tr("留空=用系统默认；例：C:\\Users\\me\\.ssh\\id_ed25519"))
    owner.edit_ssh_key.setToolTip(
        tr("SSH 克隆时通过 GIT_SSH_COMMAND 指定私钥；仅对本工具发起的 git 操作生效"))
    owner.edit_ssh_key.editingFinished.connect(owner._save_ssh_key)
    owner.btn_ssh_key = QPushButton(tr("浏览…"))
    owner.btn_ssh_key.clicked.connect(owner._pick_ssh_key)
    _ssh_lay.addWidget(owner.edit_ssh_key, 1)
    _ssh_lay.addWidget(owner.btn_ssh_key, 0)
    f3.addRow(tr("SSH 私钥："), _ssh_row)

    _network_rows_mirror(owner, settings, f3)

    # G38-7 常用主机（自建 GitLab 等，短格式可直接解析）
    owner.edit_custom_hosts = QLineEdit(_custom_hosts_text(settings))
    owner.edit_custom_hosts.setPlaceholderText(tr("mygit.example.com（逗号或换行分隔）"))
    owner.edit_custom_hosts.setToolTip(
        tr("登记自建主机后，短格式 `mygit.example.com/a/b` 可直接解析为 HTTPS 克隆地址"))
    owner.edit_custom_hosts.editingFinished.connect(owner._save_custom_hosts)
    f3.addRow(tr("常用主机："), owner.edit_custom_hosts)

    # G38-3/4/6 网络兼容开关
    owner.ck_precheck = QCheckBox(tr("克隆前做远端可达性预检（10s）"))
    owner.ck_precheck.setChecked(bool(getattr(settings, "precheck_remote", False)))
    owner.ck_precheck.setToolTip(tr("断网时 10s 内明确提示，不白等超时"))
    owner.ck_precheck.stateChanged.connect(owner._save_precheck)
    f3.addRow(tr("远端预检："), owner.ck_precheck)

    owner.ck_single_branch = QCheckBox(tr("浅克隆只拉目标分支（--single-branch）"))
    owner.ck_single_branch.setChecked(bool(getattr(settings, "single_branch", False)))
    owner.ck_single_branch.setToolTip(tr("浅克隆时减少 refs 传输量；增量更新兼容"))
    owner.ck_single_branch.stateChanged.connect(owner._save_single_branch)
    f3.addRow(tr("单分支："), owner.ck_single_branch)

    owner.ck_force_ipv4 = QCheckBox(tr("强制 HTTP/1.1（IPv6 兼容）"))
    owner.ck_force_ipv4.setChecked(bool(getattr(settings, "force_ipv4", False)))
    owner.ck_force_ipv4.setToolTip(tr("部分网络环境下 HTTP/2 兼容问题可用此项规避"))
    owner.ck_force_ipv4.stateChanged.connect(owner._save_force_ipv4)
    f3.addRow(tr("HTTP 版本："), owner.ck_force_ipv4)


def _network_rows_mirror(owner: MainWindow, settings: Settings, f3: QFormLayout) -> None:
    """G38-2/G58-2 镜像前缀 + 多备援开关 + 测速（拆出以守住函数长度上限）。"""
    owner.edit_mirror = QLineEdit(_mirror_text(settings))
    owner.edit_mirror.setPlaceholderText(
        tr("github.com=https://mirror-a, https://mirror-b（每行一个主机，多个镜像用逗号）"))
    owner.edit_mirror.setToolTip(tr(
        "按平台配置镜像前缀（仅 https 克隆生效；SSH 不拼）。"
        "一个主机可写多个镜像，按顺序尝试；全部失败自动回退源站。"))
    owner.edit_mirror.editingFinished.connect(owner._save_mirror)
    f3.addRow(tr("镜像前缀："), owner.edit_mirror)

    owner.ck_mirror_presets = QCheckBox(tr("使用内置国内镜像（GitHub）"))
    owner.ck_mirror_presets.setChecked(bool(getattr(settings, "mirror_use_presets", True)))
    owner.ck_mirror_presets.setToolTip(tr(
        "内置 4 个实测可用的 GitHub 镜像（第三方服务，实测日期 2026-10-18）。"
        "配置了令牌/主机凭据时不会走镜像，避免凭据泄漏。"))
    owner.ck_mirror_presets.toggled.connect(owner._save_mirror_presets)
    owner.ck_mirror_fallback = QCheckBox(tr("镜像失败自动重试（含回退源站）"))
    owner.ck_mirror_fallback.setChecked(bool(getattr(settings, "mirror_fallback", True)))
    owner.ck_mirror_fallback.setToolTip(tr(
        "镜像失败时在其它镜像/源站之间重试；关闭后仍会在镜像全不可用时直连源站，"
        "只是不再额外重试"))
    owner.ck_mirror_fallback.toggled.connect(owner._save_mirror_fallback)
    _mir_lay = QHBoxLayout()
    _mir_lay.addWidget(owner.ck_mirror_presets)
    _mir_lay.addWidget(owner.ck_mirror_fallback)
    owner.btn_mirror_probe = QPushButton(tr("测速并排序"))
    owner.btn_mirror_probe.setToolTip(tr(
        "用 git ls-remote 实测每个镜像（含内置）的连通性与延迟，"
        "把可用镜像按速度写回上面的输入框"))
    owner.btn_mirror_probe.clicked.connect(owner._probe_mirrors)
    _mir_lay.addWidget(owner.btn_mirror_probe)
    owner.spin_mirror_timeout = QSpinBox()
    owner.spin_mirror_timeout.setRange(3, 60)
    owner.spin_mirror_timeout.setSuffix(" s")
    owner.spin_mirror_timeout.setValue(
        int(getattr(settings, "mirror_probe_timeout", 8) or 8))
    owner.spin_mirror_timeout.setToolTip(tr("镜像连通性探测超时；网络很慢时可调大"))
    owner.spin_mirror_timeout.valueChanged.connect(owner._save_mirror_timeout)
    _mir_lay.addWidget(QLabel(tr("探测超时")))
    _mir_lay.addWidget(owner.spin_mirror_timeout)
    _mir_lay.addStretch()
    f3.addRow(tr("镜像备援："), _mir_lay)


def _build_group_appearance(owner: MainWindow, settings: Settings):
    """外观（主题 / 语言 / 强调色 / 减弱动效 / 字号复位 / 恢复默认）。"""
    g5 = QGroupBox(tr("外观（主题）"))
    g5.setToolTip(tr("主题 / 语言 / 强调色 / 减弱动效；「恢复默认」会重置除 token 外的全部外观与行为设置"))
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
    owner.theme_combo.addItem(tr("跟随系统（自动）"), "auto")
    cur = getattr(settings, "theme", "deep")
    idx = owner.theme_combo.findData(cur)
    if idx >= 0:
        owner.theme_combo.setCurrentIndex(idx)
    owner.theme_combo.currentIndexChanged.connect(owner._save_theme)
    f5.addRow(tr("界面主题："), owner.theme_combo)
    # G49-1 语言（重启后生效）
    owner.lang_combo = QComboBox()
    owner.lang_combo.addItem(tr("中文"), "zh")
    owner.lang_combo.addItem(tr("English"), "en")
    _lang = getattr(settings, "language", "zh") or "zh"
    _li = owner.lang_combo.findData(_lang)
    if _li >= 0:
        owner.lang_combo.setCurrentIndex(_li)
    owner.lang_combo.currentIndexChanged.connect(owner.settings_panel._save_language)
    f5.addRow(tr("语言（重启后生效）："), owner.lang_combo)
    # G46-7 强调色预设
    owner.accent_combo = QComboBox()
    for _k, _lbl in (("blue", tr("蓝色（默认）")), ("violet", tr("紫色")), ("teal", tr("青色"))):
        owner.accent_combo.addItem(_lbl, _k)
    _acc = getattr(settings, "accent_preset", "blue")
    _ai = owner.accent_combo.findData(_acc)
    if _ai >= 0:
        owner.accent_combo.setCurrentIndex(_ai)
    owner.accent_combo.currentIndexChanged.connect(owner.settings_panel._save_accent_preset)
    f5.addRow(tr("强调色："), owner.accent_combo)
    # G46-8 减少动态效果
    owner.ck_reduced_motion = QCheckBox(tr("减少动态效果（关闭完成动效/过渡）"))
    owner.ck_reduced_motion.setChecked(bool(getattr(settings, "prefers_reduced_motion", False)))
    owner.ck_reduced_motion.stateChanged.connect(owner.settings_panel._save_reduced_motion)
    f5.addRow(tr("动效："), owner.ck_reduced_motion)
    # G46-5 恢复默认字号
    owner.btn_font_reset = QPushButton(tr("恢复默认字号"))
    owner.btn_font_reset.clicked.connect(owner.settings_panel._reset_font_scale)
    f5.addRow(tr("字号："), owner.btn_font_reset)
    # G46-10 恢复默认设置（token/窗口位置保留）
    owner.btn_reset_defaults = QPushButton(tr("恢复默认设置（token 保留）"))
    owner.btn_reset_defaults.setToolTip(tr("除 token 与窗口位置外全部回默认值"))
    owner.btn_reset_defaults.clicked.connect(owner.settings_panel._reset_defaults)
    f5.addRow(tr("重置："), owner.btn_reset_defaults)
    return g5

def _build_group_about(owner: MainWindow):
    """关于与更新（版本 / 检查更新 / 自检 / 诊断 / 统计 / 导出 / 帮助 / 数据目录）。"""
    g4 = QGroupBox(tr("关于与更新"))
    g4.setToolTip(tr("版本信息与自检工具"))
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
    f4.addRow(tr("当前版本："), owner.lbl_version)
    h4 = QHBoxLayout()
    owner.btn_check_update = QPushButton(tr("检查更新"))
    owner.btn_check_update.clicked.connect(owner.check_update_now)
    h4.addWidget(owner.btn_check_update)
    owner.btn_selftest = QPushButton(tr("自检环境"))
    owner.btn_selftest.setToolTip(tr("检测 git / PyQt6 / 数据目录 / 当前并发 是否正常可用"))
    owner.btn_selftest.clicked.connect(owner._run_selftest)
    h4.addWidget(owner.btn_selftest)
    # G07-1 统计中心入口
    owner.btn_diag = QPushButton(tr("🩺 网络诊断"))
    owner.btn_diag.setToolTip(tr("G48-8：串行探测 git / TCP / GitHub API / 代理，输出分级报告"))
    owner.btn_diag.clicked.connect(owner.settings_panel._run_diag)
    h4.addWidget(owner.btn_diag)
    owner.btn_statistics = QPushButton(tr("📊 统计中心"))
    owner.btn_statistics.setToolTip(tr("查看仓库总数 / 同步次数 / 成功率 / 平台分布"))
    owner.btn_statistics.clicked.connect(owner.show_statistics)
    h4.addWidget(owner.btn_statistics)
    # G07-3 报表导出入口
    owner.btn_export = QPushButton(tr("📄 导出报表"))
    owner.btn_export.setToolTip(tr("导出 CSV（Excel 友好）或 Markdown 报表"))
    owner.btn_export.clicked.connect(owner.export_report)
    h4.addWidget(owner.btn_export)
    h4.addStretch()
    f4.addRow(tr("环境自检："), h4)
    # G46-11 使用说明 + 打开数据目录
    h_help = QHBoxLayout()
    owner.btn_help = QPushButton(tr("📖 使用说明"))
    owner.btn_help.setToolTip(tr("语法速查：@tag / host=token / 镜像 / 快捷键 / 拖拽 / 清单 / Star 导入"))
    owner.btn_help.clicked.connect(owner.settings_panel._open_help)
    h_help.addWidget(owner.btn_help)
    owner.btn_open_data = QPushButton(tr("打开数据目录"))
    owner.btn_open_data.setToolTip(tr("日志 / 备份 / 任务清单 所在目录"))
    owner.btn_open_data.clicked.connect(owner.settings_panel._open_data_dir)
    h_help.addWidget(owner.btn_open_data)
    h_help.addStretch()
    f4.addRow(tr("帮助："), h_help)
    # G54-4：随时可重新打开「开始使用清单」
    owner.btn_checklist = QPushButton(tr("开始使用清单"))
    owner.btn_checklist.setToolTip(tr("列出从零开始需要做的几件事，并显示完成进度"))
    owner.btn_checklist.clicked.connect(owner._show_checklist)
    f4.addRow("", owner.btn_checklist)
    return g4

def _build_group_webhook(owner: MainWindow, settings: Settings):
    """完成通知 Webhook（开关 + URL + token）。"""
    g6 = QGroupBox(tr("完成通知 Webhook"))
    g6.setToolTip(tr("仅显式开启 + 填写 URL 时才发送，失败不影响主流程"))
    f6 = QFormLayout(g6)
    f6.setContentsMargins(10, 10, 10, 10)
    f6.setHorizontalSpacing(16)
    f6.setVerticalSpacing(10)
    f6.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    f6.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    owner.ck_notify = QCheckBox(tr("启用完成通知 Webhook"))
    owner.ck_notify.setChecked(bool(getattr(settings, "notify_webhook_enabled", False)))
    owner.ck_notify.toggled.connect(owner.settings_panel._save_notify_enable)
    f6.addRow("", owner.ck_notify)
    owner.edit_webhook_url = QLineEdit(getattr(settings, "notify_webhook_url", "") or "")
    owner.edit_webhook_url.setPlaceholderText(tr("https://sctapi.ftqq.com/KEY.send（Server酱 / Telegram Bot 接口）"))
    owner.edit_webhook_url.editingFinished.connect(owner.settings_panel._save_notify_webhook)
    f6.addRow(tr("Webhook URL："), owner.edit_webhook_url)
    owner.edit_webhook_token = QLineEdit(getattr(settings, "notify_webhook_token", "") or "")
    owner.edit_webhook_token.setEchoMode(QLineEdit.EchoMode.Password)
    owner.edit_webhook_token.setPlaceholderText(tr("Webhook Token（可选）："))
    owner.edit_webhook_token.editingFinished.connect(owner.settings_panel._save_notify_webhook)
    f6.addRow(tr("Webhook Token（可选）："), owner.edit_webhook_token)
    # G67-6：通知审计台账入口（每次发送/重试/去重跳过都记一行，不含明文凭据）
    owner.btn_notify_audit = QPushButton(tr("查看通知审计"))
    owner.btn_notify_audit.setToolTip(tr("查看最近的通知发送记录（URL 与 token 均已打码）"))
    owner.btn_notify_audit.clicked.connect(owner._show_notify_audit)
    f6.addRow("", owner.btn_notify_audit)
    return g6

def _add_alert_section(owner, l7, row: int) -> int:
    """阈值告警：开关 / 间隔 / 陈旧天数 / 磁盘水位。返回下一可用行号。"""
    owner.ck_g53_alerts = QCheckBox(tr("启用阈值告警（陈旧/失败率/磁盘水位）"))
    owner.ck_g53_alerts.setChecked(bool(getattr(owner.settings, "g53_alerts_enabled", False)))
    owner.ck_g53_alerts.toggled.connect(owner.settings_panel._save_g53_alerts)
    l7.addWidget(owner.ck_g53_alerts, row, 0, 1, 2)
    l7.addWidget(QLabel(tr("告警间隔(分钟)：")), row + 1, 0)
    owner.spin_g53_interval = QSpinBox()
    owner.spin_g53_interval.setRange(5, 1440)
    owner.spin_g53_interval.setValue(
        int(getattr(owner.settings, "g53_alerts_interval_min", 60) or 60))
    owner.spin_g53_interval.valueChanged.connect(owner.settings_panel._save_g53_interval)
    l7.addWidget(owner.spin_g53_interval, row + 1, 1)
    # G69-18：补齐告警判定口径（此前字段有、UI 无 → 用户无法调整）
    l7.addWidget(QLabel(tr("陈旧告警阈值(天)：")), row + 2, 0)
    owner.spin_g53_stale = QSpinBox()
    owner.spin_g53_stale.setRange(1, 3650)
    owner.spin_g53_stale.setValue(
        int(getattr(owner.settings, "g53_alerts_stale_days", 30) or 30))
    owner.spin_g53_stale.valueChanged.connect(owner.settings_panel._save_g53_stale_days)
    l7.addWidget(owner.spin_g53_stale, row + 2, 1)
    l7.addWidget(QLabel(tr("磁盘水位阈值(GB)：")), row + 3, 0)
    owner.spin_g53_disk = QSpinBox()
    owner.spin_g53_disk.setRange(1, 100000)
    owner.spin_g53_disk.setValue(
        int(getattr(owner.settings, "g53_alerts_disk_min_gb", 5) or 5))
    owner.spin_g53_disk.valueChanged.connect(owner.settings_panel._save_g53_disk_min)
    l7.addWidget(owner.spin_g53_disk, row + 3, 1)
    return row + 4


def _add_history_section(owner, l7, row: int) -> int:
    """历史保留天数 + 立即清理。返回下一可用行号。"""
    l7.addWidget(QLabel(tr("历史保留天数：")), row, 0)
    owner.spin_g53_history = QSpinBox()
    owner.spin_g53_history.setRange(1, 3650)
    owner.spin_g53_history.setValue(
        int(getattr(owner.settings, "history_retention_days", 90) or 90))
    owner.spin_g53_history.valueChanged.connect(owner.settings_panel._save_g53_history)
    l7.addWidget(owner.spin_g53_history, row, 1)
    owner.btn_g53_clean = QPushButton(tr("立即清理历史"))
    owner.btn_g53_clean.clicked.connect(owner.settings_panel._clean_g53_history)
    l7.addWidget(owner.btn_g53_clean, row + 1, 0, 1, 2)
    return row + 2


def _add_sched_report_section(owner, l7, row: int) -> int:
    """定时报表：开关 / 间隔 / 输出目录。返回下一可用行号。

    G69-18：原实现只有开关，而 `_save_g53_sched_interval` / `_save_g53_sched_dir`
    从未被 connect，后者还引用了根本不存在的 `edit_g53_sched_dir`（一调即 AttributeError）。
    """
    owner.ck_g53_sched = QCheckBox(tr("启用定时报表"))
    owner.ck_g53_sched.setChecked(bool(getattr(owner.settings, "sched_report_enabled", False)))
    owner.ck_g53_sched.toggled.connect(owner.settings_panel._save_g53_sched)
    l7.addWidget(owner.ck_g53_sched, row, 0, 1, 2)
    l7.addWidget(QLabel(tr("报表间隔(分钟)：")), row + 1, 0)
    owner.spin_g53_sched_interval = QSpinBox()
    owner.spin_g53_sched_interval.setRange(1, 43200)
    owner.spin_g53_sched_interval.setValue(
        int(getattr(owner.settings, "sched_report_interval_min", 1440) or 1440))
    owner.spin_g53_sched_interval.valueChanged.connect(
        owner.settings_panel._save_g53_sched_interval)
    l7.addWidget(owner.spin_g53_sched_interval, row + 1, 1)
    l7.addWidget(QLabel(tr("报表输出目录：")), row + 2, 0)
    owner.edit_g53_sched_dir = QLineEdit(
        str(getattr(owner.settings, "sched_report_dir", "") or ""))
    owner.edit_g53_sched_dir.setPlaceholderText(tr("留空 = 数据目录下 reports/"))
    owner.edit_g53_sched_dir.editingFinished.connect(owner.settings_panel._save_g53_sched_dir)
    l7.addWidget(owner.edit_g53_sched_dir, row + 2, 1)
    return row + 3


def _add_reliability_section(owner, l7, row: int) -> int:
    """稳定性：失败自动重试 + 看门狗续跑。返回下一可用行号。

    G69-18：两者后端早已齐备（engine 读 `retry_enabled` / `watchdog_enabled`），
    但默认 False 且**没有任何 UI 入口** → 用户永远开不了 = 用户不可达。
    """
    owner.ck_retry = QCheckBox(tr("失败自动重试（仅网络类错误）"))
    owner.ck_retry.setChecked(bool(getattr(owner.settings, "retry_enabled", False)))
    owner.ck_retry.toggled.connect(owner.settings_panel._save_retry_enabled)
    l7.addWidget(owner.ck_retry, row, 0, 1, 2)
    l7.addWidget(QLabel(tr("最多重试次数：")), row + 1, 0)
    owner.spin_retry = QSpinBox()
    owner.spin_retry.setRange(0, 3)
    owner.spin_retry.setValue(int(getattr(owner.settings, "retry_max", 1) or 0))
    owner.spin_retry.valueChanged.connect(owner.settings_panel._save_retry_max)
    l7.addWidget(owner.spin_retry, row + 1, 1)
    owner.ck_watchdog = QCheckBox(tr("看门狗自动续跑（中断后自动接续未完成任务）"))
    owner.ck_watchdog.setChecked(bool(getattr(owner.settings, "watchdog_enabled", False)))
    owner.ck_watchdog.toggled.connect(owner.settings_panel._save_watchdog_enabled)
    l7.addWidget(owner.ck_watchdog, row + 2, 0, 1, 2)
    l7.addWidget(QLabel(tr("看门狗间隔(分钟)：")), row + 3, 0)
    owner.spin_watchdog = QSpinBox()
    owner.spin_watchdog.setRange(1, 240)
    owner.spin_watchdog.setValue(
        int(getattr(owner.settings, "watchdog_interval_min", 5) or 5))
    owner.spin_watchdog.valueChanged.connect(owner.settings_panel._save_watchdog_interval)
    l7.addWidget(owner.spin_watchdog, row + 3, 1)
    return row + 4


def _add_im_alert_section(owner, l7, row: int) -> int:
    """企业 IM 告警：渠道 / URL / Token。返回下一可用行号。

    G69-18：后端链路（模板 + token 加密落盘 + AlertScanner 调用）早已打通，
    但**没有任何 UI 写入点** → 默认空 = 永不发送 = 用户不可达。
    """
    l7.addWidget(QLabel(tr("企业 IM 告警渠道：")), row, 0)
    owner.combo_im_channel = QComboBox()
    for _label, _val in ((tr("关闭"), ""), ("钉钉", "dingtalk"),
                         ("飞书", "feishu"), ("企业微信", "wecom")):
        owner.combo_im_channel.addItem(_label, _val)
    _cur_ch = str(getattr(owner.settings, "alert_im_channel", "") or "")
    _idx = owner.combo_im_channel.findData(_cur_ch)
    owner.combo_im_channel.setCurrentIndex(_idx if _idx >= 0 else 0)
    owner.combo_im_channel.currentIndexChanged.connect(owner.settings_panel._save_alert_im)
    l7.addWidget(owner.combo_im_channel, row, 1)
    l7.addWidget(QLabel(tr("IM Webhook URL：")), row + 1, 0)
    owner.edit_im_url = QLineEdit(str(getattr(owner.settings, "alert_im_url", "") or ""))
    owner.edit_im_url.editingFinished.connect(owner.settings_panel._save_alert_im)
    l7.addWidget(owner.edit_im_url, row + 1, 1)
    l7.addWidget(QLabel(tr("IM Webhook Token（可选）：")), row + 2, 0)
    owner.edit_im_token = QLineEdit(str(getattr(owner.settings, "alert_im_token", "") or ""))
    owner.edit_im_token.setEchoMode(QLineEdit.EchoMode.Password)
    owner.edit_im_token.editingFinished.connect(owner.settings_panel._save_alert_im)
    l7.addWidget(owner.edit_im_token, row + 2, 1)
    return row + 3


def _build_group_data_insights(owner: MainWindow):
    """G53 数据洞察（阈值告警 / 历史保留 / 定时报表 / 稳定性 / IM 告警）。

    G69-18：本函数曾因补齐控件涨到 100 行（项目上限 50 行），
    现按主题拆为 5 个子构建器，本函数只做编排。
    """
    g7 = QGroupBox(tr("G53 数据洞察（告警 / 历史保留 / 定时报表）"))
    g7.setToolTip(tr("多维评分卡/阈值告警/历史保留天数/定时报表 设置"))
    l7 = QGridLayout(g7)
    row = _add_alert_section(owner, l7, 0)
    row = _add_history_section(owner, l7, row)
    row = _add_sched_report_section(owner, l7, row)
    row = _add_reliability_section(owner, l7, row)
    _add_im_alert_section(owner, l7, row)
    return g7


def build_settings_ui(owner: MainWindow) -> QScrollArea:
    """在 owner 上构建设置页全部控件并返回滚动区（挂到 owner.settings_scroll）。

    G68-1：本函数只做**编排** —— 外壳在 `_build_settings_shell`，各组在 `_build_group_*`。
    """
    settings = owner.settings  # 各组控件的初值来源
    settings_scroll, sv, groups = _build_settings_shell(owner)

    # 需要 settings 的分组（控件初值来自配置）显式传参，不需要的只传 owner ——
    # 比「统一传两个、其中一个用不到」更难写错。
    for g in (_build_group_startup(owner, settings),
              _build_group_network(owner, settings),
              _build_group_appearance(owner, settings),
              _build_group_about(owner),
              _build_group_webhook(owner, settings)):
        sv.addWidget(g)
        groups.append(g)

    # G53-2：扩展组不进 `groups`（避免破坏既有解构），单独登记
    g7 = _build_group_data_insights(owner)
    owner.settings_groups_extra.append(g7)
    groups2_layout = QVBoxLayout()
    groups2_layout.addWidget(g7)
    sv.addLayout(groups2_layout)
    sv.addStretch()
    return settings_scroll

