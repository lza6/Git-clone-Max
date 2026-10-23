"""排版系统（G75）：三字族 + 七级字号阶梯。

**为什么单独成模块**：QSS 能表达 `font-family` / `font-size` / `font-weight`，但
**不支持 `letter-spacing`** —— 字距只能用 `QFont.setLetterSpacing()` 在控件上设。
所以"排版"天然有两条落地路径（QSS 字符串 + 控件级 QFont），把它塞回 `theme.py`
会同时撑爆 800 行硬上限与职责边界。

**字族为什么是这三个**（详见 `docs/计划书/` 的 UI 指南 §2.2）：
- `Bahnschrift`：Windows 10 内置的 DIN 血统工业窄体，用于标题与数字读数；
- `Microsoft YaHei UI`：中文正文（Bahnschrift 无中文字形，靠回退）；
- `Cascadia Mono`：URL / 路径 / 时间戳 / 计数 / 日志 —— 数字等宽才对齐。

三个字族都是**系统内置**，不打包 ttf、不联网下载。缺字族时按栈回退，
最末端必有 `sans-serif` / `monospace`。
"""
from __future__ import annotations

from typing import Any

# 基体字号（px @ scale=1.0）。所有角色由它派生，改这里等于整体缩放。
FONT_BASE_PX = 13
# UI 层字号下限：设置页允许的最小缩放（QSS 底层仍按 [0.8, 1.6] 钳制）
FONT_SCALE_MIN_UI = 0.9

FONT_STACK = ('"Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", '
              '"Segoe UI Symbol", "Segoe UI Emoji", sans-serif')
FONT_STACK_DISPLAY = ('"Bahnschrift SemiCondensed", "Bahnschrift", '
                      '"Segoe UI Semibold", ' + FONT_STACK)
FONT_STACK_MONO = ('"Cascadia Mono", "Cascadia Code", Consolas, '
                   '"Microsoft YaHei UI", monospace')

# 字族键 → QSS 字族串；`stack` 字段引用这里的键。
_FAMILIES: dict[str, str] = {
    "display": FONT_STACK_DISPLAY,
    "body": FONT_STACK,
    "mono": FONT_STACK_MONO,
}

# 七级字号阶梯（px @ scale=1.0）。`spacing` 是字距（px），只能经 QFont 生效。
TYPE_SCALE: dict[str, dict[str, Any]] = {
    "display": {"px": 28, "weight": 600, "stack": "display", "spacing": 0.4},
    "h1": {"px": 20, "weight": 700, "stack": "display", "spacing": 0.2},
    "h2": {"px": 15, "weight": 600, "stack": "body", "spacing": 0.0},
    "body": {"px": FONT_BASE_PX, "weight": 400, "stack": "body", "spacing": 0.0},
    "mono": {"px": 12, "weight": 400, "stack": "mono", "spacing": 0.0},
    # small 带 0.5px 字距：仪器铭牌/小标签靠字距拉开，QSS 无 letter-spacing
    "small": {"px": 11, "weight": 500, "stack": "body", "spacing": 0.5},
    "metric": {"px": 18, "weight": 600, "stack": "display", "spacing": 0.3},
}

# 角色顺序（由大到小）；测试用它断言"阶梯严格递减"。
ROLE_ORDER = ("display", "h1", "metric", "h2", "body", "mono", "small")


def _clamp_scale(scale: float) -> float:
    try:
        s = float(scale)
    except (TypeError, ValueError):
        return 1.0
    return max(0.8, min(1.6, s))


def stack_for(stack_key: str) -> str:
    """返回 QSS 可用的字族串（未知键回退正文族）。"""
    return _FAMILIES.get(stack_key) or FONT_STACK


def role_stack(role: str) -> str:
    """返回某个排版角色对应的 QSS 字族串。"""
    spec = TYPE_SCALE.get(role) or TYPE_SCALE["body"]
    return stack_for(str(spec["stack"]))


def type_px(role: str, scale: float = 1.0) -> int:
    """返回角色的像素字号（已按 scale 缩放）。"""
    spec = TYPE_SCALE.get(role) or TYPE_SCALE["body"]
    return round(int(spec["px"]) * _clamp_scale(scale))


def type_css(role: str, scale: float = 1.0) -> str:
    """返回可直接拼进 QSS 块的字号 + 字族 + 字重声明。"""
    spec = TYPE_SCALE.get(role) or TYPE_SCALE["body"]
    return (f"font-size: {type_px(role, scale)}px; "
            f"font-family: {role_stack(role)}; "
            f"font-weight: {int(spec['weight'])};")


def type_font(role: str, scale: float = 1.0):
    """返回某个排版角色对应的 QFont（自绘 delegate 用；QSS 到不了 QPainter）。"""
    from PyQt6.QtGui import QFont

    spec = TYPE_SCALE.get(role) or TYPE_SCALE["body"]
    font = QFont()
    families = [f.strip().strip('"') for f in role_stack(role).split(",")]
    font.setFamilies([f for f in families if f])
    font.setPixelSize(type_px(role, scale))
    font.setWeight(QFont.Weight(int(spec["weight"])))
    spacing = float(spec.get("spacing") or 0.0)
    if spacing:
        font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return font


def apply_type(widget, role: str, scale: float = 1.0) -> None:
    """把排版角色套到控件（QFont 路径：唯一能设字距的方式）。

    QSS 没有 `letter-spacing`，所以标题的"工业窄体 + 微字距"必须走这里。
    控件缺 `setFont` 时静默跳过（例如 QAction 之类的非 QWidget 对象）。
    """
    setter = getattr(widget, "setFont", None)
    if not callable(setter):
        return
    setter(type_font(role, scale))
