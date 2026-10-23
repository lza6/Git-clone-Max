"""色板与设计 token（G75）。

从 `theme.py` 拆出，原因：新增 `cta/on_cta/glow` 三个 token × 四套主题后，
色板数据本身就占掉近 200 行；与 QSS 拼装混在一个文件会破 800 行硬上限。

**四套主题各自的美学立场**（不是同一个色板换亮度）：

- `deep` → **Ink（墨）**：近墨黑底 + 骨白暖字 + 唯一的"示波器青"（只用于"正在发生"）。
  主操作是**骨白反相块**（`cta`/`on_cta`），不再是一个蓝色按钮 —— 蓝色是 GitHub 的身份证，
  不是这个工具的。
- `light` → **Paper（纸）**：暖纸底 + 墨字，主操作是**墨色反相块**。
- `nord` → 保留北欧低饱和，但结构对齐（同样有 `cta`/`glow`）。
- `hc` → 纯黑/纯白，文本对比度 ≥7:1（AAA），给低视力用户。

**对比度是硬约束**（`tests/test_g70_ui_upgrade.py` + `tests/test_g75_visual_system.py`
逐主题断言）：正文 ≥4.5:1、弱文本 ≥4.5:1、语义色对控件底 ≥4.5:1、
输入边界 ≥3:1、`on_cta`/`cta` ≥4.5:1。改色值前先跑那两组测试。
"""
from __future__ import annotations

from PyQt6.QtGui import QColor

# 色板键 → 语义。四套色板必须**键集合完全一致**（守卫：test_all_palettes_share_layout）。
PALETTES: dict[str, dict[str, str]] = {
    "deep": {
        "bg": "#0b0e10",
        "panel": "#121619",
        "panel2": "#1a2024",
        "border": "#242c31",
        "border_strong": "#66757d",   # 输入框边界：对 panel2 ≥3:1（G75 实测 3.45）
        "accent": "#35c8b4",          # "正在发生"：进度 chunk / 焦点环 / 活跃页签
        "accent2": "#7fb069",
        "on_accent": "#0b0e10",       # 由 accent 亮度推导，见 _on_accent_for
        "cta": "#e8e3da",             # 主操作反相块（新增）
        "on_cta": "#0b0e10",          # 反相块上的文字（新增）
        "glow": "rgba(255, 255, 255, 0.06)",  # 卡片顶部 1px 内嵌高光（新增）
        "success": "#7fb069",
        "warning": "#d8a657",
        "error": "#e06b5c",
        "text": "#e8e3da",
        "text_dim": "#9c978e",
        "console": "#08090b",
        "console_fg": "#d7d2c8",
    },
    "light": {
        "bg": "#f4f0e8",
        "panel": "#fbf8f2",
        "panel2": "#ede7dc",
        "border": "#dad3c6",
        "border_strong": "#8a8279",
        "accent": "#0f7c70",
        "accent2": "#3f6b33",
        "on_accent": "#ffffff",
        "cta": "#16191b",
        "on_cta": "#f4f0e8",
        "glow": "rgba(0, 0, 0, 0.045)",
        "success": "#2f6b3c",
        "warning": "#8a5a12",
        "error": "#a8322a",
        "text": "#16191b",
        "text_dim": "#5e5a52",
        "console": "#fcfaf6",
        "console_fg": "#1b1e20",
    },
    "nord": {
        "bg": "#2e3440",
        "panel": "#3b4252",
        "panel2": "#434c5e",
        "border": "#4c566a",
        "border_strong": "#9aa6ba",
        "accent": "#88c0d0",
        "accent2": "#aec79a",
        "on_accent": "#2e3440",
        "cta": "#eceff4",
        "on_cta": "#2e3440",
        "glow": "rgba(255, 255, 255, 0.06)",
        # G75：原 #A3BE8C 对 panel2 只有 4.23:1（低于 AA 4.5）—— 语义色是正文级文本，
        # 必须过 4.5；提亮到 4.70:1（本次测试实测发现的历史缺陷）
        "success": "#aec79a",
        "warning": "#ebcb8b",
        "error": "#e8b0b5",
        "text": "#d8dee9",
        "text_dim": "#a8b4c8",
        "console": "#232830",
        "console_fg": "#d8dee9",
    },
    # G46-7 高对比（HC）：WCAG AAA（文本/背景 ≥7:1）
    "hc": {
        "bg": "#000000",
        "panel": "#0b0b0b",
        "panel2": "#1a1a1a",
        "border": "#ffffff",
        "border_strong": "#ffffff",
        "accent": "#00e5ff",
        "accent2": "#7cff6b",
        "on_accent": "#000000",
        "cta": "#ffffff",
        "on_cta": "#000000",
        "glow": "rgba(255, 255, 255, 0.12)",
        "success": "#7cff6b",
        "warning": "#ffe14d",
        "error": "#ff6b6b",
        "text": "#ffffff",
        "text_dim": "#e0e0e0",
        "console": "#000000",
        "console_fg": "#ffffff",
    },
}

# 设计 token：四主题共用布局，仅色板不同。数值单位是逻辑像素。
TOKENS: dict[str, dict[str, object]] = {
    # G75：卡面圆角从 8 收到 6（更硬、更像仪表面板），控件 5
    "radius": {"card": 6, "control": 5, "bar": 5, "tab": 8, "checkbox": 4, "chip": 10},
    # 4pt 网格 + 两个"呼吸位"：inset=卡内边距，section=区块之间（有意的大间距）
    "spacing": {"xs": 4, "sm": 8, "md": 12, "lg": 16, "xl": 24, "2xl": 32,
                "inset": 20, "section": 40},
    "border": {"default": 1, "focus": 2},
    # Qt QSS 不支持 box-shadow / transition：以 border/outline + glow 表达反馈
    "shadow": {"focus": "outline"},
    # 表格与进度表的行高（delegate 自绘时的基准）
    "row": {"compact": 24, "default": 26, "manage": 34},
}

# G75 强调色预设：**深底/浅底各一档**。
# 为什么成对：同一个十六进制不可能同时满足"深底上够亮"和"浅底上够暗"——
# 旧实现用单一色强行套四套主题，导致浅色主题的焦点环只有 2:1 左右。
# 口径：深底取 `dark`，浅底取 `light`（按**底色亮度**判定，不看主题名）。
ACCENT_PRESETS: dict[str, dict[str, str]] = {
    "teal": {"dark": "#35c8b4", "light": "#0f7c70"},
    "blue": {"dark": "#5aa0e6", "light": "#0969da"},
    "violet": {"dark": "#a78bfa", "light": "#8250df"},
}

# 没有任何色板时的兜底 accent（仅用于推导 on_accent，不参与渲染）
_FALLBACK_ACCENT = "#35c8b4"


def _on_accent_for(accent: str, palette: dict) -> str:
    """G70：按 accent 亮度选择强调色上的文字色，保证 ≥4.5:1。

    浅色 accent（Ink #35C8B4 2.08:1 / nord #88C0D0 2.00:1 / hc #00E5FF 1.54:1）
    用深色文字；深色 accent（light #0F7C70 5.08:1 / violet 5.05:1）用白字。
    """
    if contrast_ratio("#ffffff", accent) >= 4.5:
        return "#ffffff"
    return palette["bg"] if _luminance(palette["bg"]) < 0.2 else "#000000"


def pick_preset_accent(spec: dict, bg: str) -> str:
    """按底色亮度挑 deep/light 两档里的那一档（spec 为空返回空串）。"""
    dark = str(spec.get("dark") or "")
    light = str(spec.get("light") or "")
    if not (dark or light):
        return ""
    try:
        is_dark_bg = _luminance(bg) < 0.2
    except Exception:
        is_dark_bg = True
    return dark if (is_dark_bg and dark) else (light or dark)


def theme_with_accent(palette: dict, preset) -> dict:
    """返回换用指定强调色后的色板副本。

    `preset` 可以是：
    - `ACCENT_PRESETS` 的键（按底色亮度挑 dark/light 档）；
    - 直接给 dict（`{"accent": "#123456"}`，测试与自定义配色用）。
    """
    pal = dict(palette)
    if isinstance(preset, dict):
        pal.update({k: v for k, v in preset.items() if k in ("accent", "accent2")})
    else:
        picked = pick_preset_accent(ACCENT_PRESETS.get(str(preset)) or {},
                                   str(pal.get("bg") or "#000000"))
        if picked:
            pal["accent"] = picked
    # 强调色变化后 on_accent 必须重算，否则浅色 accent 上会压深字（2:1）
    pal["on_accent"] = _on_accent_for(str(pal.get("accent") or _FALLBACK_ACCENT), pal)
    return pal


def _luminance(hex_color: str) -> float:
    """WCAG 相对亮度（G70：修正绿/蓝权重写反的既有缺陷）。"""
    c = QColor(hex_color)
    rgb = [v / 255.0 for v in (c.red(), c.green(), c.blue())]

    def _f(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    # WCAG 2.x: 0.2126 R + 0.7152 G + 0.0722 B
    return 0.2126 * _f(rgb[0]) + 0.7152 * _f(rgb[1]) + 0.0722 * _f(rgb[2])


def contrast_ratio(c1: str, c2: str) -> float:
    """WCAG 对比度（1..21）。"""
    l1, l2 = _luminance(c1), _luminance(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def hc_contrast_ratio(palette: dict) -> float:
    """G46-7：文本 vs 背景对比度（HC 主题应 ≥7）。"""
    return contrast_ratio(palette["text"], palette["bg"])


def _shade(hex_color: str, factor: int) -> str:
    """用 QColor.darker/lighter 生成按压/悬浮底色（factor>100 变暗）。"""
    try:
        return QColor(hex_color).darker(factor).name()
    except Exception:
        return hex_color
