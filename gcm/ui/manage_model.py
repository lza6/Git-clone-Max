"""仓库管理表模型：数据与视图解耦，支持大批量行的高性能展示与过滤。"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import QAbstractItemModel, QEvent, QModelIndex, QRect, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QStyle,
    QStyledItemDelegate,
)

from ..i18n import tr


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
        # Rev11-C P-02：数据读数列走 mono。缓存一个 QFont（QFont 构造含字族解析，
        # data() 每格每角色都问一次，不能每行每列新建）。
        self._mono_font = None  # 懒建：QFont 在 QApplication 前构造不可靠
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

    _HEADERS = tuple(tr(h) for h in (
        "仓库", "作者/仓库", "本地路径", "上次同步", "HEAD", "历史", "健康", "大小"))

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
                # Rev11-C R-06 收尾：空值显示「—」而不是空串 —— 空串让用户分不清
                # 「没算」和「这列坏了」；与 ToolTip 的解释文案配套。
                return r.size or "—"
            return (r.folder_name if col == 0 else
                    (f"{r.owner}/{r.repo}" if col == 1 else
                     (r.local_path if col == 2 else
                      (r.last_sync_at if col == 3 else
                       (r.head_sha if col == 4 else "")))))
        # Rev11-C P-02：读数列（上次同步/HEAD/健康/大小）走 mono + 数字列右对齐。
        # 与 failure_panel P-04 同口径：数字是仪表读数，必须可纵向比较（Rev11 原则三）；
        # 仓库名/路径是标识符但本轮只改读数列，整表等宽化登记为后续可选。
        if role == Qt.ItemDataRole.FontRole and col in (3, 4, 6, 7):
            if self._mono_font is None:
                from .typography import type_font
                self._mono_font = type_font("mono")
            return self._mono_font
        if role == Qt.ItemDataRole.TextAlignmentRole and col in (3, 4, 6, 7):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # G84-17：健康/大小两列此前**没有任何解释** —— 表里只有 `5/5` 和空串，
        # 用户既不知道 5/5 怎么来的，也不知道空白是"还在算"还是"算不出来"。
        if role == Qt.ItemDataRole.ToolTipRole and col == 6:
            return tr("健康分 0–5 = 最近 20 次同步成功率 50% + 陈旧度 25% + 冲突惩罚 25%"
                      "（空历史默认 3）。6 维明细见「统计中心」的雷达图。")
        if role == Qt.ItemDataRole.ToolTipRole and col == 7:
            return r.size or tr("尚未算出（点「刷新列表」强制重算；大仓库首次需要几秒）")
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
                tip = f"{tip}\n" + tr("超过 {n} 天未同步").format(n=self.stale_days)
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

    def mark_sizes_pending(self, repo_ids, text: str) -> int:
        """G84-17：把一批行的"大小"置为占位文本（如「计算中…」），返回改动行数。

        空白与"计算中"在界面上长得一样，用户无法判断是坏了还是在跑 —— 这个
        三态（计算中… / 具体值 / 未知）是本次修复的核心。
        """
        wanted = set(repo_ids)
        changed = 0
        for r in self._rows:
            if r.repo_id in wanted and not str(r.size or ""):
                r.size = str(text)
                changed += 1
        if changed:
            idx = self.index(0, 7)
            self.dataChanged.emit(idx, self.index(len(self._rows) - 1, 7),
                                  [Qt.ItemDataRole.DisplayRole])
        return changed

class HistoryButtonDelegate(QStyledItemDelegate):
    """G03-7 末列「查看」按钮委托：每行独立可点击（替代共享按钮）。

    **Rev14 R14-15：自绘，不再用 `style.drawControl(CE_PushButton)`**。
    原生按钮的绘制走操作系统主题：深色主题下就是一块**浅灰方块**，
    与项目四套深色卡面完全不搭（这是 Rev11 `V-12` 记了很久的遗留问题）。
    改成 QPainter 画「panel2 底 + 1px 强边框 + 圆角 5」，颜色全部读
    `theme.current_palette()`，于是它跟着主题走，也满足「颜色只有一个来源」。
    """

    clicked = pyqtSignal(int)  # row
    #: 按钮内框尺寸。写死而不问 `styleFromContents`：原生尺寸随平台变，
    #: 会让「历史」列宽在 Windows 10/11 上平均差 4px，列宽持久化就失去意义。
    BUTTON_W = 56
    BUTTON_H = 22

    def button_rect(self, rect: QRect) -> QRect:
        """在单元格内居中放置按钮。"""
        w = min(self.BUTTON_W, max(30, rect.width() - 12))
        h = min(self.BUTTON_H, max(16, rect.height() - 8))
        return QRect(
            rect.x() + (rect.width() - w) // 2,
            rect.y() + (rect.height() - h) // 2,
            w, h,
        )

    def _label(self) -> str:
        return tr("查看")

    def paint(self, painter, option, index):
        if index.column() != 5:
            super().paint(painter, option, index)
            return
        from PyQt6.QtGui import QColor, QFont, QPainter, QPen

        from .palettes import TOKENS, _shade
        from .theme import current_palette
        from .typography import type_font

        pal = current_palette()
        rect = self.button_rect(option.rect)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        bg = _shade(pal["panel2"], 112 if hovered else 100)
        radius = int(TOKENS["radius"]["control"])
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setBrush(QColor(bg))
        painter.setPen(QPen(QColor(pal["border_strong"]),
                            int(TOKENS["border"]["default"])))
        painter.drawRoundedRect(rect, radius, radius)
        painter.setPen(QColor(pal["text"]))
        painter.setFont(QFont(type_font("small")))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._label())
        painter.restore()

    def sizeHint(self, option, index):
        if index.column() == 5:
            from PyQt6.QtCore import QSize
            return QSize(self.BUTTON_W + 14, self.BUTTON_H + 10)
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
