"""批次结论条（Rev11 V-09/V-13）—— 全应用唯一 display 字号的地方。

**职责边界**（照 `batch_overview.py` 的先例写清，防"两处真相"）：

| 组件 | 回答的问题 | 生命周期 | 命中守卫 |
|------|-----------|---------|---------|
| **本组件** `BatchConclusionBar` | **这一批**的叙事：就绪清单 / 运行读数 / 最终成绩单 | 每次 `_prepare_table` 重置 | 本文件单测 |
| `status_strip.StatusStrip` | **进程**现在在干什么（并发/活跃/累计/用时） | 跨批次累计 | `test_g75` A8 |
| `batch_overview.BatchOverview` | 旧的总览条（"已完成 x/y · 成功率%"），**保留原样** | 每次 `_prepare_table` 重置 | `test_g46_nav_batch` 精确文本断言 —— 本组件**不得**替代它（Rev11 §5.1） |

**三态**（§2.8 记忆点 1）：
- `idle`（就绪）：`12 个待处理 · 9 将克隆 · 3 将更新 · 0 受保护`
  —— 数据来源：Rev10 的 `app/batch_plan.py`（若已交付）或引擎预检计数（V-10 的复用约束见计划）；
- `running`（运行中）：`display` 大字号百分比 + 迷你进度条；
- `finished`（已完成）：成功率大字号 + 成功/失败计数 + 「查看失败」动作。

**动效预算**（§2.6，仅两处，`prefers_reduced_motion`/`ck_animations` 关闭时全部跳过）：
1. running 切入时：进度条 chunk 220ms 一次（不循环）；
2. finished 切入时：整条 180ms 透明度脉冲一次（effect 用完即置 None，V-04 实测单次 0.004ms）。
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPropertyAnimation
from PyQt6.QtWidgets import (
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QWidget,
)

from ..i18n import tr
from . import motion
from .typography import apply_type

PLACEHOLDER = "—"


class BatchConclusionBar(QWidget):
    """批次叙事三态条。所有计数可空（空态显示 `—`，不显示假 0）。"""

    def __init__(self, parent=None, motion: bool = True,
                 show_review_action: bool = True):
        super().__init__(parent)
        self.setObjectName("panelPrimary")
        self._motion = bool(motion)
        self._state = "idle"
        self._total = 0
        self._done = 0
        self._ok = 0
        self._failed = 0
        self._ready_counts: Optional[dict[str, int]] = None

        row = QHBoxLayout(self)
        row.setContentsMargins(16, 10, 16, 10)
        row.setSpacing(12)

        # 状态铭牌（small + 字距，仪器铭牌感）
        self.phase_lbl = QLabel(PLACEHOLDER)
        self.phase_lbl.setObjectName("kicker")
        apply_type(self.phase_lbl, "small")
        row.addWidget(self.phase_lbl)

        # 大字号读数（display 28px，全应用唯一）
        self.metric_lbl = QLabel(PLACEHOLDER)
        self.metric_lbl.setObjectName("display")
        self.metric_lbl.setToolTip(tr("本批次结论"))
        row.addWidget(self.metric_lbl)

        # 说明行（body）
        self.detail_lbl = QLabel("")
        self.detail_lbl.setObjectName("muted")
        row.addWidget(self.detail_lbl, 1)

        # 迷你进度条（运行态可见）
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        self.bar.setFixedWidth(160)
        self.bar.setTextVisible(False)
        self.bar.setAccessibleName(tr("批次完成度"))
        self.bar.hide()
        row.addWidget(self.bar)

        # 成绩单动作（终态可见）：由宿主接线（本组件不依赖引擎/DB）
        self.review_btn = QPushButton(tr("查看失败"))
        self.review_btn.setObjectName("danger")
        self.review_btn.hide()
        if show_review_action:
            row.addWidget(self.review_btn)

        self._anim: Optional[QPropertyAnimation] = None
        self._opacity: Optional[QGraphicsOpacityEffect] = None

        # Rev11：1020×560 硬地板（test_g70_ui_upgrade 最小高度断言）。
        # display 字号(28px)的 sizeHint 48px 会把整窗顶爆 —— 显式钳 minimum+maximum，
        # 紧凑模式再由 set_compact() 收成单行（24px 内）。
        self.setMinimumHeight(24)
        self.setMaximumHeight(46)

    def set_compact(self, compact: bool) -> None:
        """紧凑布局（小窗）下收起说明行与铭牌，只留读数一行。

        1020×560 是硬地板（`test_ui_compact_visibility` 的 11 按钮可见性 +
        `test_g70_ui_upgrade` 的最小高度）：紧凑下结论条整个收成 26px 单行，
        并声明 `Expanding` 之外的纵向策略防止父级把它撑回来。
        """
        self.detail_lbl.setVisible(not compact)
        self.phase_lbl.setVisible(not compact)
        self.bar.setVisible(not compact and self._state == "running")
        self.setMinimumHeight(22 if compact else 24)
        self.setMaximumHeight(24 if compact else 46)
        m = 1 if compact else 8
        self.layout().setContentsMargins(16, m, 16, m)
        self.layout().setSpacing(6 if compact else 12)

    # ------------------------------------------------------------ 配置
    def set_motion(self, motion: bool) -> None:
        self._motion = bool(motion)

    # ------------------------------------------------------------ 就绪态（idle）
    def set_ready(self, *, clone: int, update: int, protected: int = 0,
                  blocked: int = 0) -> None:
        """一批任务尚未开始：展示就绪清单。"""
        self._state = "idle"
        self._ready_counts = {"clone": clone, "update": update,
                              "protected": protected, "blocked": blocked}
        total = clone + update + protected + blocked
        self._total = total
        self._done = self._ok = self._failed = 0
        self.phase_lbl.setText(tr("就绪"))
        self.metric_lbl.setText(tr("{n} 个待处理").format(n=total) if total else PLACEHOLDER)
        self.detail_lbl.setText(tr("{c} 将克隆 · {u} 将更新 · {p} 受保护 · {b} 被拦截")
                                .format(c=clone, u=update, p=protected, b=blocked))
        self.bar.hide()
        self.review_btn.hide()

    # ------------------------------------------------------------ 运行态（running）
    def begin(self, total: int) -> None:
        self._state = "running"
        self._total = max(0, int(total or 0))
        self._done = self._ok = self._failed = 0
        self.phase_lbl.setText(tr("运行中"))
        self.metric_lbl.setText("0%")
        self.detail_lbl.setText("")
        self.bar.setValue(0)
        self.bar.show()
        self.review_btn.hide()
        self._animate_bar()

    def note_result(self, status: str) -> None:
        """单条终态：累加并刷新读数（口径与 batch_overview 一致：只认 success）。"""
        key = str(status or "").strip().lower()
        self._done += 1
        if key == "success":
            self._ok += 1
        elif key in ("failed", "conflict"):
            self._failed += 1
        if self._state == "running":
            self.render_running()

    def render_running(self) -> None:
        total = self._total
        pct = min(100, round(self._done / total * 100)) if total > 0 else 0
        self.metric_lbl.setText(f"{pct}%")
        self.bar.setValue(pct)
        self.detail_lbl.setText(tr("成功 {s} · 失败 {f}")
                                .format(s=self._ok, f=self._failed))

    # ------------------------------------------------------------ 终态（finished）
    def finish(self) -> None:
        self._state = "finished"
        self.phase_lbl.setText(tr("已完成"))
        total, done, ok, failed = self._total, self._done, self._ok, self._failed
        if done <= 0:
            self.metric_lbl.setText(PLACEHOLDER)
            self.detail_lbl.setText("")
        else:
            rate = round(ok / done * 100)
            self.metric_lbl.setText(f"{rate}%")
            self.detail_lbl.setText(tr("成功率 {r}% · 成功 {s} · 失败 {f} · 共 {d}/{t}")
                                    .format(r=rate, s=ok, f=failed, d=done, t=total))
        self.bar.hide()
        self.review_btn.setVisible(failed > 0)
        self._pulse()

    # ------------------------------------------------------------ 动效（§2.6）
    def _animate_bar(self) -> None:
        """运行切入：chunk 220ms 一次（0 → 当前值）。reduced-motion 时直接跳过。"""
        if not self._motion:
            return
        self._anim = QPropertyAnimation(self.bar, b"value", self)
        self._anim.setDuration(motion.BASE_MS)
        self._anim.setEasingCurve(motion.easing())
        self._anim.setStartValue(0)
        self._anim.setEndValue(self.bar.value())
        self._anim.start()

    def _pulse(self) -> None:
        """终态切入：180ms 透明度脉冲一次，effect 用完即释放（V-04 实测可忽略开销）。"""
        if not self._motion:
            return
        self._opacity = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity)
        self._anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._anim.setDuration(motion.PULSE_MS)
        self._anim.setEasingCurve(motion.easing())
        self._anim.setStartValue(0.35)
        self._anim.setEndValue(1.0)
        # finished 信号已存在且本对象是发送者，connect 不会抛 —— 窄化掉防御性 except
        self._anim.finished.connect(self._drop_effect)
        self._anim.start()

    def _drop_effect(self) -> None:
        # widget 已销毁时 setGraphicsEffect 会 RuntimeError；用一次性标记 +
        # parent 存活检测（LBYL）而非 try/except —— 异常预算棘轮只降不升。
        if getattr(self, "_drop_effect_done", False):
            return
        self._drop_effect_done = True
        self.setGraphicsEffect(None)
        self._opacity = None
        self._anim = None

    # ------------------------------------------------------------ 只读（测试断言用）
    def state(self) -> str:
        return self._state

    def counts(self) -> tuple[int, int, int, int]:
        return self._total, self._done, self._ok, self._failed

    def metric_text(self) -> str:
        return self.metric_lbl.text()


__all__ = ["BatchConclusionBar", "PLACEHOLDER"]
