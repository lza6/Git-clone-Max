"""失败原因聚合面板（G52-5）。

按错误类别（网络 / 认证 / 平台限制 / 本地）聚合失败次数 + 建议动作，
可点击重试该类别。数据源为 data/progress.json 的 failed 记录（与进度一致）。
错误分类复用 gcm/git/service.py 的 _is_networkish_error（网络类）+ 关键字映射。
"""
from __future__ import annotations

import weakref
from functools import partial
from typing import Any, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..app.errors import (
    CAT_AUTH,
    CAT_CANCELLED,
    CAT_LOCAL,
    CAT_NETWORK,
    CAT_NOT_FOUND,
    CAT_OTHER,
    CAT_PLATFORM,
    ErrorInfo,
    category_label,
    classify_error,
)
from ..i18n import tr

# 错误类别常量：G71-3 起统一由 app/errors.py 定义，这里原样再导出，
# 保持既有调用方（main_window / 测试）无需改动。
__all__ = [
    "CAT_NETWORK", "CAT_AUTH", "CAT_PLATFORM", "CAT_LOCAL", "CAT_OTHER",
    "CAT_NOT_FOUND", "CAT_CANCELLED", "categorize_failure",
    "failure_category_label", "failure_category_suggestion", "failure_info",
    "aggregate_failures", "FailurePanel", "FailureDialog",
]

_CATEGORY_SUGGESTIONS = {
    CAT_NETWORK: "检查网络/代理后重试，或开启「失败重试」自动重投",
    CAT_AUTH: "检查并更新 Token / 按平台凭据，再重试该类别",
    CAT_PLATFORM: "仓库含 Windows 限制内容，建议换 WSL/Linux 或跳过",
    CAT_LOCAL: "检查磁盘空间 / 目录占用 / 文件权限后重试",
    CAT_NOT_FOUND: "核对仓库地址；仓库可能已删除/改名/转为私有，重试无效",
    CAT_CANCELLED: "任务被取消；需要时重新开始即可",
    CAT_OTHER: "查看日志详情后手动处理",
}


def categorize_failure(message: str, detail: str = "") -> str:
    """把失败文本映射到错误类别 key。

    G71-3：分类逻辑已收敛到 `app/errors.classify_error`（唯一判据），
    本函数只做兼容转发 —— 避免「重试队列认为可重试、UI 却显示另一类」。
    """
    return classify_error(message, detail).category


def failure_info(message: str, detail: str = "") -> ErrorInfo:
    """取三段式结构化错误（UI/诊断包共用）。"""
    return classify_error(message, detail)


def failure_category_label(category: str) -> str:
    return category_label(category)


def failure_category_suggestion(category: str) -> str:
    return _CATEGORY_SUGGESTIONS.get(category, _CATEGORY_SUGGESTIONS[CAT_OTHER])


def aggregate_failures(failed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按类别聚合失败记录：返回 [{category,label,count,suggestion,keys,messages,
    title,cause,action,retryable}]。

    failed_rows：progress.json 的 failed 列表（[{key,message,time},...]）。
    结果按次数降序；空输入 → 空列表。
    """
    groups: dict[str, dict[str, Any]] = {}
    for row in failed_rows or []:
        if not isinstance(row, dict) or not row.get("key"):
            continue
        info = classify_error(str(row.get("message") or ""),
                              str(row.get("detail") or ""))
        cat = info.category
        g = groups.setdefault(cat, {
            "category": cat,
            "label": category_label(cat),
            "count": 0,
            "suggestion": info.action or failure_category_suggestion(cat),
            "title": info.title,
            "cause": info.cause,
            "action": info.action,
            "retryable": info.retryable,
            "keys": [],
            "messages": [],
        })
        g["count"] += 1
        if row["key"] not in g["keys"]:
            g["keys"].append(row["key"])
        g["messages"].append(str(row.get("message") or ""))
    out = list(groups.values())
    out.sort(key=lambda g: (-g["count"], g["label"]))
    return out


class FailurePanel(QWidget):
    """失败聚合面板：表格 + 空态占位 + 按类别给出**可点击的下一步**。"""

    retry_category = pyqtSignal(str)        # category key
    #: C2-4（Rev7 B2-3）：跳到设置页某个分组（值见 `_SECTION_BY_ACTION`）
    open_settings_requested = pyqtSignal(str)
    #: C2-4：导出诊断包（"分类器不认识这类错误，先把现场带走"）
    diagnostics_requested = pyqtSignal()

    def __init__(self, owner, parent: Optional[QWidget] = None):
        super().__init__(parent)
        # owner 允许为 None（测试会构造无宿主的面板单独验证数据逻辑）
        self._owner_ref = (weakref.ref(owner) if owner is not None
                           else (lambda: None))
        self._build_ui()

    @property
    def owner(self):
        """G69-18：**弱引用**持有宿主，避免 Panel↔MainWindow 引用环。

        原实现 `self.owner = owner` 是强引用：MainWindow 持有 Panel、Panel 又持有
        MainWindow → 环 → 只能靠循环 GC 回收 → 回收时机不可控（可能在 Qt 分发
        事件中途）→ 原生崩溃。弱引用后窗口由引用计数立即释放。
        """
        return self._owner_ref()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setSpacing(8)
        # C1-3（Rev7 §3.4 X-3）：卡死任务条。引擎识别出「远超超时不结束」的任务后
        # 只发 `task_stuck` 事件 + 写日志，而 `cancel_stuck_tasks()` 有实现、有单测，
        # 却**零 UI 入口** —— 用户看到「疑似卡死」也做不了任何事。
        # 这一行就是那个入口：默认隐藏（无卡死任务时不占位）。
        self.stuck_row = QWidget()
        _sr = QHBoxLayout(self.stuck_row)
        _sr.setContentsMargins(0, 0, 0, 0)
        self.stuck_lbl = QLabel("")
        self.stuck_lbl.setWordWrap(True)
        self.btn_cancel_stuck = QPushButton(tr("取消这些卡死任务"))
        self.btn_cancel_stuck.setToolTip(tr(
            "把这些任务的取消标志置位，让它们在下一个检查点尽快收敛。\n"
            "不会超发并发（槽位仍等 worker 真正退出才释放）。"))
        self.btn_cancel_stuck.clicked.connect(self._on_cancel_stuck)
        _sr.addWidget(self.stuck_lbl, 1)
        _sr.addWidget(self.btn_cancel_stuck)
        self.stuck_row.setVisible(False)
        v.addWidget(self.stuck_row)
        self.empty_lbl = QLabel(tr("暂无失败记录"))
        self.empty_lbl.setObjectName("muted")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.empty_lbl)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [tr("错误类别"), tr("失败次数"), tr("原因与处理"), tr("操作")])
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        # G75：操作列里是真实按钮，必须按内容自适应，否则文字被省略成「重试该类」
        self.table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # G74：三段式原因/建议需要换行，否则在 560px 对话框里被省略成
        # “网络连接失败或超时...”，用户看不到“怎么办”。
        self.table.setWordWrap(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.ResizeToContents)
        v.addWidget(self.table, 1)

    def refresh(self, failed_rows: Optional[list[dict[str, Any]]] = None) -> int:
        """从 progress 失败记录刷新聚合；返回类别行数。空态显示占位。"""
        self._refresh_stuck()
        if failed_rows is None:
            failed_rows = self._load_failed_rows()
        rows = aggregate_failures(failed_rows)
        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for i, g in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(tr(g["label"])))
            self.table.setItem(i, 1, QTableWidgetItem(str(g["count"])))
            # G71-3：三段式（发生了什么 / 为什么 / 怎么办）；不可自动重试时明示
            lines = [
                tr(str(g.get("title") or g["label"])),
                tr("为什么：") + tr(str(g.get("cause") or "")),
                tr("怎么办：") + tr(str(g.get("action") or g.get("suggestion") or "")),
            ]
            if not g.get("retryable", False):
                lines.append(tr("（这类错误不会自动重试）"))
            it_s = QTableWidgetItem("\n".join(x for x in lines if x.strip()))
            it_s.setToolTip("\n".join(g["messages"][:5]))
            self.table.setItem(i, 2, it_s)
            self.table.setCellWidget(
                i, 3, self._action_widget(g["category"]))
        self.table.setSortingEnabled(was_sorting)
        self.table.resizeRowsToContents()
        self.empty_lbl.setVisible(len(rows) == 0)
        self.table.setVisible(len(rows) > 0)
        return len(rows)

    #: action_kind → (按钮文案, 跳到哪个设置分组)。分组键由 MainWindow 解释。
    _SECTION_BY_ACTION = {"network": "network", "auth": "network",
                          "switch_mirror": "network", "local": "network"}

    def _action_widget(self, category: str):
        """按 `errors.action_kind_for(category)` 渲染该行的「下一步」控件。

        C2-4 / Rev7 B2-3：原来这里只有一个「重试该类别」按钮 —— 对
        **认证失败 / 仓库已删除 / 磁盘满** 这三类，重试都是**无效动作**
        （`retryable=False`，点了只是让失败再发生一遍）。现在按类别给真正的下一步：
        重试 / 去设置改 Token / 导出诊断包 / 无动作（显示 —）。
        """
        try:
            from ..app.errors import action_kind_for
            kind = action_kind_for(category)
        except Exception:      # noqa: BLE001 —— 分类器异常退化为"重试"（不比原来更差）
            kind = "retry"
        if kind == "retry":
            btn = QPushButton(tr("重试该类别"))
            btn.setToolTip(tr("对这一类错误重新入队（仅网络类有效）"))
            btn.clicked.connect(partial(self._emit_retry, category))
            return btn
        if kind in ("open_settings", "switch_mirror"):
            btn = QPushButton(tr("去设置"))
            btn.setToolTip(tr("打开设置页的对应分组（认证类要去更新 Token/凭据）"))
            btn.clicked.connect(
                partial(self.open_settings_requested.emit,
                        self._SECTION_BY_ACTION.get(kind, "network")))
            return btn
        if kind == "copy_diag":
            btn = QPushButton(tr("诊断包"))
            btn.setToolTip(tr("导出含事件流与日志尾部的诊断包（已打码），便于定位"))
            btn.clicked.connect(partial(self.diagnostics_requested.emit))
            return btn
        # kind == "none"：本地确实没有可做的动作，别放一个点了没用的按钮
        lbl = QLabel("—")      # 占位符，无文案含义，刻意不走 tr()
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl.setToolTip(tr("这类错误在本机没有可执行动作（例如仓库已删除）"))
        return lbl

    def _emit_retry(self, category: str) -> None:
        self.retry_category.emit(category)

    def _refresh_stuck(self) -> list[str]:
        """刷新「疑似卡死任务」条：有则显示并可一键取消，无则整行隐藏。

        数据源是**引擎内存**（`stuck_task_keys()`）而不是 progress.json —— 卡死是
        「进程内还在跑但不结束」的实时状态，落盘就失去意义。引擎不存在
        （面板可独立构造、或批次已结束）时自然显示为空。
        """
        keys: list[str] = []
        owner = self.owner
        engine = getattr(owner, "engine", None) if owner is not None else None
        if engine is not None and hasattr(engine, "stuck_task_keys"):
            try:
                keys = list(engine.stuck_task_keys())
            except Exception as e:  # noqa: BLE001 —— 读失败不该拖垮面板，但必须留痕
                from ..app.applog import warn as _warn
                _warn(f"读取疑似卡死任务失败（面板按无处理）：{e}")
                keys = []
        self._stuck_keys = keys
        if keys:
            shown = "、".join(keys[:3]) + (f" 等 {len(keys)} 个" if len(keys) > 3 else "")
            self.stuck_lbl.setText(
                tr("发现 {n} 个疑似卡死任务（超过超时阈值仍未结束）：{items}")
                .format(n=len(keys), items=shown))
            self.stuck_row.setVisible(True)
        else:
            self.stuck_lbl.setText("")
            self.stuck_row.setVisible(False)
        return keys

    def _on_cancel_stuck(self) -> int:
        """把已上报的卡死任务的取消标志置位（不超发并发，见引擎文档）。"""
        owner = self.owner
        engine = getattr(owner, "engine", None) if owner is not None else None
        if engine is None or not hasattr(engine, "cancel_stuck_tasks"):
            return 0
        try:
            n = int(engine.cancel_stuck_tasks())
        except Exception as e:  # noqa: BLE001 —— 单个取消失败只影响该次点击
            n = 0
            from ..app.applog import warn as _warn
            _warn(f"取消卡死任务失败：{e}")
        from ..app.applog import info as _info
        _info(f"用户从失败面板请求取消 {n} 个疑似卡死任务")
        self.refresh()
        return n

    def _load_failed_rows(self) -> list[dict[str, Any]]:
        try:
            from ..db.repo_db import load_progress
            data = load_progress(getattr(self.owner, "progress_path", None))
            return list(data.get("failed") or [])
        except Exception:
            return []


class FailureDialog(QDialog):
    """失败聚合对话框（入口：主窗口「失败汇总」按钮）。"""

    retry_category = pyqtSignal(str)
    open_settings_requested = pyqtSignal(str)
    diagnostics_requested = pyqtSignal()

    def __init__(self, owner, parent: Optional[QWidget] = None):
        super().__init__(parent)
        from .theme import apply_parent_theme
        apply_parent_theme(self)
        self.setWindowTitle(tr("失败原因汇总"))
        from .layout import fit_dialog_to_screen
        fit_dialog_to_screen(self, 780, 600, 540, 380)
        self.panel = FailurePanel(owner, self)
        self.panel.retry_category.connect(self.retry_category)
        self.panel.open_settings_requested.connect(self.open_settings_requested)
        self.panel.diagnostics_requested.connect(self.diagnostics_requested)
        lay = QVBoxLayout(self)
        lay.addWidget(self.panel)
        self.btn_close = QPushButton(tr("关闭"))
        self.btn_close.clicked.connect(self.accept)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.btn_close)
        lay.addLayout(row)

    def refresh(self) -> int:
        return self.panel.refresh()
