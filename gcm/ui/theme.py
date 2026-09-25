"""UI 通用：日志事件模型 / 文本高亮 / 样式常量 / 图标 helper。"""

# G55 engineering item: E501 (>88 chars) counts.
# before: 8 rows [232/247/251/254/282/284/287/418]; after: 5 rows [232/247/282/284/287].
# Rows 232/247/282/284/287 are whole-line QSS template text: splitting would change
# emitted QSS bytes, so they stay. Rows 251/254 fixed via locals, 418 via adjacent
# string literal split (regex bytes identical).
from __future__ import annotations

import re
from collections import deque
from enum import Enum
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import QStyle  # QStyle.StandardPixmap 在 QtWidgets 命名空间

# G36-3 统一图标体系：语义键 → QStyle.StandardPixmap（零资源文件、跨平台一致）。
# 找不到映射的键返回空图标（调用方保留 emoji 文本作回退）。
_STD_ICONS: dict[str, QStyle.StandardPixmap] = {
    "play": QStyle.StandardPixmap.SP_MediaPlay,
    "stop": QStyle.StandardPixmap.SP_MediaStop,
    "open_dir": QStyle.StandardPixmap.SP_DirOpenIcon,
    "refresh": QStyle.StandardPixmap.SP_BrowserReload,
    "save": QStyle.StandardPixmap.SP_DialogSaveButton,
    "trash": QStyle.StandardPixmap.SP_TrashIcon,
    "info": QStyle.StandardPixmap.SP_MessageBoxInformation,
    "warn": QStyle.StandardPixmap.SP_MessageBoxWarning,
    "error": QStyle.StandardPixmap.SP_MessageBoxCritical,
    "ok": QStyle.StandardPixmap.SP_DialogApplyButton,
    "close": QStyle.StandardPixmap.SP_DialogCloseButton,
    "folder": QStyle.StandardPixmap.SP_DirIcon,
    "file": QStyle.StandardPixmap.SP_FileIcon,
    "arrow_down": QStyle.StandardPixmap.SP_ArrowDown,
    "arrow_up": QStyle.StandardPixmap.SP_ArrowUp,
    "computer": QStyle.StandardPixmap.SP_ComputerIcon,
}


def std_icon(name: str, widget=None) -> Optional[QIcon]:
    """返回语义图标的 QIcon；无映射返回 None（调用方保留文本回退）。

    widget 提供 style()（通常传按钮自身），保证与当前主题/样式一致。
    """
    pm = _STD_ICONS.get(name)
    if pm is None:
        return None
    try:
        style = widget.style() if widget is not None else None
        if style is not None:
            return style.standardIcon(pm)
        from PyQt6.QtWidgets import QApplication
        return QApplication.style().standardIcon(pm)
    except Exception:
        return None

# 主题色板集合（G05-1 多主题）
# Deep = 默认 GitHub 深色；Light = 浅色；Nord = 北欧风格
PALETTES = {
    "deep": {
        "bg": "#0d1117",
        "panel": "#161b22",
        "panel2": "#1c2128",
        "border": "#30363d",
        "accent": "#58a6ff",
        "accent2": "#3fb950",
        "warning": "#d29922",
        "error": "#f85149",
        "text": "#c9d1d9",
        "text_dim": "#8b949e",
        "console": "#0a0c10",
        "console_fg": "#d0d7de",
    },
    "light": {
        "bg": "#ffffff",
        "panel": "#f6f8fa",
        "panel2": "#eef1f4",
        "border": "#d0d7de",
        "accent": "#0969da",
        "accent2": "#1a7f37",
        "warning": "#9a6700",
        "error": "#cf222e",
        "text": "#1f2328",
        "text_dim": "#656d76",
        "console": "#fbfbfb",
        "console_fg": "#24292f",
    },
    "nord": {
        "bg": "#2e3440",
        "panel": "#3b4252",
        "panel2": "#434c5e",
        "border": "#4c566a",
        "accent": "#88c0d0",
        "accent2": "#a3be8c",
        "warning": "#ebcb8b",
        "error": "#bf616a",
        "text": "#d8dee9",
        "text_dim": "#7b88a1",
        "console": "#232830",
        "console_fg": "#d8dee9",
    },
    # G46-7 高对比（HC）主题：WCAG AAA（文本/背景 ≥7:1）
    "hc": {
        "bg": "#000000",
        "panel": "#0b0b0b",
        "panel2": "#1a1a1a",
        "border": "#ffffff",
        "accent": "#00e5ff",
        "accent2": "#7cff6b",
        "warning": "#ffe14d",
        "error": "#ff6b6b",
        "text": "#ffffff",
        "text_dim": "#e0e0e0",
        "console": "#000000",
        "console_fg": "#ffffff",
    },
}

# G46-1 设计 token 表：三主题共用布局 token（仅色板不同）
TOKENS: dict[str, dict[str, object]] = {
    "radius": {"card": 8, "control": 6, "bar": 5, "tab": 8, "checkbox": 4, "chip": 10},
    "spacing": {"xs": 4, "sm": 6, "md": 8, "lg": 12, "xl": 18},
    "border": {"default": 1, "focus": 2},
    # Qt QSS 不支持 box-shadow / transition：以 border/outline + hover 状态色表达反馈
    "shadow": {"focus": "outline"},
}

# G46-5 字体栈（中文优先）+ UI 层字号下限（qss_for_scale 底层仍保持 [0.8,1.6] 兼容旧测试）
FONT_STACK = '"Microsoft YaHei UI", "PingFang SC", "Noto Sans CJK SC", sans-serif'
FONT_SCALE_MIN_UI = 0.9

# G46-7 强调色预设（3 档，覆盖 accent/accent2）
ACCENT_PRESETS = {
    "blue": {"accent": "#0969da", "accent2": "#1a7f37"},
    "violet": {"accent": "#8250df", "accent2": "#1a7f37"},
    "teal": {"accent": "#007f7f", "accent2": "#1a7f37"},
}


def theme_with_accent(palette: dict, preset) -> dict:
    """G46-7：返回换用指定强调色后的色板副本（key 取 ACCENT_PRESETS 或直接 dict）。"""
    pal = dict(palette)
    if isinstance(preset, dict):
        pal.update({k: v for k, v in preset.items() if k in ("accent", "accent2")})
    else:
        p = ACCENT_PRESETS.get(preset)
        if p:
            pal.update(p)
    return pal


def _luminance(hex_color: str) -> float:
    """WCAG 相对亮度。"""
    c = QColor(hex_color)
    rgb = [v / 255.0 for v in (c.red(), c.green(), c.blue())]
    def _f(v):
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * _f(rgb[0]) + 0.0722 * _f(rgb[1]) + 0.7152 * _f(rgb[2])


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


def _build_qss(palette: dict, scale: float = 1.0, motion: bool = True) -> str:
    """G46-1：由色板 + 设计 token 统一生成 QSS（三主题共用布局，仅换色板）。

    - 状态色：hover 用 accent，disabled 用 text_dim/border，pressed 用 panel2 变暗；
    - motion=False（reduced-motion）时去掉 hover/pressed 颜色变化块（Qt QSS 无 transition）；
    - scale 钳制 [0.8, 1.6]（与旧测试兼容）；UI 层下限 FONT_SCALE_MIN_UI 由设置页负责。
    """
    try:
        scale = float(scale)
    except Exception:
        scale = 1.0
    scale = max(0.8, min(1.6, scale))
    px = round(FONT_BASE_PX * scale)
    bg = palette["bg"]
    panel = palette["panel"]
    panel2 = palette["panel2"]
    border = palette["border"]
    accent = palette["accent"]
    err = palette["error"]
    text = palette["text"]
    dim = palette["text_dim"]
    cons = palette["console"]
    cons_fg = palette["console_fg"]
    r_card = TOKENS["radius"]["card"]
    r_ctl = TOKENS["radius"]["control"]
    r_bar = TOKENS["radius"]["bar"]
    r_tab = TOKENS["radius"]["tab"]
    r_cb = TOKENS["radius"]["checkbox"]
    b_def = TOKENS["border"]["default"]
    b_fmt = TOKENS["border"]["focus"]
    pressed = _shade(panel2, 118)
    hover_bg = _shade(panel2, 108)
    # G55 engineering item: split oversize template lines via locals (same values).
    alt_bg = _shade(panel, 125) if palette.get("bg") == "#0d1117" else _shade(panel, 96)
    danger_qss = (f"QPushButton#danger {{ background: {_shade(err, 260)}; "
                  f"border-color: {_shade(err, 160)}; color: {err}; }}")
    if not motion:
        hover_motion = ""
    else:
        hover_motion = (
            f"QPushButton:hover {{ background: {hover_bg}; border-color: {accent}; }}"
        )
        pressed_motion = f"QPushButton:pressed {{ background: {pressed}; }}"
    style = f"""
QWidget {{ background: {bg}; color: {text};
            font-size: {px}px; font-family: {FONT_STACK}; }}
QMainWindow, QDialog {{ background: {bg}; }}
QLabel#pageTitle {{ font-size: 20px; font-weight: 700; color: {text}; }}
QLabel#accent {{ color: {accent}; font-weight: 600; }}
QLabel#muted {{ color: {dim}; }}
QGroupBox {{
  border: {b_def}px solid {border}; border-radius: {r_card}px;
  margin-top: 12px; padding-top: 8px; background: {panel};
  font-weight: 600; color: {text};
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
QPlainTextEdit, QTextEdit, QLineEdit, QSpinBox, QComboBox {{
  background: {panel2}; border: {b_def}px solid {border};
  border-radius: {r_ctl}px; padding: 6px; selection-background-color: {accent};
}}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
  border: {b_fmt}px solid {accent};
}}
QLineEdit:disabled, QSpinBox:disabled, QComboBox:disabled, QPlainTextEdit:disabled {{
  color: {dim}; background: {panel};
}}
QPushButton {{
  background: {panel2}; border: {b_def}px solid {border};
  border-radius: {r_ctl}px; padding: 6px 14px; color: {text};
}}
QPushButton:focus {{ border: {b_fmt}px solid {accent}; }}
QPushButton:disabled {{ color: {dim}; border-color: {border}; background: {panel}; }}
{hover_motion}
{pressed_motion if motion else ""}
QPushButton#primary {{
  background: {accent}; border: {b_def}px solid {accent}; color: #ffffff; font-weight: 700;
}}
QPushButton#primary:disabled {{ background: {_shade(accent, 200)}; color: {dim}; }}
QPushButton#primary:hover {{ background: {_shade(accent, 108)}; }}
{danger_qss}
QPushButton#danger:hover {{ background: {_shade(err, 200)}; }}
QTableWidget {{
  background: {panel}; alternate-background-color: {alt_bg};
  gridline-color: {border}; border: {b_def}px solid {border}; border-radius: {r_ctl}px;
}}
QHeaderView::section {{
  background: {panel2}; color: {text}; border: none;
  border-right: {b_def}px solid {border}; padding: 6px; font-weight: 700;
}}
QTextEdit#console {{
  background: {cons}; color: {cons_fg};
  font-family: Consolas, "Courier New", monospace; font-size: 12px;
  border: {b_def}px solid {border}; border-radius: {r_ctl}px;
}}
QTextEdit#pane {{
  background: {cons}; color: {cons_fg};
  font-family: Consolas, "Courier New", monospace; font-size: 12px;
  border: {b_def}px solid {border}; border-radius: {r_ctl}px;
}}
QProgressBar {{
  border: {b_def}px solid {border}; border-radius: {r_bar}px;
  background: {panel2}; text-align: center; color: {text};
  font-size: 11px; min-height: 16px; max-height: 16px;
}}
QProgressBar::chunk {{ background: {accent}; border-radius: 4px; }}
QStatusBar {{ color: {dim}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: {b_def}px solid {border};
  border-radius: {r_cb}px; background: {panel2}; }}
QCheckBox::indicator:checked {{ background: {accent}; border-color: {accent}; }}
QScrollBar:vertical {{ background: {bg}; width: 12px; }}
QScrollBar::handle:vertical {{ background: {border}; border-radius: {r_bar}px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QTabs::pane {{ border: {b_def}px solid {border}; border-radius: {r_tab}px; background: {bg}; }}
QTabBar::tab {{ padding: 8px 18px; background: {panel}; color: {dim};
  border: {b_def}px solid {border}; border-bottom: none; }}
QTabBar::tab:selected {{ background: {panel2}; color: {text}; border-top: 2px solid {accent}; }}
QTabBar::tab:hover {{ color: {text}; }}
"""
    from . import qss_validate  # G55 engineering item: static QSS validator hook.
    qss_validate.validate_qss_or_raise(style)
    return style


def qss_for_theme(name: str, scale: float = 1.0, motion: bool = True) -> str:
    """G46-1：按主题名生成其 QSS（未知回退 deep）。"""
    pal = PALETTES.get(name) or PALETTES["deep"]
    return _build_qss(pal, scale, motion)


def qss_for_palette(palette: dict, scale: float = 1.0, motion: bool = True) -> str:
    """G46-7：按任意色板（可含强调色覆盖）生成 QSS。"""
    return _build_qss(palette, scale, motion)

# 当前激活主题
_current_theme = "deep"

# G10-1 字号缩放基线（QSS 里基础字体 px）
FONT_BASE_PX = 13


def clamp_scale_for_dpi(scale: float, dpi: float = 0.0) -> float:
    """G57-2 DPI 联动：DPI>=150% 时字号下限自动抬到 1.0（防小字号不可读）。

    scale 原比例 [0.8,1.6]；dpi<=0 表示未知（保持原值）。返回应用 DPI 规则后的比例。
    """
    try:
        s = float(scale)
    except (TypeError, ValueError):
        s = 1.0
    d = float(dpi or 0)
    if d >= 150.0:
        s = max(s, 1.0)
    return s


def qss_for_scale(scale: float = 1.0, motion: bool = True) -> str:
    """按当前主题与缩放生成 QSS（比例钳制 [0.8, 1.6]；motion=False 走 reduced-motion）。

    旧版仅替换深色模板字号；G46-1 起按当前主题色板生成，light/nord/hc 真正换色。
    G57-2：DPI>=150% 时下限抬到 1.0（clamp_scale_for_dpi）。
    """
    from PyQt6.QtGui import QColor as _QC  # noqa: F401  — _build_qss 内部已引用 QColor
    dpi = 0.0
    try:
        import os as _os
        if str(_os.environ.get("QT_QPA_PLATFORM", "")).lower() != "offscreen":
            from PyQt6.QtWidgets import QApplication
            app = QApplication.instance()
            if app is not None and app.primaryScreen() is not None:
                dpi = float(app.primaryScreen().devicePixelRatio() * 100.0)
    except Exception:
        dpi = 0.0
    return _build_qss(current_palette(), clamp_scale_for_dpi(scale, dpi), motion)


def apply_theme(name: str) -> None:
    """切换当前主题（未知回退 deep）。"""
    global _current_theme
    _current_theme = name if name in PALETTES else "deep"


def current_palette() -> dict:
    return PALETTES[_current_theme]


def current_theme() -> str:
    return _current_theme


# 主题名 → 中文标签（设置页下拉选项）
THEMES = {
    "deep": "深色 (Deep)",
    "light": "浅色 (Light)",
    "nord": "Nord 极简",
    "hc": "高对比 (HC)",
}

# 兼容：默认主题 = Deep（历史引用 PALETTE 的代码不受影响）
PALETTE = PALETTES["deep"]

QSS = _build_qss(PALETTES["deep"])


class LogLevel(str, Enum):
    INFO = "info"
    WARN = "warn"
    ERROR = "error"
    SYSTEM = "system"


class LogEvent:
    """单条日志。"""

    __slots__ = ("time", "level", "text")

    def __init__(self, time: str, level: LogLevel, text: str):
        self.time = time
        self.level = level
        self.text = text


class LogModel(QObject):
    appended = pyqtSignal(int)  # 最大 3000 条

    def __init__(self, max_entries: int = 3000, parent=None):
        super().__init__(parent)
        self.max_entries = max_entries
        # G43-1①：deque(maxlen) O(1) 裁剪（替代 list 切片 O(n)），32 并发日志风暴不卡 UI
        self._entries: deque[LogEvent] = deque(maxlen=max_entries)
        self._dropped = 0

    def append(self, time: str, level: LogLevel, text: str):
        entry = LogEvent(time, level, text)
        if len(self._entries) >= self.max_entries:
            self._dropped += 1  # deque 满员：队首被挤出
        self._entries.append(entry)
        self.appended.emit(len(self._entries))

    def append_many(self, entries):
        """批量追加多条（只 emit 一次，用于日志洪峰节流合并）。"""
        if not entries:
            return
        before = len(self._entries)
        self._entries.extend(entries)
        dropped = max(0, before + len(entries) - self.max_entries)
        self._dropped += dropped
        self.appended.emit(len(self._entries))

    def _trim(self) -> tuple:
        """裁剪超限条目，返回 (dropped, 现条数)（deque 恒 ≤ maxlen，保留兼容）。"""
        return self._dropped, len(self._entries)

    def clear(self):
        self._entries.clear()
        self.appended.emit(0)

    def snapshot(self) -> list[LogEvent]:
        return list(self._entries)

    def to_plain_text(self) -> str:
        return "\n".join(f"[{e.time}] [{e.level}] {e.text}" for e in self._entries)


def make_highlighter(document):
    """构造控制台关键字高亮器。"""
    class _HL(QSyntaxHighlighter):
        def __init__(self, doc):
            super().__init__(doc)
            self.specs = [
                (re.compile(r"^\[(成功|完成|SUCCESS|OK|CLONED|UPDATED|FETCHED).*$", re.I),
                 PALETTE["accent2"], True),
                (re.compile(r"^\[(失败|错误|ERROR|FAILED|FAIL|CONFLICT).*$", re.I),
                 PALETTE["error"], True),
                (re.compile(r"^\[(警告|WARN).*$", re.I), PALETTE["warning"], True),
                (re.compile(r"https?://\S+"), PALETTE["accent"], False),
                (re.compile(
                     r"^.*\b(Cloning into|remote:|Receiving objects:|"
                     r"Resolving deltas:|Counting objects:|Compressing objects:|"
                     r"Updating files:|Unpacking objects:|Fetching|From |"
                     r"Fast-forward|Already up to date|Total \d+)\b.*$", re.I),
                 PALETTE["text_dim"], False),
            ]

        def highlightBlock(self, text):
            for pattern, color, bold in self.specs:
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(color))
                if bold:
                    fmt.setFontWeight(QFont.Weight.Bold)
                for m in pattern.finditer(text):
                    self.setFormat(m.start(), m.end() - m.start(), fmt)

    return _HL(document)


def maybe_validate_url(text: str) -> Optional[str]:
    """供 UI 校验：返回错误提示或 None。"""
    from ..app.url_lib import is_github_url
    if not text.strip():
        return "地址为空"
    if not is_github_url(text):
        return "不是有效的 GitHub 仓库地址"
    return None
