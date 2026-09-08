# -*- coding: utf-8 -*-
"""UI 通用：日志事件模型 / 文本高亮 / 样式常量。"""
from __future__ import annotations

import re
from enum import Enum
from typing import List, Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

# GitHub 风格深色主题色板（与 README 保持一致）
PALETTE = {
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
}

QSS = f"""
QWidget {{ background: {PALETTE['bg']}; color: {PALETTE['text']};
            font-size: 13px; font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif; }}
QMainWindow, QDialog {{ background: {PALETTE['bg']}; }}
QLabel#pageTitle {{ font-size: 20px; font-weight: 700; color: #e6edf3; }}
QLabel#accent {{ color: {PALETTE['accent']}; font-weight: 600; }}
QLabel#muted {{ color: {PALETTE['text_dim']}; }}
QGroupBox {{
  border: 1px solid {PALETTE['border']}; border-radius: 8px;
  margin-top: 12px; padding-top: 8px; background: {PALETTE['panel']};
  font-weight: 600; color: #e6edf3;
}}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px; }}
QPlainTextEdit, QTextEdit, QLineEdit, QSpinBox, QComboBox {{
  background: {PALETTE['panel2']}; border: 1px solid {PALETTE['border']};
  border-radius: 6px; padding: 6px; selection-background-color: #264f78;
}}
QPushButton {{
  background: {PALETTE['panel2']}; border: 1px solid {PALETTE['border']};
  border-radius: 6px; padding: 6px 14px; color: {PALETTE['text']};
}}
QPushButton:hover {{ border-color: {PALETTE['accent']}; color: #e6edf3; }}
QPushButton:disabled {{ color: #4d5563; border-color: #232a38; }}
QPushButton#primary {{
  background: #1f6feb; border: 1px solid #1f6feb; color: #fff; font-weight: 700;
}}
QPushButton#primary:hover {{ background: #388bfd; }}
QPushButton#primary:disabled {{ background: #1a3b63; color: #8ea6c9; }}
QPushButton#danger {{ background: #3d1d22; border-color: #7d3d44; color: #ffa198; }}
QPushButton#danger:hover {{ background: #512e35; }}
QTableWidget {{
  background: {PALETTE['panel']}; alternate-background-color: #10151d;
  gridline-color: {PALETTE['border']}; border: 1px solid {PALETTE['border']}; border-radius: 6px;
}}
QHeaderView::section {{
  background: {PALETTE['panel2']}; color: #e6edf3; border: none;
  border-right: 1px solid {PALETTE['border']}; padding: 6px; font-weight: 700;
}}
QTextEdit#console {{
  background: {PALETTE['console']}; color: {PALETTE['console_fg']};
  font-family: Consolas, "Courier New", monospace; font-size: 12px;
  border: 1px solid {PALETTE['border']}; border-radius: 6px;
}}
QTextEdit#pane {{
  background: {PALETTE['console']}; color: {PALETTE['console_fg']};
  font-family: Consolas, "Courier New", monospace; font-size: 12px;
  border: 1px solid {PALETTE['border']}; border-radius: 6px;
}}
QProgressBar {{
  border: 1px solid {PALETTE['border']}; border-radius: 5px;
  background: {PALETTE['panel2']}; text-align: center; color: {PALETTE['text']};
  font-size: 11px; min-height: 16px; max-height: 16px;
}}
QProgressBar::chunk {{ background: {PALETTE['accent']}; border-radius: 4px; }}
QStatusBar {{ color: {PALETTE['text_dim']}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {PALETTE['border']};
  border-radius: 4px; background: {PALETTE['panel2']}; }}
QCheckBox::indicator:checked {{ background: {PALETTE['accent']}; border-color: {PALETTE['accent']}; }}
QScrollBar:vertical {{ background: {PALETTE['bg']}; width: 12px; }}
QScrollBar::handle:vertical {{ background: {PALETTE['border']}; border-radius: 6px; min-height: 24px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QTabs::pane {{ border: 1px solid {PALETTE['border']}; border-radius: 8px; background: {PALETTE['bg']}; }}
QTabBar::tab {{ padding: 8px 18px; background: {PALETTE['panel']}; color: {PALETTE['text_dim']};
  border: 1px solid {PALETTE['border']}; border-bottom: none; }}
QTabBar::tab:selected {{ background: {PALETTE['panel2']}; color: #e6edf3; border-top: 2px solid {PALETTE['accent']}; }}
QTabBar::tab:hover {{ color: #e6edf3; }}
"""


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
        self._entries: List[LogEvent] = []
        self._dropped = 0

    def append(self, time: str, level: LogLevel, text: str):
        entry = LogEvent(time, level, text)
        self._entries.append(entry)
        if len(self._entries) > self.max_entries:
            self._entries = self._entries[-self.max_entries:]
            self._dropped += 1
        self.appended.emit(len(self._entries))

    def clear(self):
        self._entries.clear()
        self.appended.emit(0)

    def snapshot(self) -> List[LogEvent]:
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
                (re.compile(r"^.*\b(Cloning into|remote:|Receiving objects:|Resolving deltas:|Counting objects:|Compressing objects:|Updating files:|Unpacking objects:|Fetching|From |Fast-forward|Already up to date|Total \d+)\b.*$", re.I),
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