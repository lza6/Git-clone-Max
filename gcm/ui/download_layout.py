"""G71-7 下载中心顶部区域构建（从 `_build_download_tab` 抽出）。

原 `_build_download_tab` 191 行，其中 160 行是**纯控件搭建**（输入框/实时校验/
快捷填充/任务清单/Star 导入/下载位置/克隆模式/操作按钮）。抽出来后：
- `main_window._build_download_tab` 只负责布局拼装（滚动区 + 分隔器 + 进度表）；
- 本模块可独立被 UI 测试构造（仍把所有控件挂回 owner，外部引用不变）。

**行为零变化**：控件名、信号连接、tooltip、无障碍名、拖拽弱引用包装逐项照抄。
"""
from __future__ import annotations

import weakref

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr


def build_download_top(owner) -> QWidget:
    """构建下载中心上半区并返回容器；所有控件挂到 `owner` 上（兼容既有引用）。"""
    box = QGroupBox(tr("仓库地址（每行一个）"))
    b = QVBoxLayout(box)
    owner.repo_input = QPlainTextEdit()
    owner.repo_input.setPlaceholderText(
        tr("每行一个仓库地址，例如：\n")
        + "https://github.com/vercel-labs/skills\n"
        + "git@github.com:microsoft/azure-skills.git\n"
        + "vercel-labs/agent-skills\n"
        + tr("https://gitlab.com/grp/repo@v1.2.0  （@后指定分支/标签）\n")
        + tr("（地址中的 作者/仓库名 将自动作为文件夹名：作者__仓库）")
    )
    owner.repo_input.setMinimumHeight(120)
    owner.repo_input.setAccessibleName(tr("仓库地址输入框"))
    b.addWidget(owner.repo_input)
    # G54-1 实时校验条：输入/粘贴后显示「有效 N / 无效 M」
    owner.repo_valid_lbl = QLabel("")
    owner.repo_valid_lbl.setWordWrap(True)
    owner.repo_valid_lbl.setTextFormat(Qt.TextFormat.PlainText)
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

    quick = QHBoxLayout()
    quick.addWidget(QLabel(tr("快捷填充：")))
    owner._quick_fill_buttons = []
    for repo in getattr(owner.settings, "quick_repos", None) or ():
        btn = QPushButton(repo)
        btn.setProperty("quick_repo", repo)
        btn.clicked.connect(owner._on_quick_fill)
        quick.addWidget(btn)
        owner._quick_fill_buttons.append(btn)
    quick.addStretch()
    # G35-4 任务清单
    owner.btn_save_list = QPushButton(tr("💾 保存清单"))
    owner.btn_save_list.setToolTip(
        tr("把当前输入区的地址保存为命名任务清单（data/lists/*.json）"))
    owner.btn_save_list.clicked.connect(owner._save_task_list)
    owner.combo_load_list = QComboBox()
    owner.combo_load_list.setToolTip(tr("选择已保存的清单一键载入"))
    owner.combo_load_list.setMinimumWidth(180)
    owner.btn_load_list = QPushButton(tr("载入"))
    owner.btn_load_list.setToolTip(tr("把所选清单的地址填入输入区"))
    owner.btn_load_list.clicked.connect(owner._load_task_list)
    quick.addWidget(owner.btn_save_list)
    quick.addWidget(owner.combo_load_list)
    quick.addWidget(owner.btn_load_list)
    # G35-10 GitHub Star 列表导入
    owner.btn_star_import = QPushButton(tr("⭐ 导入 Star"))
    owner.btn_star_import.setToolTip(
        tr("按 GitHub 用户名拉取 Star 列表并填入输入区（需要时在设置页填写令牌）"))
    owner.btn_star_import.clicked.connect(owner.import_starred)
    quick.addWidget(owner.btn_star_import)
    b.addLayout(quick)

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

    btns = QHBoxLayout()
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
    owner.btn_open = QPushButton(tr("打开克隆目录"))
    owner.btn_open.clicked.connect(owner.open_target)
    owner.btn_open.setIcon(owner._std_icon("open_dir", owner.btn_open))
    btns.addWidget(owner.btn_start, 2)
    btns.addWidget(owner.btn_cancel)
    btns.addWidget(owner.btn_clear)
    btns.addWidget(owner.btn_open)
    # G52-5：失败原因聚合面板入口
    owner.btn_failures = QPushButton(tr("失败汇总"))
    owner.btn_failures.clicked.connect(owner._open_failure_dialog)
    btns.addWidget(owner.btn_failures)
    # G71-4：重试队列可见化
    owner.btn_retry_queue = QPushButton(tr("重试队列"))
    owner.btn_retry_queue.setToolTip(
        tr("查看自动重试队列：失败次数、下次重试时间、原因与处理建议"))
    owner.btn_retry_queue.clicked.connect(owner._open_retry_dialog)
    btns.addWidget(owner.btn_retry_queue)
    # G71-6：一键导出诊断包
    owner.btn_diag_bundle = QPushButton(tr("诊断包"))
    owner.btn_diag_bundle.setToolTip(
        tr("导出 zip 诊断包：状态报告 + 最近事件 + 日志尾部（全部已打码）"))
    owner.btn_diag_bundle.clicked.connect(owner._export_diag_bundle)
    btns.addWidget(owner.btn_diag_bundle)
    btns.addStretch()
    tv.addLayout(btns)
    return top
