"""G71-7 下载中心顶部区域构建（从 `_build_download_tab` 抽出）。

原 `_build_download_tab` 191 行，其中 160 行是**纯控件搭建**（输入框/实时校验/
快捷填充/任务清单/Star 导入/下载位置/克隆模式/操作按钮）。抽出来后：
- `main_window._build_download_tab` 只负责布局拼装（滚动区 + 分隔器 + 进度表）；
- 本模块可独立被 UI 测试构造（仍把所有控件挂回 owner，外部引用不变）。

**行为零变化**：控件名、信号连接、tooltip、无障碍名、拖拽弱引用包装逐项照抄。
"""
from __future__ import annotations

import re
import weakref

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
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

from ..i18n import tr

# 快捷填充地址的分隔符：逗号（中英文）、分号、空白。允许用户直接粘贴一堆地址。
_QUICK_SPLIT = re.compile(r"[,;，；\s]+")


def sync_quick_scroll_height(owner) -> None:
    """G78：快捷填充滚动区的高度跟着 chip 内容走，**不再写死像素值**。

    为什么改：原实现 `setFixedHeight(38)`，而 chip 按钮实测 `sizeHint().height() == 42`
    → 底部 4px 被裁（chip 的圆角下沿与文字基线以下整段消失）。
    这在 **1440×900 标准模式**下就能看到，不是"小屏 + 缩放"的锅
    （早先 `known-issues.md` 把病因归成了缩放，特此更正）。
    字号缩放 / 主题切换都会改变 chip 高度，所以每次重建都重算一次。
    """
    scroll = getattr(owner, "quick_scroll", None)
    if scroll is None:
        return  # 首次构建时 scroll 还没挂到 owner（rebuild 先于赋值）
    heights = [b.sizeHint().height()
               for b in (getattr(owner, "_quick_fill_buttons", None) or [])]
    # +2 是 chip 与视口之间的 1px 余量；没有 chip 时退回历史值 38 附近
    scroll.setFixedHeight((max(heights) if heights else 36) + 2)


def rebuild_quick_fill(owner) -> int:
    """重建「快捷填充」chip 行，返回按钮数（供测试断言）。

    G76：`quick_repos` 此前只能手改 `settings.json`（changelog 承诺了「快捷填充可配置」），
    现在由 `edit_quick_repos()` 编辑后调用本函数重建。`_on_quick_fill` 复用既有回调。
    """
    lay = getattr(owner, "_quick_fill_layout", None)
    if lay is None:
        return 0
    while lay.count():
        item = lay.takeAt(0)
        w = item.widget()
        if w is not None:
            w.deleteLater()
    buttons = []
    for repo in getattr(owner.settings, "quick_repos", None) or ():
        btn = QPushButton(str(repo))
        btn.setProperty("quick_repo", str(repo))
        btn.clicked.connect(owner._on_quick_fill)
        lay.addWidget(btn)
        buttons.append(btn)
    lay.addStretch()
    owner._quick_fill_buttons = buttons
    sync_quick_scroll_height(owner)   # G78：增删 chip 后高度跟着变
    return len(buttons)


def parse_quick_repos(text: str) -> tuple[str, ...]:
    """把用户输入的一串地址解析成去重后的元组（保持输入顺序）。"""
    seen: dict[str, None] = {}
    for seg in _QUICK_SPLIT.split(text or ""):
        seg = seg.strip()
        if seg:
            seen.setdefault(seg, None)
    return tuple(seen)


def edit_quick_repos(owner) -> int:
    """快捷填充的增删改入口；确认后写盘并重建 chip。返回新按钮数。"""
    from PyQt6.QtWidgets import QInputDialog
    cur = ", ".join(str(r) for r in (getattr(owner.settings, "quick_repos", None) or ()))
    text, ok = QInputDialog.getText(
        owner, tr("编辑快捷填充"),
        tr("用逗号或换行分隔仓库地址（留空 = 不显示任何快捷按钮）："), text=cur)
    if not ok:
        return len(getattr(owner, "_quick_fill_buttons", None) or [])
    owner.settings.quick_repos = parse_quick_repos(text)
    owner.settings_store.save(owner.settings)
    return rebuild_quick_fill(owner)


def build_download_top(owner) -> QWidget:
    """构建下载中心上半区并返回容器；所有控件挂到 `owner` 上（兼容既有引用）。"""
    box = QGroupBox(tr("仓库地址（每行一个）"))
    b = QVBoxLayout(box)
    owner.repo_input = QPlainTextEdit()
    owner.repo_input.setPlaceholderText(
        tr("每行一个仓库地址，例如：\n")
        + "https://github.com/vercel-labs/skills\n"
        + "git@github.com:microsoft/azure-skills.git"
    )
    # G74：输入框高度 80~112px 只放得下 3~4 行；更长的说明放 tooltip，
    # 否则 1024/1366 下占位文案会在第 4 行被裁成半截字。
    owner.repo_input.setToolTip(
        tr("支持 GitLab / Gitee / Codeberg / Bitbucket / 自建主机；\n"
           "可在地址后加 @分支或@标签（如 repo@v1.2.0）；\n"
           "地址中的 作者/仓库名 将自动作为文件夹名：作者__仓库。"))
    owner.repo_input.setMinimumHeight(80)
    # G74：限制最大高度，保证 1366/1440 下「下载位置/克隆模式/快捷填充/清单」
    # 都能落在首屏；更多地址可在输入框内滚动。
    owner.repo_input.setMaximumHeight(112)
    owner.repo_input.setAccessibleName(tr("仓库地址输入框"))
    b.addWidget(owner.repo_input)
    # G54-1 实时校验条：输入/粘贴后显示「有效 N / 无效 M」
    owner.repo_valid_lbl = QLabel("")
    owner.repo_valid_lbl.setWordWrap(True)
    owner.repo_valid_lbl.setTextFormat(Qt.TextFormat.PlainText)
    owner.repo_valid_lbl.setVisible(False)  # 有内容时才出现（G74）
    b.addWidget(owner.repo_valid_lbl)
    owner._validate_timer = QTimer(owner)
    owner._validate_timer.setSingleShot(True)
    owner._validate_timer.setInterval(300)
    owner._validate_timer.timeout.connect(owner._refresh_url_validation)
    owner.repo_input.textChanged.connect(owner._on_input_changed)
    owner.repo_input.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    owner.repo_input.customContextMenuRequested.connect(owner._repo_input_menu)
    owner.repo_input.setAcceptDrops(True)
    # G69-18：弱引用包装，避免「控件 → 绑定方法 → 窗口 → 控件」引用环
    self_ref = weakref.ref(owner)

    def _drag_enter(e):
        me = self_ref()
        if me is not None:
            me._repo_input_drag_enter(e)

    def _drop(e):
        me = self_ref()
        if me is not None:
            me._repo_input_drop(e)

    owner.repo_input.dragEnterEvent = _drag_enter
    owner.repo_input.dropEvent = _drop

    # G74：快捷填充按钮放进可横向滚动的容器 —— 6 个按钮 + 右侧功能按钮在
    # 1366/1024 下会互相挤压，原来靠 QScrollArea 关掉横向滚动直接裁掉控件。
    quick_row = QHBoxLayout()
    quick_row.setSpacing(6)
    owner.quick_label = QLabel(tr("快捷填充："))
    quick_row.addWidget(owner.quick_label)
    quick_scroll = QScrollArea()
    quick_scroll.setObjectName("quickFillScroll")
    quick_scroll.setWidgetResizable(True)
    quick_scroll.setFrameShape(QFrame.Shape.NoFrame)
    quick_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    quick_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    # G78：高度不再写死 38（chip 实测 42 → 底部被裁 4px），改由
    # sync_quick_scroll_height() 按内容推导；owner.quick_scroll 赋值后立即同步。
    quick_inner = QWidget()
    quick = QHBoxLayout(quick_inner)
    quick.setContentsMargins(0, 0, 0, 0)
    quick.setSpacing(6)
    owner._quick_fill_layout = quick
    owner._quick_fill_buttons = []
    rebuild_quick_fill(owner)  # G76：chip 构建逻辑集中到一处，编辑后可原地重建
    quick_scroll.setWidget(quick_inner)
    quick_row.addWidget(quick_scroll, 1)
    owner.quick_scroll = quick_scroll
    sync_quick_scroll_height(owner)   # G78：按 chip 实际高度定滚动区高度
    # G76：快捷填充的编辑入口（此前只能手改 settings.json）
    owner.btn_edit_quick = QPushButton(tr("编辑"))
    owner.btn_edit_quick.setToolTip(tr("增删「快捷填充」按钮里的仓库地址（逗号或换行分隔）"))
    owner.btn_edit_quick.clicked.connect(owner._edit_quick_repos)
    quick_row.addWidget(owner.btn_edit_quick)

    # 任务清单 / Star 导入单独一行，保证 1024 宽也完整可见
    list_row = QHBoxLayout()
    list_row.setSpacing(6)
    # G35-4 任务清单
    owner.btn_save_list = QPushButton(tr("保存清单"))
    owner.btn_save_list.setToolTip(
        tr("把当前输入区的地址保存为命名任务清单（data/lists/*.json）"))
    owner.btn_save_list.clicked.connect(owner._save_task_list)
    owner.combo_load_list = QComboBox()
    owner.combo_load_list.setToolTip(tr("选择已保存的清单一键载入"))
    owner.combo_load_list.setMinimumWidth(160)
    owner.btn_load_list = QPushButton(tr("载入"))
    owner.btn_load_list.setToolTip(tr("把所选清单的地址填入输入区"))
    owner.btn_load_list.clicked.connect(owner._load_task_list)
    list_row.addWidget(owner.btn_save_list)
    list_row.addWidget(owner.combo_load_list, 1)
    list_row.addWidget(owner.btn_load_list)
    # G35-10 GitHub Star 列表导入
    owner.btn_star_import = QPushButton(tr("导入 Star"))
    owner.btn_star_import.setToolTip(
        tr("按 GitHub 用户名拉取 Star 列表并填入输入区（需要时在设置页填写令牌）"))
    owner.btn_star_import.clicked.connect(owner.import_starred)
    list_row.addWidget(owner.btn_star_import)
    list_row.addStretch(1)

    top = QWidget()
    tv = QVBoxLayout(top)
    tv.setContentsMargins(0, 0, 0, 0)
    tv.setSpacing(10)
    tv.addWidget(box)

    opts = QHBoxLayout()
    g1 = QGroupBox(tr("下载位置"))
    l1 = QHBoxLayout(g1)
    default_dir = str(owner.settings.download_dir or (owner.data_dir / "clones"))
    owner.target_edit = QLineEdit(default_dir)
    btn_browse = QPushButton(tr("浏览…"))
    btn_browse.clicked.connect(owner.choose_target)
    l1.addWidget(owner.target_edit, 1)
    l1.addWidget(btn_browse)
    opts.addWidget(g1, 1)

    g2 = QGroupBox(tr("克隆模式"))
    l2 = QHBoxLayout(g2)
    owner.mode_combo = QComboBox()
    owner.mode_combo.addItems([tr("满量克隆（完整历史）"), tr("浅克隆（最新代码）"),
                               tr("单分支浅克隆（--single-branch）"),
                               tr("镜像克隆（--mirror）")])
    owner.depth_spin = QSpinBox()
    owner.depth_spin.setRange(1, 10000)
    owner.depth_spin.setValue(1)
    owner.depth_spin.setSuffix(" 层")
    owner.depth_spin.setEnabled(False)
    owner.mode_combo.currentIndexChanged.connect(owner._on_mode_changed)
    l2.addWidget(owner.mode_combo)
    l2.addWidget(owner.depth_spin)
    opts.addWidget(g2)
    tv.addLayout(opts)
    # G74：快捷填充/清单是次要操作，放在下载位置/克隆模式之后；
    # 小高度时先保证核心选项可见。
    tv.addLayout(quick_row)
    tv.addLayout(list_row)
    return top


def build_download_actions(owner) -> QWidget:
    """G74：主操作区（固定在滚动区之外，任何分辨率都可见）。

    原实现把 7 个按钮放在下载页顶部 QScrollArea 内；1366/1024 下它们被
    滚动区裁掉，用户看不到「开始/取消」，只能盲目滚动。现在拆成两行：
    第一行开始/取消/清空，第二行打开目录/失败汇总/重试队列/诊断包。
    """
    wrap = QWidget()
    v = QVBoxLayout(wrap)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(6)

    row1 = QHBoxLayout()
    owner.btn_start = QPushButton(tr("开始并行下载 / 更新"))
    owner.btn_start.setObjectName("primary")
    owner.btn_start.setMinimumHeight(38)
    owner.btn_start.setAccessibleName(tr("开始并行下载或更新"))
    owner.btn_start.clicked.connect(owner.start_all)
    owner.btn_start.setIcon(owner._std_icon("play", owner.btn_start))
    owner.btn_cancel = QPushButton(tr("取消全部"))
    owner.btn_cancel.setEnabled(False)
    owner.btn_cancel.setAccessibleName(tr("取消全部任务"))
    owner.btn_cancel.clicked.connect(owner.cancel_all)
    owner.btn_cancel.setIcon(owner._std_icon("stop", owner.btn_cancel))
    owner.btn_clear = QPushButton(tr("清空列表"))
    owner.btn_clear.clicked.connect(owner.repo_input.clear)
    # G75：主按钮给足权重但不拉满整行 —— 满宽横幅是模板相，不是"一个主操作"
    owner.btn_start.setMaximumWidth(420)
    row1.addWidget(owner.btn_start)
    row1.addWidget(owner.btn_cancel)
    row1.addWidget(owner.btn_clear)
    row1.addStretch(1)
    v.addLayout(row1)

    row2 = QHBoxLayout()
    owner.btn_open = QPushButton(tr("打开克隆目录"))
    owner.btn_open.clicked.connect(owner.open_target)
    owner.btn_open.setIcon(owner._std_icon("open_dir", owner.btn_open))
    row2.addWidget(owner.btn_open)
    # G52-5：失败原因聚合面板入口
    owner.btn_failures = QPushButton(tr("失败汇总"))
    owner.btn_failures.clicked.connect(owner._open_failure_dialog)
    row2.addWidget(owner.btn_failures)
    # G71-4：重试队列可见化
    owner.btn_retry_queue = QPushButton(tr("重试队列"))
    owner.btn_retry_queue.setToolTip(
        tr("查看自动重试队列：失败次数、下次重试时间、原因与处理建议"))
    owner.btn_retry_queue.clicked.connect(owner._open_retry_dialog)
    row2.addWidget(owner.btn_retry_queue)
    # G71-6：一键导出诊断包
    owner.btn_diag_bundle = QPushButton(tr("诊断包"))
    owner.btn_diag_bundle.setToolTip(
        tr("导出 zip 诊断包：状态报告 + 最近事件 + 日志尾部（全部已打码）"))
    owner.btn_diag_bundle.clicked.connect(owner._export_diag_bundle)
    row2.addWidget(owner.btn_diag_bundle)
    row2.addStretch(1)
    v.addLayout(row2)
    return wrap
