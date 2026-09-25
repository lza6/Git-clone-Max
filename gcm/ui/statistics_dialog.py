"""G07-1/G37-1/G37-5 统计中心对话框：全局聚合 + 30 天趋势 + 用量统计。

- G37-1：最近 30 天同步趋势（QPainter 自绘柱状，成功绿/失败红/冲突橙）
- G37-5：累计克隆/更新次数（来自 stats_overview 与 action 聚合）
"""
from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .theme import PALETTE, QSS

# 趋势柱配色（与主题色板联动：成功/失败/冲突）
_TREND_COLORS = {
    "success": (PALETTE["accent2"], PALETTE["accent2"]),
    "failed": (PALETTE["error"], PALETTE["error"]),
    "conflict": (PALETTE["warning"], PALETTE["warning"]),
}


class _TrendCanvas(QWidget):
    """QPainter 自绘 30 天趋势柱状图（纯 Qt，无图表库依赖）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data: list[dict] = []
        self.setMinimumSize(460, 120)
        self.setToolTip("最近 30 天每日同步：绿=成功 红=失败 橙=冲突")

    def set_data(self, data: list[dict]) -> None:
        self._data = data or []
        self._hover_idx = -1
        self.update()

    def data_at(self, x: float) -> int:
        """G47-4：按 x 坐标返回命中的日期索引（无数据/越界返回 -1）。"""
        if not self._data:
            return -1
        n = len(self._data)
        slot = self.width() / n if n else 0
        idx = int(x // slot) if slot > 0 else -1
        return idx if 0 <= idx < n else -1

    def mouseMoveEvent(self, e):
        """G47-4：hover 显示当日成功/失败/冲突 tooltip。"""
        try:
            idx = self.data_at(e.position().x())
            if idx >= 0:
                d = self._data[idx]
                tip = (f"{d.get('date', '')}：成功 {d.get('success', 0)} · "
                       f"失败 {d.get('failed', 0)} · 冲突 {d.get('conflict', 0)}")
                self.setToolTip(tip)
                self._hover_idx = idx
                self.update()
        except Exception:
            pass
        super().mouseMoveEvent(e)

    def paintEvent(self, e):
        from PyQt6.QtGui import QBrush
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        painter.fillRect(self.rect(), QColor(PALETTE["panel"]))
        if not self._data:
            painter.setPen(QColor(PALETTE["text_dim"]))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             "（暂无同步数据）")
            painter.end()
            return
        n = len(self._data)
        max_v = max((max(d.get("success", 0), d.get("failed", 0),
                         d.get("conflict", 0)) for d in self._data), default=1)
        max_v = max(max_v, 1)
        slot = w / n
        bar_w = max(slot * 0.55, 2.0)
        for i, d in enumerate(self._data):
            x = i * slot + (slot - bar_w) / 2
            # 三序列从下往上堆叠
            y_cursor = h - 6
            for key in ("success", "failed", "conflict"):
                v = int(d.get(key, 0))
                if v <= 0:
                    continue
                bh = max(1.0, (v / max_v) * (h - 16))
                color = _TREND_COLORS[key][0]
                painter.fillRect(QRectF(x, y_cursor - bh, bar_w, bh),
                                 QBrush(QColor(color)))
                y_cursor -= bh
        # 横轴边框
        painter.setPen(QPen(QColor(PALETTE["border"]), 1))
        painter.drawLine(0, h - 4, w, h - 4)
        painter.end()


class StatisticsDialog(QDialog):
    """统计中心：全局聚合 + 30 天趋势 + 用量统计。"""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("统计中心")
        self.resize(560, 460)
        self.setMinimumSize(460, 380)
        self.setStyleSheet(QSS)
        self._days = 30  # G47-4 趋势周期（7/30/90）
        self._build_ui()
        self._refresh()

    # ------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        v = QVBoxLayout(self)
        v.setSpacing(10)

        self.lbl_title = QLabel("📊 统计中心")
        self.lbl_title.setObjectName("pageTitle")
        v.addWidget(self.lbl_title)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setWordWrap(True)
        v.addWidget(self.lbl_summary)

        self.lbl_hosts = QLabel("")
        self.lbl_hosts.setObjectName("muted")
        self.lbl_hosts.setWordWrap(True)
        v.addWidget(self.lbl_hosts)

        # G37-5 用量统计（累计克隆/更新/新增提交）
        self.lbl_usage = QLabel("")
        self.lbl_usage.setObjectName("muted")
        self.lbl_usage.setWordWrap(True)
        v.addWidget(self.lbl_usage)

        # G37-1/G47-4 同步趋势（周期可切 7/30/90，可导出 PNG）
        h_trend = QHBoxLayout()
        h_trend.addWidget(QLabel("同步趋势（绿=成功 红=失败 橙=冲突）"))
        self.period_combo = QComboBox()
        for _d, _lbl in ((7, "近 7 天"), (30, "近 30 天"), (90, "近 90 天")):
            self.period_combo.addItem(_lbl, _d)
        self.period_combo.setCurrentIndex(1)
        self.period_combo.currentIndexChanged.connect(
            lambda _i: self._on_period_changed())
        h_trend.addWidget(self.period_combo)
        h_trend.addStretch()
        self.btn_export_png = QPushButton("导出 PNG")
        self.btn_export_png.clicked.connect(self._export_png)
        h_trend.addWidget(self.btn_export_png)
        v.addLayout(h_trend)
        self.trend_canvas = _TrendCanvas(self)
        v.addWidget(self.trend_canvas)

        # G43-3 平均耗时 Top10 慢仓库（帮助定位网络/大仓问题）
        v.addWidget(QLabel("平均耗时 Top10 慢仓库（秒）"))
        self.lbl_slow = QLabel("")
        self.lbl_slow.setObjectName("muted")
        self.lbl_slow.setWordWrap(True)
        v.addWidget(self.lbl_slow)


        # G53-1 多维评分卡（成功率/陈旧度/冲突/大小/标签/最近同步）
        v.addWidget(QLabel("多维评分卡（0-100，越高越好）"))
        self.lbl_scorecard = QLabel("")
        self.lbl_scorecard.setObjectName("muted")
        self.lbl_scorecard.setWordWrap(True)
        v.addWidget(self.lbl_scorecard)

        # G53-3 存储看板（数据目录占用 / 磁盘剩余 / 建议）
        v.addWidget(QLabel("存储看板"))
        self.lbl_storage = QLabel("")
        self.lbl_storage.setObjectName("muted")
        self.lbl_storage.setWordWrap(True)
        v.addWidget(self.lbl_storage)
        self.lbl_note = QLabel("数据来自 sync_history 全量聚合；每次同步自动记录。")
        self.lbl_note.setObjectName("muted")
        v.addWidget(self.lbl_note)

        v.addStretch(1)

        btns = QHBoxLayout()
        btns.addStretch(1)
        self.btn_refresh = QPushButton("刷新")
        self.btn_refresh.clicked.connect(self._refresh)
        btns.addWidget(self.btn_refresh)
        self.btn_close = QPushButton("关闭")
        self.btn_close.clicked.connect(self.accept)
        btns.addWidget(self.btn_close)
        v.addLayout(btns)

    # ------------------------------------------------------------ 数据
    def _refresh(self) -> None:
        try:
            ov = self.db.stats_overview()
        except Exception:
            ov = {"total_repos": 0, "total_syncs": 0,
                  "success_syncs": 0, "failed_syncs": 0, "by_host": {}}
        self.lbl_summary.setText(
            f"仓库总数：{ov.get('total_repos', 0)}\n"
            f"同步总次数：{ov.get('total_syncs', 0)}\n"
            f"成功：{ov.get('success_syncs', 0)} · 失败：{ov.get('failed_syncs', 0)}")
        by_host = ov.get("by_host") or {}
        if by_host:
            self.lbl_hosts.setText(
                "平台分布：" + " · ".join(f"{h}: {c}" for h, c in by_host.items()))
        else:
            self.lbl_hosts.setText("平台分布：（暂无仓库）")

        # G37-5 用量统计：累计克隆/更新（按 action 聚合）
        try:
            usage = self.db.stats_usage()
            self.lbl_usage.setText(
                f"累计克隆 {usage.get('clones', 0)} 次 · "
                f"累计更新 {usage.get('updates', 0)} 次 · "
                f"累计新增提交 {usage.get('commits', 0)} 个")
        except Exception:
            self.lbl_usage.setText("用量统计：（不可用）")

        # G37-1 30 天趋势
        try:
            self.trend_canvas.set_data(
                self.db.stats_daily(days=int(getattr(self, "_days", 30))))
        except Exception:
            self.trend_canvas.set_data([])

        # G43-3 平均耗时 Top10 慢仓库
        try:
            slow = self.db.stats_slow_repos(limit=10) if hasattr(self.db, "stats_slow_repos") else []
        except Exception:
            slow = []
        if not isinstance(slow, (list, tuple)) or not slow:
            self.lbl_slow.setText("（暂无同步数据）")
        else:
            lines = []
            for i, it in enumerate(slow, 1):
                avg_s = it.get("avg_ms", 0) / 1000.0
                lines.append(
                    f"{i}. {it.get('owner_repo', '')}  "
                    f"{avg_s:.1f}s × {it.get('count', 0)}  "
                    f"{it.get('last_sync', '')}")
            self.lbl_slow.setText("\n".join(lines))

        # G53-1 多维评分卡展示（最差在前 5 + 总览）
        try:
            from ..app.alerts import overall_summary, scorecards
            repos = self.db.list_repos(host=None) if hasattr(self.db, "list_repos") else []
            cards = scorecards(repos or [],
                                lambda rid: self.db.history(rid, 20) if hasattr(self.db, "history") else [],
                                now=None)
            summary = overall_summary(cards)
            if cards:
                avg = summary["avg"]
                parts = [f"{k} {v}" for k, v in avg.items()]
                lines = [f'总评 {summary["overall"]}/100 · ' + ' '.join(parts)]
                for c in summary["weakest"][:3]:
                    lines.append(f'  {c["key"]} {c["overall"]}分：{c["suggestions"][0]}')
                self.lbl_scorecard.setText("\\n".join(lines))
            else:
                self.lbl_scorecard.setText("（暂无仓库数据）")
        except Exception:
            self.lbl_scorecard.setText("评分卡：（不可用）")

        # G53-3 存储看板
        try:
            from ..app.alerts import storage_overview
            data_dir = getattr(self, "data_dir", None) or getattr(self.db, "path", None)
            info = storage_overview(data_dir, threshold_gb=5)
            files = info.get("files", {})
            fsize = " · ".join(f"{k} {v//1024}KB" for k, v in files.items() if v)
            self.lbl_storage.setText(
                f'占用 {info["total_bytes"]//1024}KB ｜ 磁盘剩余 {info["free_gb"]}GB\\n{fsize}\\n{info["suggest"]}')
        except Exception:
            self.lbl_storage.setText("存储看板：（不可用）")

    # ------------------------------------------------------------ G47-4 周期/PNG
    def _on_period_changed(self):
        """G47-4：切换 7/30/90 天周期并重算趋势。"""
        try:
            self._days = int(self.period_combo.currentData() or 30)
        except Exception:
            self._days = 30
        self._refresh()

    def _export_png(self):
        """G47-4：导出趋势图为 PNG。"""
        from pathlib import Path
        path, _ = QFileDialog.getSaveFileName(
            self, "导出趋势图", str(Path.home() / "trend.png"), "PNG (*.png)")
        if not path:
            return
        try:
            ok = self.trend_canvas.grab().save(path)
            if not ok:
                raise RuntimeError("保存失败")
        except Exception as e:
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.warning(self, "导出失败", str(e))
