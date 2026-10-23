"""UI 通用：日志事件模型 / 文本高亮 / 主题状态 / 样式再导出。

G75 起本模块**不再包含**色板数据与 QSS 拼装（原 598 行，加新 token 必破 800 行上限）：

| 关注点 | 现在在哪 |
|--------|---------|
| 字族与字号阶梯 | `gcm/ui/typography.py` |
| 色板与设计 token | `gcm/ui/palettes.py` |
| QSS 拼装 | `gcm/ui/qss_build.py` |
| 主题状态（当前主题/缩放）+ 日志模型 + 高亮 | **本文件**（并原样再导出上面三个模块的符号）|

**兼容契约**：任何 `from .theme import X` 的旧写法都必须继续可用 —— 本轮清点出 22 个被外部
导入的符号，全部在下面再导出。改这里之前先跑 `tests/test_g46_theme.py`、`tests/test_themes.py`。
"""
from __future__ import annotations

import re
from collections import deque
from enum import Enum
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QIcon, QSyntaxHighlighter, QTextCharFormat
from PyQt6.QtWidgets import QStyle  # QStyle.StandardPixmap 在 QtWidgets 命名空间

# --- 再导出（G75 拆分后的唯一兼容面，勿删）-----------------------------------
from .palettes import (  # noqa: F401
    ACCENT_PRESETS,
    PALETTES,
    TOKENS,
    _luminance,
    _on_accent_for,
    _shade,
    contrast_ratio,
    hc_contrast_ratio,
    theme_with_accent,
)
from .qss_build import (  # noqa: F401
    _build_qss,
    qss_for_palette,
    qss_for_theme,
)
from .typography import (  # noqa: F401
    FONT_BASE_PX,
    FONT_SCALE_MIN_UI,
    FONT_STACK,
    FONT_STACK_DISPLAY,
    FONT_STACK_MONO,
    TYPE_SCALE,
    apply_type,
    role_stack,
    stack_for,
    type_font,
    type_px,
)

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


def apply_parent_theme(widget) -> None:
    """把父窗口当前 QSS 显式套到独立对话框（G74）。

    Qt 对带 parent 的顶层 QDialog 的样式继承在部分平台/样式下不可靠
    （实测诊断对话框仍是白底）。这里沿 parent 链找到带 `_themed_qss()`
    的主窗口，显式 setStyleSheet，保证对话框与主窗主题/字号一致。
    """
    try:
        parent = widget.parent()
        while parent is not None:
            fn = getattr(parent, "_themed_qss", None)
            if callable(fn):
                widget.setStyleSheet(fn())
                return
            parent = parent.parent()
    except Exception:
        pass


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


# 当前激活主题 / 强调色预设
_current_theme = "deep"
# G75：默认强调色 = Ink 的"示波器青"（旧默认是 Primer 蓝，会把整套配色拉回通用模板）
DEFAULT_ACCENT_PRESET = "teal"
_current_accent = DEFAULT_ACCENT_PRESET


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

    G57-2：DPI>=150% 时下限抬到 1.0（clamp_scale_for_dpi）。
    """
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
    return qss_for_palette(current_palette(), clamp_scale_for_dpi(scale, dpi), motion)


def apply_theme(name: str) -> None:
    """切换当前主题（未知回退 deep）。"""
    global _current_theme
    _current_theme = name if name in PALETTES else "deep"


def apply_accent_preset(name: str) -> None:
    """切换当前强调色预设（未知回退默认）。

    为什么要有模块级状态：自绘 delegate（进度条/状态 pill）、日志高亮、托盘图标都
    直接读 `current_palette()`。如果只有 QSS 应用了预设，就会**同一界面两种强调色**
    （G75 实测：批次进度条是旧蓝、行内进度条是新的青）。
    """
    global _current_accent
    _current_accent = name if name in ACCENT_PRESETS else DEFAULT_ACCENT_PRESET


def current_accent() -> str:
    return _current_accent


def current_palette() -> dict:
    """当前主题 + 当前强调色预设后的色板（所有绘制路径的唯一来源）。"""
    return theme_with_accent(PALETTES[_current_theme], _current_accent)


def current_theme() -> str:
    return _current_theme


# 主题名 → 中文标签（设置页下拉选项）
THEMES = {
    "deep": "墨黑 (Ink)",
    "light": "暖纸 (Paper)",
    "nord": "Nord 极简",
    "hc": "高对比 (HC)",
}

# 兼容：默认主题 = deep（历史引用 PALETTE 的代码不受影响）
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


_HL_SPECS = (
    (re.compile(r"^\[(成功|完成|SUCCESS|OK|CLONED|UPDATED|FETCHED).*$", re.I),
     "success", True),
    (re.compile(r"^\[(失败|错误|ERROR|FAILED|FAIL|CONFLICT).*$", re.I),
     "error", True),
    (re.compile(r"^\[(警告|WARN).*$", re.I), "warning", True),
    (re.compile(r"https?://\S+"), "accent", False),
    (re.compile(
        r"^.*\b(Cloning into|remote:|Receiving objects:|"
        r"Resolving deltas:|Counting objects:|Compressing objects:|"
        r"Updating files:|Unpacking objects:|Fetching|From |"
        r"Fast-forward|Already up to date|Total \d+)\b.*$", re.I),
     "text_dim", False),
)


def make_highlighter(document):
    """构造控制台关键字高亮器。

    G70：正则模块级编译一次；颜色按**当前主题**在每次高亮时读取，
    切换主题后无需重建高亮器也不会再固定成深色板。
    """

    class _HL(QSyntaxHighlighter):
        def __init__(self, doc):
            super().__init__(doc)
            self.specs = _HL_SPECS

        def highlightBlock(self, text):
            pal = current_palette()
            for pattern, key, bold in self.specs:
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(pal.get(key) or pal["text"]))
                if bold:
                    fmt.setFontWeight(QFont.Weight.Bold)
                for m in pattern.finditer(text):
                    self.setFormat(m.start(), m.end() - m.start(), fmt)

    return _HL(document)


def maybe_validate_url(text: str) -> Optional[str]:
    """单行地址校验：返回错误提示或 None。

    G69-18 修复：原实现用 `is_github_url()` —— **只认 GitHub**，而本工具支持
    GitLab / Gitee / Codeberg / Bitbucket / 自建主机。用它做输入校验会把合法地址
    判成非法。改用**与克隆链路同一个解析器** `parse_any_repo_url`，
    校验结论与「点了开始之后会不会真的克隆」保持一致。
    """
    from ..app.url_lib import parse_any_repo_url
    raw = (text or "").strip()
    if not raw:
        return "地址为空"
    if parse_any_repo_url(raw) is None:
        return "不是可识别的仓库地址（支持 GitHub/GitLab/Gitee/Codeberg/Bitbucket/自建主机）"
    return None
