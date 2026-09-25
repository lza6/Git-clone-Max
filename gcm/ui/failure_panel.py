"""失败原因聚合面板（G52-5）。

按错误类别（网络 / 认证 / 平台限制 / 本地）聚合失败次数 + 建议动作，
可点击重试该类别。数据源为 data/progress.json 的 failed 记录（与进度一致）。
错误分类复用 gcm/git/service.py 的 _is_networkish_error（网络类）+ 关键字映射。
"""
from __future__ import annotations

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

from ..git.service import _is_networkish_error
from ..i18n import tr

# 错误类别常量
CAT_NETWORK = "network"
CAT_AUTH = "auth"
CAT_PLATFORM = "platform"
CAT_LOCAL = "local"
CAT_OTHER = "other"

_CATEGORY_LABELS = {
    CAT_NETWORK: "网络",
    CAT_AUTH: "认证",
    CAT_PLATFORM: "平台限制",
    CAT_LOCAL: "本地",
    CAT_OTHER: "其他",
}

_CATEGORY_SUGGESTIONS = {
    CAT_NETWORK: "检查网络/代理后重试，或开启「失败重试」自动重投",
    CAT_AUTH: "检查并更新 Token / 按平台凭据，再重试该类别",
    CAT_PLATFORM: "仓库含 Windows 限制内容，建议换 WSL/Linux 或跳过",
    CAT_LOCAL: "检查磁盘空间 / 目录占用 / 文件权限后重试",
    CAT_OTHER: "查看日志详情后手动处理",
}

_AUTH_KEYWORDS = (
    "authentication failed", "invalid username or password",
    "could not read username", "access denied", "permission denied",
    "not authorized", "401", "403",
)
_PLATFORM_KEYWORDS = (
    "invalid path", "file exists", "already exists",
    "unable to checkout", "unable to create file",
)
_LOCAL_KEYWORDS = (
    "no space left on device", "cannot create directory",
    "cannot mkdir", "read-only file system", "disk full",
    "file too large", "cannot create work tree",
)


def categorize_failure(message: str, detail: str = "") -> str:
    """把失败文本映射到错误类别 key（网络/认证/平台限制/本地/其他）。

    优先级：平台限制 > 认证 > 本地 > 网络（_is_networkish_error 判网络）。
    """
    text = f"{message or ''}\n{detail or ''}".lower()
    if any(k in text for k in _PLATFORM_KEYWORDS):
        return CAT_PLATFORM
    if any(k in text for k in _AUTH_KEYWORDS):
        return CAT_AUTH
    if any(k in text for k in _LOCAL_KEYWORDS):
        return CAT_LOCAL
    if _is_networkish_error(text):
        return CAT_NETWORK
    return CAT_OTHER


def failure_category_label(category: str) -> str:
    return _CATEGORY_LABELS.get(category, "其他")


def failure_category_suggestion(category: str) -> str:
    return _CATEGORY_SUGGESTIONS.get(category, _CATEGORY_SUGGESTIONS[CAT_OTHER])


def aggregate_failures(failed_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """按类别聚合失败记录：返回 [{category, label, count, suggestion, keys, messages}]。

    failed_rows：progress.json 的 failed 列表（[{key,message,time},...]）。
    结果按次数降序；空输入 → 空列表。
    """
    groups: dict[str, dict[str, Any]] = {}
    for row in failed_rows or []:
        if not isinstance(row, dict) or not row.get("key"):
            continue
        cat = categorize_failure(str(row.get("message") or ""), "")
        g = groups.setdefault(cat, {
            "category": cat,
            "label": failure_category_label(cat),
            "count": 0,
            "suggestion": failure_category_suggestion(cat),
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
    """失败聚合面板：表格 + 空态占位 + 按类别重试。"""

    retry_category = pyqtSignal(str)   # category key

    def __init__(self, owner, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.owner = owner
        self._build_ui()

    def _build_ui(self):
        v = QVBoxLayout(self)
        v.setSpacing(8)
        self.empty_lbl = QLabel(tr("暂无失败记录 🎉"))
        self.empty_lbl.setObjectName("muted")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self.empty_lbl)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [tr("错误类别"), tr("失败次数"), tr("建议动作"), tr("操作")])
        self.table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        v.addWidget(self.table, 1)

    def refresh(self, failed_rows: Optional[list[dict[str, Any]]] = None) -> int:
        """从 progress 失败记录刷新聚合；返回类别行数。空态显示占位。"""
        if failed_rows is None:
            failed_rows = self._load_failed_rows()
        rows = aggregate_failures(failed_rows)
        was_sorting = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        self.table.setRowCount(len(rows))
        for i, g in enumerate(rows):
            self.table.setItem(i, 0, QTableWidgetItem(g["label"]))
            self.table.setItem(i, 1, QTableWidgetItem(str(g["count"])))
            it_s = QTableWidgetItem(g["suggestion"])
            it_s.setToolTip("\n".join(g["messages"][:5]))
            self.table.setItem(i, 2, it_s)
            btn = QPushButton(tr("重试该类别"))
            btn.clicked.connect(
                lambda _=False, c=g["category"]: self.retry_category.emit(c))
            self.table.setCellWidget(i, 3, btn)
        self.table.setSortingEnabled(was_sorting)
        self.empty_lbl.setVisible(len(rows) == 0)
        self.table.setVisible(len(rows) > 0)
        return len(rows)

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

    def __init__(self, owner, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle(tr("失败原因汇总"))
        self.resize(560, 360)
        self.panel = FailurePanel(owner, self)
        self.panel.retry_category.connect(self.retry_category)
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
