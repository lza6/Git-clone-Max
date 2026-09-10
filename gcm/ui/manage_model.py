# -*- coding: utf-8 -*-
"""仓库管理表模型：数据与视图解耦，支持大批量行的高性能展示与过滤。"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import List, Optional

from PyQt6.QtCore import QAbstractItemModel, QEvent, QModelIndex, QRect, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionButton,
)


@dataclass
class ManageRow:
    """管理页单行数据。"""
    folder_name: str
    owner: str
    repo: str
    local_path: str
    last_sync_at: str
    head_sha: str
    host: str = "github.com"
    url: str = ""
    repo_id: int = 0
    default_branch: str = ""
    # G03 扩展列（v5.2 DB 迁移后模型同步）
    tags: str = ""
    favorite: int = 0
    excluded: int = 0


class ManageModel(QAbstractItemModel):
    """内存行 + 懒加载渲染：支持数万行而不卡。

    数据行预取一次存内存，视图只请求可见区域，避免 setCellWidget 逐行开销。
    """

    rowsChanged = pyqtSignal()
    visChanged = pyqtSignal()

    def __init__(self, rows: Optional[List[dict]] = None, parent=None):
        super().__init__(parent)
        self._rows: List[ManageRow] = []
        self._vis: List[bool] = []
        if rows:
            self.set_rows(rows)
        else:
            self._sync_vis()

    # ------------------------------------------------------------ 数据接入
    def set_rows(self, rows: List[dict]):
        """从 db.list_repos() 的行列表重建。"""
        self.beginResetModel()
        self._rows = []
        for r in rows:
            self._rows.append(ManageRow(
                folder_name=str(r.get("folder_name") or ""),
                owner=str(r.get("owner") or ""),
                repo=str(r.get("repo") or ""),
                local_path=str(r.get("local_path") or ""),
                last_sync_at=str(r.get("last_sync_at") or "")[:19],
                head_sha=str(r.get("head_sha") or "")[:8],
                host=str(r.get("host") or "github.com"),
                url=str(r.get("url") or ""),
                repo_id=int(r.get("id") or 0),
                default_branch=str(r.get("default_branch") or ""),
                tags=str(r.get("tags") or ""),
                favorite=int(r.get("favorite") or 0),
                excluded=int(r.get("excluded") or 0),
            ))
        self.endResetModel()
        self.rowsChanged.emit()
        self._sync_vis()

    def _sync_vis(self):
        self._vis = [True] * len(self._rows)

    def row_at(self, row: int) -> Optional[ManageRow]:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def is_stale(self, row: int, days: int = 30) -> bool:
        """该行是否「超过 days 天未同步」（G03-6 过期高亮）。"""
        r = self.row_at(row)
        if r is None or not r.last_sync_at:
            return False
        try:
            last = datetime.datetime.strptime(r.last_sync_at[:19], "%Y-%m-%d %H:%M:%S")
            return (datetime.datetime.now() - last).days > days
        except Exception:
            return False

    def filter_rows(self, query: str) -> int:
        """按仓库名/owner/repo/host/tags 过滤行；返回可见行数（G03-1）。"""
        q = (query or "").strip().lower()
        visible = 0
        for r in range(len(self._rows)):
            row = self._rows[r]
            hay = " ".join([row.folder_name, row.owner, row.repo,
                            row.host, row.tags]).lower()
            if q and q not in hay:
                self._vis[r] = False
            else:
                self._vis[r] = True
                visible += 1
        self.visChanged.emit()
        return visible

    def remove_rows_at(self, indexes: List[int]) -> int:
        """删除指定行（倒序），返回删除数量。"""
        idxs = sorted(set(indexes), reverse=True)
        for i in idxs:
            if 0 <= i < len(self._rows):
                self.beginRemoveRows(QModelIndex(), i, i)
                del self._rows[i]
                self.endRemoveRows()
        return len(idxs)

    def __len__(self) -> int:
        return len(self._rows)

    # ------------------------------------------------------------ Qt 模型接口
    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if parent.isValid() or not (0 <= row < len(self._rows)) or not (0 <= column < 6):
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, child: QModelIndex = QModelIndex()) -> QModelIndex:
        return QModelIndex()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 6

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        r = self._rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            return (r.folder_name if col == 0 else
                    (f"{r.owner}/{r.repo}" if col == 1 else
                     (r.local_path if col == 2 else
                      (r.last_sync_at if col == 3 else
                       (r.head_sha if col == 4 else "")))))
        if role == Qt.ItemDataRole.ToolTipRole and col == 0:
            return r.url or r.local_path
        return None


class HistoryButtonDelegate(QStyledItemDelegate):
    """G03-7 末列「查看」按钮委托：每行独立可点击（替代共享按钮）。

    用 QStyle 绘制原生按钮外观，sizeHint 给足按钮尺寸，editorEvent 处理点击。
    """

    clicked = pyqtSignal(int)  # row

    def button_rect(self, rect: QRect) -> QRect:
        """在单元格内居中放置一个合适宽高的按钮。"""
        w = min(56, max(30, rect.width() - 12))
        h = min(24, max(16, rect.height() - 8))
        return QRect(
            rect.x() + (rect.width() - w) // 2,
            rect.y() + (rect.height() - h) // 2,
            w, h,
        )

    def _option(self, option, rect: QRect) -> QStyleOptionButton:
        btn = QStyleOptionButton()
        btn.rect = rect
        btn.text = "查看"
        btn.state = QStyle.StateFlag.State_Enabled
        return btn

    def paint(self, painter, option, index):
        if index.column() != 5:
            super().paint(painter, option, index)
            return
        rect = self.button_rect(option.rect)
        btn = self._option(option, rect)
        style = option.widget.style() if option.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_PushButton, btn, painter)

    def sizeHint(self, option, index):
        if index.column() == 5:
            btn = self._option(option, QRect(0, 0, 70, 30))
            style = QApplication.style()
            return style.sizeFromContents(
                QStyle.ContentsType.CT_PushButton, btn, option.rect.size())
        return super().sizeHint(option, index)

    def editorEvent(self, event, model, option, index):
        if index.column() != 5:
            return False
        rect = self.button_rect(option.rect)
        if event.type() in (QEvent.Type.MouseButtonRelease,
                            QEvent.Type.MouseButtonPress):
            pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            if rect.contains(pos):
                if event.type() == QEvent.Type.MouseButtonRelease:
                    self.clicked.emit(index.row())
                return True
        return False
