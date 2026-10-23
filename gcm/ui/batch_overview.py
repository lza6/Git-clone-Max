"""批次总览条（G46-4，G75 从 `progress_table` 抽出）。

职责边界（写在这里防止"两处真相"）：

| 组件 | 回答的问题 | 生命周期 |
|------|-----------|---------|
| **本组件** `BatchOverview` | **这一批**完成了多少、成功率多少 | 每次 `_prepare_table` 重置 |
| `status_strip.StatusStrip` | **进程**现在在干什么（并发/活跃/累计成功失败/用时）| 跨批次累计，整批结束停表 |

两者都会出现在下载页，但回答的不是同一个问题。G75 曾计划把总览条并入齿条，
实测发现：`batch_total_lbl` 的文本被 `tests/test_g46_nav_batch.py` 与本窗口托盘逻辑复用，
且"批次完成度"是用户判断"这一批跑完没有"的唯一依据 —— 合并会造成信息丢失。
故改为**职责划分 + 各自命名清晰**，并把组件独立成模块（`progress_table.py` 已 848 行，破 800 红线）。
"""
from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QWidget


class BatchOverview(QWidget):
    """「已完成 x/总 y · 成功率 %」+ 迷你进度条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.label = QLabel("批次：—")
        self.label.setObjectName("muted")
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFixedWidth(180)
        self.bar.setTextVisible(False)
        self.bar.setAccessibleName("批次完成度")
        row.addWidget(self.label)
        row.addWidget(self.bar, 1)
        row.addStretch()
        self._total = 0
        self._done = 0
        self._ok = 0

    # ---------------- 计数
    def reset(self, total: int) -> None:
        """新批次开始：清零并按总数重算。"""
        self._total = int(total or 0)
        self._done = 0
        self._ok = 0
        self.render()

    def on_result(self, status) -> None:
        """单个终态结果到达：累加并刷新（只认 success，其余计入"已完成但非成功"）。"""
        self._done += 1
        if str(getattr(status, "value", status)) == "success":
            self._ok += 1
        self.render()

    def text(self) -> str:
        return self.label.text()

    # ---------------- 渲染
    def render(self) -> None:
        total, done, ok = self._total, self._done, self._ok
        if total <= 0:
            self.label.setText("批次：—")
            self.bar.setValue(0)
            return
        pct = min(100, round(done / total * 100))
        rate = round(ok / done * 100) if done else 0
        self.label.setText(f"已完成 {done}/{total} · 成功率 {rate}%")
        self.bar.setValue(pct)


__all__ = ["BatchOverview"]
