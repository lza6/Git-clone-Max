"""仪表齿条（G75）：贴顶的一行等宽读数 —— 并发 / 活跃 / 成功 / 失败 / 用时。

**设计意图**：这个工具的核心体验是"挂着刷几百个仓库，想知道它现在在干什么"。
原来的顶栏只有标题 + `并行线程 8`（`main_window._build_ui` 旧实现），运行时看不出
进度、速率、用时。齿条把"运行态"变成一眼可读的仪表：数字用等宽字体对齐，
空态一律显示 `—`（而不是假的 0，那会让人以为已经跑过）。

**与批次总览条的分工**（避免两处真相）：
- 本齿条 = **进程运行态**（并发/活跃/成功/失败/用时），跨批次累计，结束时停止计时；
- `progress_table.batch_total_lbl` = **本批次完成度**（已完成 x/y · 成功率 %），
  它被 `tests/test_g46_nav_batch.py` 与本窗口托盘逻辑复用，职责不同，因此保留。

**零引擎契约变更**：全部读数来自窗口既有槽（`_on_worker_progress` /
`_on_worker_result` / `_on_engine_finished`），不改 `app/engine` 的接口。
"""
from __future__ import annotations

import time

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QWidget

from ..i18n import tr

PLACEHOLDER = "—"


def format_elapsed(seconds=None) -> str:
    """秒 → `mm:ss`（≥1 小时用 `h:mm:ss`）；None → `—`。"""
    if seconds is None:
        return PLACEHOLDER
    try:
        total = max(0, int(seconds))
    except (TypeError, ValueError):
        return PLACEHOLDER
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_count(value) -> str:
    """计数 → 字符串；None → `—`（空态不显示假的 0）。"""
    if value is None:
        return PLACEHOLDER
    try:
        return str(int(value))
    except (TypeError, ValueError):
        return PLACEHOLDER


def format_metrics(*, concurrency: int, active=None, success=None, failed=None,
                   elapsed=None) -> str:
    """把读数拼成一行（供 tooltip / 日志 / 测试断言复用同一口径）。"""
    return " · ".join((
        tr("并行线程 {n}").format(n=int(concurrency)),
        tr("活跃 {v}").format(v=format_count(active)),
        tr("成功 {v}").format(v=format_count(success)),
        tr("失败 {v}").format(v=format_count(failed)),
        tr("用时 {v}").format(v=format_elapsed(elapsed)),
    ))


class StatusStrip(QWidget):
    """运行态仪表齿条。所有读数可空（`—`），结束时冻结用时。"""

    def __init__(self, parent=None, concurrency: int = 8, motion: bool = True,
                 clock=None):
        super().__init__(parent)
        self.setObjectName("statusStrip")
        self._concurrency = max(1, int(concurrency or 1))
        self._motion = bool(motion)
        self._clock = clock or time.monotonic
        self._active = None
        self._success = None
        self._failed = None
        self._started_at = None
        self._final_elapsed = None
        self._total = 0

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 2, 0, 2)
        row.setSpacing(10)

        # 品牌位（等宽小字，仪器铭牌感；不再占一整行标题的视觉预算）
        self.brand_lbl = QLabel("Git-clone-Max")
        self.brand_lbl.setObjectName("kicker")
        # G75：字距只能经 QFont 生效（QSS 无 letter-spacing）—— 铭牌的零点几像素
        # 就是"仪器感"的全部来源，所以这里必须真设一次字体，而不是只靠 QSS。
        from .typography import apply_type
        apply_type(self.brand_lbl, "small")
        row.addWidget(self.brand_lbl)
        brand_sep = QLabel("·")
        brand_sep.setObjectName("muted")
        row.addWidget(brand_sep)
        # 兼容：settings_panel 会直接 setText("并行线程 N")，故保留同名标签
        self.thread_lbl = QLabel(tr("并行线程 {n}").format(n=self._concurrency))
        self.thread_lbl.setObjectName("monoLabel")
        row.addWidget(self.thread_lbl)
        self.active_lbl = self._mk(row)
        self.success_lbl = self._mk(row)
        self.failed_lbl = self._mk(row)
        self.elapsed_lbl = self._mk(row)
        row.addStretch(1)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self.refresh()

    # ---------------- 构建
    def _mk(self, row: QHBoxLayout) -> QLabel:
        sep = QLabel("·")
        sep.setObjectName("muted")
        row.addWidget(sep)
        lbl = QLabel("")
        lbl.setObjectName("monoLabel")
        row.addWidget(lbl)
        return lbl

    # ---------------- 对外 API
    def set_motion(self, motion: bool) -> None:
        """跟随设置页的「减少动效」开关（G75：只影响将来要加的过渡，不影响读数）。"""
        self._motion = bool(motion)

    def set_concurrency(self, n: int) -> None:
        self._concurrency = max(1, int(n or 1))
        self.thread_lbl.setText(tr("并行线程 {n}").format(n=self._concurrency))
        self.setToolTip(self.metrics_text())

    def begin(self, total: int = 0) -> None:
        """一批任务开始：清零计数、启动计时。"""
        self._total = max(0, int(total or 0))
        self._active = 0
        self._success = 0
        self._failed = 0
        self._started_at = self._clock()
        self._final_elapsed = None
        if not self._timer.isActive():
            self._timer.start()
        self.refresh()

    def note_active(self, active: int) -> None:
        self._active = max(0, int(active))
        self.refresh()

    def note_result(self, status: str) -> None:
        """一条任务终态：累加成功/失败；活跃数随批次推进回落。"""
        key = str(status or "").strip().lower()
        success = int(self._success or 0)
        failed = int(self._failed or 0)
        if key == "success":
            success += 1
        elif key in ("failed", "conflict"):
            failed += 1
        self._success, self._failed = success, failed
        if self._active is not None and self._active > 0:
            self._active -= 1
        self.refresh()

    def end(self) -> None:
        """整批结束：停表并冻结用时（不做清零，用户还要看结果）。"""
        self._timer.stop()
        if self._started_at is not None and self._final_elapsed is None:
            self._final_elapsed = max(0.0, self._clock() - self._started_at)
        self._active = 0 if self._active is not None else None
        self.refresh()

    def reset(self) -> None:
        """回到空态（全 `—`）。"""
        self._timer.stop()
        self._active = self._success = self._failed = None
        self._started_at = None
        self._final_elapsed = None
        self.refresh()

    # ---------------- 读数
    def elapsed_seconds(self) -> float | None:
        if self._final_elapsed is not None:
            return self._final_elapsed
        if self._started_at is None:
            return None
        return max(0.0, self._clock() - self._started_at)

    def is_running(self) -> bool:
        return self._timer.isActive()

    def metrics_text(self) -> str:
        return format_metrics(
            concurrency=self._concurrency, active=self._active,
            success=self._success, failed=self._failed,
            elapsed=self.elapsed_seconds())

    def refresh(self) -> None:
        pal = self._palette()
        self.thread_lbl.setText(tr("并行线程 {n}").format(n=self._concurrency))
        self.active_lbl.setText(tr("活跃 {v}").format(v=format_count(self._active)))
        self.success_lbl.setText(tr("成功 {v}").format(v=format_count(self._success)))
        self.failed_lbl.setText(tr("失败 {v}").format(v=format_count(self._failed)))
        self.elapsed_lbl.setText(
            tr("用时 {v}").format(v=format_elapsed(self.elapsed_seconds())))
        # 有失败时用错误色提醒（颜色是二次编码，文本本身已经写了"失败"）
        fail_color = pal.get("error") if (self._failed or 0) > 0 else pal.get("text_dim")
        self.failed_lbl.setStyleSheet(f"color: {fail_color};")
        self.setToolTip(self.metrics_text())

    def _tick(self) -> None:
        self.elapsed_lbl.setText(
            tr("用时 {v}").format(v=format_elapsed(self.elapsed_seconds())))

    @staticmethod
    def _palette() -> dict:
        from .theme import current_palette
        try:
            return current_palette()
        except Exception:  # pragma: no cover - 无 QApplication 的极端情形
            return {"error": "#E06B5C", "text_dim": "#9C978E"}


__all__ = ["StatusStrip", "format_elapsed", "format_count", "format_metrics",
           "PLACEHOLDER"]
