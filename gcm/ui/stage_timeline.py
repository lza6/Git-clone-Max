"""阶段耗时时间线（Rev11 V-19）—— 仓库详情对话框里的一条横向分段条。

**数据来源**：`sync_history.stage_json`（`db/stage_stats.loads_stages` 还原成
`{阶段: 毫秒}`）。四个阶段：`precheck / transfer / checkout / hook`。
无数据时整条显示 `—`（不编造）—— 与结论条/齿条同一纪律。

**绘制口径**：
- 纯 QPainter 自绘（与 progress_delegate 同一范式，零 QWidget/段）；
- 每段底色 = panel2，段间 1px 边框；transfer 段（通常占大头）用 accent 弱化版
  （alpha 60）提示「传输是主要成本」，其余段用 text_dim 弱化版；
- 悬停 ToolTip 显示每段毫秒数（QPainter 内不好做逐段 hover，改为整条 tooltip
  列出四段明细）。
"""
from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from ..i18n import tr

# 阶段顺序（写死展示顺序；stage_json 里缺的阶段跳过）
STAGE_ORDER = ("precheck", "transfer", "checkout", "hook")

STAGE_LABELS = {
    "precheck": tr("预检"),
    "transfer": tr("传输"),
    "checkout": tr("检出"),
    "hook": tr("钩子"),
}


def normalize_stages(raw: dict | None) -> list[tuple[str, float]]:
    """stage dict → [(阶段, 毫秒)]，按 STAGE_ORDER 排序、滤掉 ≤0；空返回 []。

    LBYL 判型而非 try/except：异常预算棘轮（test_g80_hygiene）只降不升。
    """
    if not isinstance(raw, dict):
        return []
    out: list[tuple[str, float]] = []
    for key in STAGE_ORDER:
        v = raw.get(key)
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            continue
        if v > 0:
            out.append((key, float(v)))
    return out


def tooltip_text(stages: list[tuple[str, float]]) -> str:
    """四段明细的整条 tooltip（毫秒 → 与 result_labels.format_duration 同形态）。"""
    from .result_labels import format_duration
    if not stages:
        return tr("暂无分阶段耗时数据")
    parts = [f"{STAGE_LABELS.get(k, k)} {format_duration(int(ms))}"
             for k, ms in stages]
    return " · ".join(parts)


class StageTimeline(QWidget):
    """横向分段条。`set_stages({})` 即空态（画一个 `—` 占位，不编数据）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stages: list[tuple[str, float]] = []
        self.setFixedHeight(26)
        self.setToolTip(tooltip_text([]))
        self.setAccessibleName(tr("分阶段耗时时间线"))

    def set_stages(self, raw: dict | None) -> None:
        self._stages = normalize_stages(raw)
        self.setToolTip(tooltip_text(self._stages))
        self.update()

    def has_data(self) -> bool:
        return bool(self._stages)

    def paintEvent(self, event) -> None:  # noqa: N802 —— Qt 命名
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        w, h = self.width(), self.height()
        if not self._stages:
            # 空态：画一个居中 em-dash（文本，不编数据）
            from .typography import type_font
            p.setFont(type_font("mono"))
            p.setPen(self._dim())
            p.drawText(QRect(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "—")
            return
        total = sum(ms for _, ms in self._stages)
        x = 0.0
        for i, (key, ms) in enumerate(self._stages):
            seg_w = max(2.0, w * ms / total)
            rect = QRect(int(x), 4, int(seg_w) - (1 if i else 0), h - 8)
            color = self._segment_color(key)
            p.fillRect(rect, color)
            # 段边界 1px
            p.setPen(QPen(self._dim(), 1))
            p.drawRect(rect)
            # 段内标签（宽度足够才画，避免挤成一团）
            from .typography import type_font
            p.setFont(type_font("small"))
            label = STAGE_LABELS.get(key, key)
            if rect.width() > 52:
                p.setPen(self._text_color())
                p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            x += seg_w

    # ------------------------------------------------------------ 色板
    @staticmethod
    def _palette() -> dict:
        # 与 radar_chart._palette 同款兜底：无 QApplication 时直接读模块级常量。
        # 失败路径只有"模块导入异常"这一种 —— 窄化到 ImportError。
        try:
            from .theme import current_palette
            return current_palette()
        except ImportError:
            from .palettes import PALETTES
            return dict(PALETTES["deep"])

    @classmethod
    def _dim(cls) -> QColor:
        c = QColor(cls._palette().get("text_dim") or "#9c978e")
        c.setAlpha(90)
        return c

    @classmethod
    def _segment_color(cls, key: str) -> QColor:
        pal = cls._palette()
        base = pal.get("accent") if key == "transfer" else pal.get("text_dim")
        c = QColor(str(base or "#888888"))
        c.setAlpha(60)
        return c

    @classmethod
    def _text_color(cls) -> QColor:
        return QColor(cls._palette().get("text") or "#e8e3da")


__all__ = ["StageTimeline", "STAGE_ORDER", "STAGE_LABELS",
           "normalize_stages", "tooltip_text"]
