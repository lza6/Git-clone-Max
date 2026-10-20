"""仓库管理表模型：数据与视图解耦，支持大批量行的高性能展示与过滤。"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Optional

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
    # G47-1/5 数据洞察：健康度(0-5) 与磁盘大小（懒加载）
    health: int = 3
    size: str = ""


class ManageModel(QAbstractItemModel):
    """内存行 + 懒加载渲染：支持数万行而不卡。

    数据行预取一次存内存，视图只请求可见区域，避免 setCellWidget 逐行开销。
    """

    rowsChanged = pyqtSignal()
    visChanged = pyqtSignal()

    def __init__(self, rows: Optional[list[dict]] = None, parent=None, db=None):
        super().__init__(parent)
        self._rows: list[ManageRow] = []
        self._vis: list[bool] = []
        self.db = db  # G47-1 健康度懒加载数据源
        self._health_cache: dict[int, int] = {}
        # 过期判定缓存：`data()` 每格每角色都会问一次，避免重复解析时间字符串。
        self._stale_cache: dict[tuple[int, int], bool] = {}
        # G03-6 过期高亮阈值：由管理面板按设置覆盖，默认 30 天。
        self.stale_days: int = 30
        if rows:
            self.set_rows(rows)
        else:
            self._sync_vis()

    # ------------------------------------------------------------ 数据接入
    def set_rows(self, rows: list[dict]):
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
        self._health_cache = {}
        self.rowsChanged.emit()
        self._sync_vis()

    def _sync_vis(self):
        self._vis = [True] * len(self._rows)
        self._stale_cache = {}

    def row_at(self, row: int) -> Optional[ManageRow]:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    # ------------------------------------------------------------ host 分布统计（G35-6）
    def host_counts(self) -> dict[str, int]:
        """按 host 对内存行分组计数；空 host 归一为 `local`（G35-6）。

        纯内存聚合、不新增 SQL；返回的 dict 键按计数降序排列，保证
        host_summary 输出稳定可排序。
        """
        counts: dict[str, int] = {}
        for row in self._rows:
            host = row.host or "local"
            counts[host] = counts.get(host, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))

    def host_summary(self) -> str:
        """将 host 分布拼成单行统计文案，如 `github.com: 2 · gitlab.com: 1`。

        无仓库时返回空串；主控将其拼入 manage_stats 文案。
        """
        return " · ".join(f"{host}: {n}" for host, n in self.host_counts().items())

    def is_stale(self, row: int, days: Optional[int] = None) -> bool:
        """该行是否「超过 days 天未同步」（G03-6 过期高亮）。

        `days` 省略时用 `self.stale_days`（由管理面板从设置写入，默认 30）。
        """
        limit = self.stale_days if days is None else days
        r = self.row_at(row)
        if r is None or not r.last_sync_at:
            return False
        key = (row, int(limit))
        cached = self._stale_cache.get(key)
        if cached is not None:
            return cached
        try:
            last = datetime.datetime.strptime(r.last_sync_at[:19], "%Y-%m-%d %H:%M:%S")
            result = (datetime.datetime.now() - last).days > limit
        except Exception:
            result = False
        self._stale_cache[key] = result
        return result

    def is_visible(self, row: int) -> bool:
        """该行是否通过当前过滤条件（G03-1；由视图 `setRowHidden` 消费）。"""
        return bool(0 <= row < len(self._vis) and self._vis[row])

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

    def remove_rows_at(self, indexes: list[int]) -> int:
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
        if parent.isValid() or not (0 <= row < len(self._rows)) or not (0 <= column < 8):
            return QModelIndex()
        return self.createIndex(row, column)

    def parent(self, child: QModelIndex = QModelIndex()) -> QModelIndex:
        return QModelIndex()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 8

    _HEADERS = ("仓库", "作者/仓库", "本地路径", "上次同步", "HEAD", "历史", "健康", "大小")

    def headerData(self, section, orientation=Qt.Orientation.Horizontal,
                   role=Qt.ItemDataRole.DisplayRole):
        if (orientation == Qt.Orientation.Horizontal
                and role == Qt.ItemDataRole.DisplayRole
                and 0 <= section < len(self._HEADERS)):
            return self._HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        r = self._rows[index.row()]
        col = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if col == 6:
                # G74：去掉 ⭐（Microsoft YaHei 无此字形会显示 tofu），
                # 用 "3/5" 更直接，也保留列语义。
                return f"{self.health_value(index.row())}/5"
            if col == 7:
                return r.size or ""
            return (r.folder_name if col == 0 else
                    (f"{r.owner}/{r.repo}" if col == 1 else
                     (r.local_path if col == 2 else
                      (r.last_sync_at if col == 3 else
                       (r.head_sha if col == 4 else "")))))
        if role == Qt.ItemDataRole.ToolTipRole and col in (0, 2, 3, 4):
            if col == 2:
                return r.local_path or ""
            if col == 3:
                return r.last_sync_at or ""
            if col == 4:
                return r.head_sha or ""
            tip = r.url or r.local_path
            # G70：历史库行可能残留 URL 内嵌凭据（写库剥凭据是 G69-11 才加的），
            # 展示前再剥一次，避免 tooltip 把旧凭据带给旁人。
            try:
                from ..app.url_lib import strip_embedded_credentials
                tip = strip_embedded_credentials(tip or "")[0]
            except Exception:
                pass
            if self.is_stale(index.row()):
                tip = f"{tip}\n超过 {self.stale_days} 天未同步"
            return tip
        # G03-6 过期高亮：背景淡色 + 任意列 tooltip 说明（原先 is_stale 定义了却
        # 没有任何颜色角色——README 宣称的「过期高亮」实际不可见）。
        if role == Qt.ItemDataRole.BackgroundRole and self.is_stale(index.row()):
            from PyQt6.QtGui import QBrush, QColor

            from .theme import current_palette
            color = QColor(str(current_palette().get("warning", "#d29922")))
            color.setAlpha(38)
            return QBrush(color)
        if role == Qt.ItemDataRole.ForegroundRole and self.is_stale(index.row()):
            from PyQt6.QtGui import QColor

            from .theme import current_palette
            return QColor(str(current_palette().get("warning", "#d29922")))
        return None


    # ------------------------------------------------------------ G47-1/5 数据洞察
    def health_value(self, row: int) -> int:
        """G47-1：该行健康度（复用 db.repo_health，带缓存；无 db 时用行内默认 3）。"""
        r = self.row_at(row)
        if r is None:
            return 3
        if self.db is None:
            return r.health
        cached = self._health_cache.get(r.repo_id)
        if cached is None:
            cached = self.db.repo_health(r.repo_id)
            self._health_cache[r.repo_id] = cached
        return cached

    def set_size(self, repo_id: int, text: str) -> None:
        """G47-5：懒加载回填某行磁盘大小并刷新视图。"""
        for row, r in enumerate(self._rows):
            if r.repo_id == repo_id:
                r.size = str(text)
                idx = self.index(row, 7)
                self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DisplayRole])
                return
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
