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
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QToolButton,
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
    # Rev14 R14-03 修正：QPlainTextEdit 默认 `tabChangesFocus=False`，Tab 会被当成
    # 文本缩进 —— 用户在地址框按 Tab 想跳到选项，结果往输入框里插了一个制表符，
    # 声明好的 `repo_input → btn_readme_import` 永远不可达。这里让 Tab 交还焦点链。
    owner.repo_input.setTabChangesFocus(True)
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
    # G79b：紧凑模式下 chip 滚动区被收起，它的伸缩空间会轮到本按钮 → 按钮被拉成
    # 通栏「编辑」。固定为自然宽度，两种布局模式下都保持紧凑。
    owner.btn_edit_quick.setSizePolicy(
        QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    quick_row.addWidget(owner.btn_edit_quick)
    # Rev12 U-17：地址历史从「右键菜单深处」提到快捷填充行的常驻入口。
    # 不新增布局行（与快捷填充/编辑同一行，挤占的是已有 stretch），下拉列前 10 条。
    from PyQt6.QtWidgets import QMenu as _QMenu
    from PyQt6.QtWidgets import QToolButton
    owner.btn_recent = QToolButton()
    owner.btn_recent.setText(tr("历史 ▾"))
    owner.btn_recent.setToolTip(tr("最近同步过的地址（也可在输入框里右键打开）"))
    owner.btn_recent.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    owner.btn_recent.setObjectName("ghost")
    owner.btn_recent.setAccessibleName(tr("最近地址历史"))
    owner._recent_menu = _QMenu(owner.btn_recent)

    # U-17 实测教训：闭包捕 `owner` 会形成 owner↔闭包引用环（历史 SIGSEGV 同根因，
    # g78 多窗串联测试在第二窗构造时挂起，stash 对照确认回归来自本改动）。
    # 改 weakref 范式（照 undo_bar 先例）：槽经弱引用解引用，动作回填同样不捕 owner。
    import weakref as _weakref
    _owner_ref = _weakref.ref(owner)
    _menu_ref = _weakref.ref(owner._recent_menu)

    def _recent_append(url: str) -> None:
        w = _owner_ref()
        if w is not None:
            w.repo_input.appendPlainText(url + "\n")

    def _rebuild_recent_menu() -> None:
        """重建历史下拉（每次点开前刷新，条目随同步滚动）。"""
        w = _owner_ref()
        m = _menu_ref()
        if w is None or m is None:
            return
        m.clear()
        try:
            items = (w.url_history.items() or [])[:10]
        except Exception:   # noqa: BLE001 —— 历史读不到时菜单显示空态
            items = []
        if not items:
            empty = m.addAction(tr("（暂无历史）"))
            empty.setEnabled(False)
            return
        for u in items:
            act = m.addAction(u)
            act.triggered.connect(
                lambda _ch=False, url=u: _recent_append(url))

    def _show_recent_menu() -> None:
        _rebuild_recent_menu()
        m = _menu_ref()
        w = _owner_ref()
        if m is None or w is None:
            return
        m.exec(w.btn_recent.mapToGlobal(w.btn_recent.rect().bottomLeft()))

    owner._rebuild_recent_menu = _rebuild_recent_menu
    owner.btn_recent.clicked.connect(_show_recent_menu)
    quick_row.addWidget(owner.btn_recent)

    top = QWidget()
    tv = QVBoxLayout(top)
    tv.setContentsMargins(0, 0, 0, 0)
    tv.setSpacing(10)
    tv.addWidget(box)

    # G74：快捷填充/清单是次要操作，留在可滚动区里（小高度时可滚出视口）
    # Rev16 B3 更正：快捷填充行（chips + 编辑 + 历史）在 1366/1024/900 的
    # 首屏都不可见（滚动视口 120px，内容最小 172px，该行在视口下方）——
    # 用户不知道有快捷填充。移出滚动区成为常驻行（与「下载位置/克隆模式」
    # 同级）；它只有一行 ~42px，换来的是高频入口全程可见。紧凑模式下仍整行
    # 隐藏（chip 输入方式随 _quick_fill_buttons 一起收起）。
    quick_host = QWidget()
    quick_host.setObjectName("quickFillRow")
    qh = QHBoxLayout(quick_host)
    qh.setContentsMargins(0, 0, 0, 0)
    qh.setSpacing(6)
    qh.addLayout(quick_row)
    tv.addWidget(quick_host)
    # 返回常驻宿主，主窗把它插到 top_container（滚动区外）
    owner._quick_row_host = quick_host
    return top


def build_download_options(owner) -> QWidget:
    """下载位置 + 克隆模式（Rev14 F1/F7：**常驻，不进滚动区**）。

    **为什么从滚动区里搬出来**（实测，不是观感）：
    - 1024×768 / 1366×768 / 1280×800 三档下，这两个控件被顶部滚动区切到只剩
      `visibleRegion` 352×**6** / 335×6 / 608×23（控件本身 34 高）—— 用户看到的
      是一个只有一条边的输入框，点都点不准；
    - 顶部区内容需要 298px、视口只给 226px，**连 1920×1080 都要滚动**才能看到
      「克隆模式」下拉。
    这两件事与「开始」按钮同属最高频操作（粘地址 → 选模式 → 点开始），
    不该藏在折叠线以下。搬出后滚动区只剩「仓库地址 + 快捷填充」，各档都不需要滚动。
    """
    wrap = QWidget()
    opts = QHBoxLayout(wrap)
    opts.setContentsMargins(0, 0, 0, 0)
    opts.setSpacing(10)

    g1 = QGroupBox(tr("下载位置"))
    l1 = QHBoxLayout(g1)
    default_dir = str(owner.settings.download_dir or (owner.data_dir / "clones"))
    owner.target_edit = QLineEdit(default_dir)
    # Rev14 R14-04 修正：这一组控件没有相邻 QLabel（标题在 QGroupBox 上），
    # 推导链取不到名字 —— 显式命名，否则读屏只报「编辑框」。
    owner.target_edit.setAccessibleName(tr("下载位置"))
    owner.target_edit.setAccessibleDescription(tr("仓库克隆到哪个目录；可点「浏览…」选择"))
    btn_browse = QPushButton(tr("浏览…"))
    btn_browse.clicked.connect(owner.choose_target)
    l1.addWidget(owner.target_edit, 1)
    l1.addWidget(btn_browse)
    opts.addWidget(g1, 1)

    g2 = QGroupBox(tr("克隆模式"))
    l2 = QHBoxLayout(g2)
    owner.mode_combo = QComboBox()
    owner.mode_combo.setAccessibleName(tr("克隆模式"))
    owner.mode_combo.setAccessibleDescription(
        tr("满量克隆 / 浅克隆 / 单分支浅克隆 / 镜像克隆 四选一"))
    owner.mode_combo.addItems([tr("满量克隆（完整历史）"), tr("浅克隆（最新代码）"),
                               tr("单分支浅克隆（--single-branch）"),
                               tr("镜像克隆（--mirror）")])
    owner.depth_spin = QSpinBox()
    owner.depth_spin.setRange(1, 10000)
    owner.depth_spin.setValue(1)
    owner.depth_spin.setSuffix(tr(" 层"))
    owner.depth_spin.setEnabled(False)
    owner.depth_spin.setAccessibleName(tr("浅克隆深度（层）"))
    owner.mode_combo.currentIndexChanged.connect(owner._on_mode_changed)
    l2.addWidget(owner.mode_combo)
    l2.addWidget(owner.depth_spin)
    opts.addWidget(g2)
    return wrap


def build_task_list_row(owner) -> QWidget:
    """任务清单 / Star 导入一行（**常驻**，不放进顶部滚动区）。

    v9.12.1 / Rev7 X-4：这三四个控件原本是 `build_download_top()` 里的最后一行，
    属于顶部 `download_top_scroll` 的内容 —— 窗口变矮时它被滚出可视区：
    实测 1366×768 笔记本（窗口 1240×729）下「保存清单/载入/导入 Star」只露 **10px**
    （`visibleRegion` 86×10 / 86×42），看起来就是四个**空框**；1080p@150%
    （1216×684）下完全不可见（0×0）。
    这与 G74 把「开始/取消」搬出滚动区是同一个判断：**次要操作可以滚，主操作不行**。
    """
    wrap = QWidget()
    list_row = QHBoxLayout(wrap)
    list_row.setContentsMargins(0, 0, 0, 0)
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
    # G84-18：从 README / awesome 清单批量导入地址（纯本地解析，零网络）
    owner.btn_readme_import = QPushButton(tr("导入清单…"))
    owner.btn_readme_import.setToolTip(
        tr("从 README / awesome 清单 / 收藏页文本里批量提取仓库地址并填入输入区"))
    owner.btn_readme_import.clicked.connect(owner.import_readme_list)
    list_row.addWidget(owner.btn_readme_import)
    # Rev10 P-02：执行前预览差异表（纯本地预检，不联网不写盘）
    owner.btn_preview = QPushButton(tr("预览…"))
    owner.btn_preview.setToolTip(
        tr("看这一批将克隆/更新/删除重建/被占/重复/无效的逐条计划（不联网、不写盘）"))
    owner.btn_preview.clicked.connect(owner.open_batch_preview)
    list_row.addWidget(owner.btn_preview)
    list_row.addStretch(1)
    return wrap


def build_more_tools_button(owner) -> QToolButton:
    """R13-02：折叠态的「更多 ▸」按钮（矮窗时承接四个工具入口，入口与槽不变）。

    Rev17 四轨审查（D22 同类）：U+25B8 在微软雅黑**无字形**（QFontMetrics.inFontUcs4
    实测 False；✓✕⚠⊘ tofu 教训的字符符号同类风险）。文本改纯「更多」，
    方向指示交给 `setArrowType(DownArrow)`——Qt 内置 pixmap，零字体依赖，
    随弹出态自动翻转语义（菜单打开即向下，语义不变）。
    """
    btn = QToolButton()
    btn.setText(tr("更多"))
    # 方向指示不加：DownArrow icon 会把 sizeHint 抬 1px（22→23，实测），折叠态
    # wrap 高度基线跟着变矮 → 900×480 下按钮底部 3px 被父级裁掉
    # （test_rev13_adapt.TestToolsFold 实测抓出）。弹出菜单的交互暗示由
    # InstantPopup 的按下态承担，不再引字符符号（D22 同类：U+25B8 雅黑无字形）。
    btn.setObjectName("ghost")  # Rev11-C R-03: 折叠态同一权重
    btn.setToolTip(tr("打开克隆目录 / 失败汇总 / 重试队列 / 诊断包"))
    btn.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
    btn.setAccessibleName(tr("更多工具菜单"))
    btn.hide()
    owner.btn_more_tools = btn
    return btn


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

    # v9.12.1 / Rev7 X-4：清单行常驻（原本在顶部滚动区里，1366×768 下被裁成 10px）
    v.addWidget(build_task_list_row(owner))

    row1 = QHBoxLayout()
    owner.btn_start = QPushButton(tr("开始并行下载 / 更新"))
    owner.btn_start.setObjectName("primary")
    owner.btn_start.setMinimumHeight(38)
    owner.btn_start.setAccessibleName(tr("开始并行下载或更新"))
    owner.btn_start.clicked.connect(owner.start_all)
    owner.btn_start.setIcon(owner._std_icon("play", owner.btn_start))
    owner.btn_cancel = QPushButton(tr("取消全部"))
    owner.btn_cancel.setObjectName("secondary")  # Rev11-C R-03: 批次控制区权重
    owner.btn_cancel.setEnabled(False)
    owner.btn_cancel.setAccessibleName(tr("取消全部任务"))
    owner.btn_cancel.clicked.connect(owner.cancel_all)
    owner.btn_cancel.setIcon(owner._std_icon("stop", owner.btn_cancel))
    owner.btn_clear = QPushButton(tr("清空输入区"))  # A5-4/U-18：原名「清空列表」与行为不符
    owner.btn_clear.setObjectName("ghost")  # Rev11-C R-03: 有确认+撤销兜底, 低频权重
    owner.btn_clear.clicked.connect(owner.confirm_clear_input)
    # C2-6 / Rev7 B2-2：批次级「暂停/继续」——原来只有"单仓库暂停"和"全部取消"，
    # 挂机刷几百个仓库时想"先停十分钟再接着跑"做不到，只能取消重来。
    owner.btn_pause_batch = QPushButton(tr("暂停"))
    owner.btn_pause_batch.setObjectName("secondary")  # Rev11-C R-03: 批次控制区权重
    owner.btn_pause_batch.setEnabled(False)
    owner.btn_pause_batch.setToolTip(tr(
        "暂停后不再开始新仓库；正在跑的会自然结束。再点一次继续。\n"
        "与「取消全部」不同：暂停不丢进度、不终止在途任务。"))
    owner.btn_pause_batch.clicked.connect(owner.toggle_batch_pause)
    # G75：主按钮给足权重但不拉满整行 —— 满宽横幅是模板相，不是"一个主操作"
    owner.btn_start.setMaximumWidth(420)
    row1.addWidget(owner.btn_start)
    row1.addWidget(owner.btn_cancel)
    row1.addWidget(owner.btn_pause_batch)
    row1.addWidget(owner.btn_clear)
    row1.addStretch(1)
    v.addLayout(row1)

    row2 = QHBoxLayout()
    owner.btn_open = QPushButton(tr("打开克隆目录"))
    owner.btn_open.setObjectName("ghost")  # Rev11-C R-03: 工具区权重
    owner.btn_open.clicked.connect(owner.open_target)
    owner.btn_open.setIcon(owner._std_icon("open_dir", owner.btn_open))
    row2.addWidget(owner.btn_open)
    # G52-5：失败原因聚合面板入口
    owner.btn_failures = QPushButton(tr("失败汇总"))
    owner.btn_failures.setObjectName("ghost")  # Rev11-C R-03: 工具区权重
    owner.btn_failures.clicked.connect(owner._open_failure_dialog)
    row2.addWidget(owner.btn_failures)
    # G71-4：重试队列可见化
    owner.btn_retry_queue = QPushButton(tr("重试队列"))
    owner.btn_retry_queue.setObjectName("ghost")  # Rev11-C R-03: 工具区权重
    owner.btn_retry_queue.setToolTip(
        tr("查看自动重试队列：失败次数、下次重试时间、原因与处理建议"))
    owner.btn_retry_queue.clicked.connect(owner._open_retry_dialog)
    row2.addWidget(owner.btn_retry_queue)
    # G71-6：一键导出诊断包
    owner.btn_diag_bundle = QPushButton(tr("诊断包"))
    owner.btn_diag_bundle.setObjectName("ghost")  # Rev11-C R-03: 工具区权重
    owner.btn_diag_bundle.setToolTip(
        tr("导出 zip 诊断包：状态报告 + 最近事件 + 日志尾部（全部已打码）"))
    owner.btn_diag_bundle.clicked.connect(owner._export_diag_bundle)
    row2.addWidget(owner.btn_diag_bundle)
    row2.addStretch(1)
    v.addLayout(row2)
    # Rev13 R13-02：**兜底折叠**。矮窗口（900×480 / 1366×768@150% 的工作区 911×512）
    # 放不下这第二行 —— 实测工具行被裁到 27/42px。此时把四个工具按钮折进一个
    # 「更多 ▸」按钮的菜单里：入口仍在（点开就能用），但不再占一整行高度。
    # 正常高度下这行保持原样展开（gui_helpers.set_tools_row_folded 控制）。
    v.addWidget(build_more_tools_button(owner))
    # v9.12.1：动作区**绝不允许被压扁**。清单行搬进来后总共三行，在声明的最小窗口
    # 1020×560 下 Qt 会按比例压缩这个容器，按钮被切成 31/42px（看起来像被裁）。
    # 这里把纵向策略钉成 Fixed 并给出最小高度，让**可滚动**的顶部区去吸收压缩。
    wrap.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
    wrap.setMinimumHeight(wrap.sizeHint().height())
    # Rev13 R13-02：给折叠态留最小高度基线（展开态 = sizeHint，折叠态 = 两行）
    owner._actions_hint_expanded = int(wrap.sizeHint().height())
    owner._actions_hint_folded = int(wrap.minimumSizeHint().height() - 44)
    return wrap


def set_tools_row_folded(owner, folded: bool) -> None:
    """Rev13 R13-02：把工具行（打开目录/失败汇总/重试队列/诊断包）折叠成「更多 ▸」。

    **为什么需要**：矮窗口下这一行放不下 —— 实测 900×480 与 1366×768@150% 的工作区
    911×512 下，`btn_open` 等四个按钮可见高度只有 27/42px（下半截被父级裁掉）。
    折叠后入口仍在（菜单里同名同槽），但不再占一整行高度。

    **不要简单删掉这几个按钮**：它们是失败排查链路唯一的 UI 入口
    （`test_g69_regressions.TestG69_18WiringClosure` 会抓孤儿）。
    折叠只是换了个挂载点，槽与文案必须逐项保持一致。
    """
    more = getattr(owner, "btn_more_tools", None)
    btns = [getattr(owner, n, None)
            for n in ("btn_open", "btn_failures", "btn_retry_queue", "btn_diag_bundle")]
    if more is None or any(b is None for b in btns):
        return
    if folded:
        if more.menu() is None:
            menu = QMenu(more)
            # 菜单项直接**复用按钮的文本与图标**，再绑定同一个槽 ——
            # 文案/行为只有按钮这一处事实源，不存在第二份定义。
            for btn in btns:
                act = QAction(btn.icon(), btn.text(), menu)
                act.triggered.connect(btn.click)
                menu.addAction(act)
            more.setMenu(menu)
        for b in btns:
            b.hide()
        more.show()
    else:
        more.hide()
        for b in btns:
            b.show()
    act = getattr(owner, "download_actions", None)
    if act is not None:
        hint = getattr(owner, "_actions_hint_folded", 0) if folded \
            else getattr(owner, "_actions_hint_expanded", 0)
        if hint:
            act.setMinimumHeight(int(hint))
