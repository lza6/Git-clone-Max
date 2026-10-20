"""G70 窗口尺寸断点与对话框适配（纯逻辑，可无 GUI 单测）。

断点按**逻辑像素**（Qt 已做 DPI 缩放）定义。桌面端不追求手机断点；
最低目标是让 1366×768@125%（约 1093×614）与 1920×1080@150%（约 1280×680）
这两类主流办公环境可用。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    from PyQt6.QtWidgets import QWidget

WINDOW_DEFAULT: tuple[int, int] = (1240, 840)
# G70：原 (1020, 700) 在 1366×768@125% / 1080p@150% 下放不下
WINDOW_MIN: tuple[int, int] = (1020, 560)
COMPACT_WIDTH = 1100
COMPACT_HEIGHT = 820
WIDE_WIDTH = 1440
WIDE_HEIGHT = 1000
ABSOLUTE_MIN: tuple[int, int] = (900, 480)


def layout_mode(width: int, height: int) -> str:
    """返回 unsupported / compact / standard / wide。"""
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError):
        return "standard"
    if w < WINDOW_MIN[0] or h < WINDOW_MIN[1]:
        return "unsupported"
    if w < COMPACT_WIDTH or h < COMPACT_HEIGHT:
        return "compact"
    if w >= WIDE_WIDTH and h >= WIDE_HEIGHT:
        return "wide"
    return "standard"


def effective_minimum(avail_w: int, avail_h: int) -> tuple[int, int]:
    """按屏幕工作区推导实际可用最小尺寸（小屏不再被 1020×560 卡死）。"""
    try:
        aw, ah = int(avail_w), int(avail_h)
    except (TypeError, ValueError):
        return WINDOW_MIN
    if aw <= 0 or ah <= 0:
        return WINDOW_MIN
    return (min(WINDOW_MIN[0], max(ABSOLUTE_MIN[0], int(aw * 0.90))),
            min(WINDOW_MIN[1], max(ABSOLUTE_MIN[1], int(ah * 0.85))))


def clamp_window_size(width: int, height: int, avail_w: int, avail_h: int
                      ) -> tuple[int, int]:
    """把恢复的窗口尺寸夹到屏幕工作区内（位置由调用方另行处理）。"""
    min_w, min_h = effective_minimum(avail_w, avail_h)
    try:
        w, h = int(width), int(height)
    except (TypeError, ValueError):
        w, h = WINDOW_DEFAULT
    max_w = max(min_w, int(int(avail_w) * 0.95))
    max_h = max(min_h, int(int(avail_h) * 0.95))
    return (max(min_w, min(w, max_w)), max(min_h, min(h, max_h)))


def fit_dialog_to_screen(widget: QWidget, pref_w: int, pref_h: int,
                         min_w: int = 460, min_h: int = 320) -> tuple[int, int]:
    """按当前屏幕工作区缩放对话框（90% 宽 / 85% 高）。

    返回最终逻辑尺寸，便于测试断言。无 QApplication（纯逻辑测试）时直接
    resize 到偏好尺寸。
    """
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance()
    screen = widget.screen() if widget is not None else None
    if screen is None and app is not None:
        screen = app.primaryScreen()
    if screen is None:
        widget.resize(pref_w, pref_h)
        return (pref_w, pref_h)
    avail = screen.availableGeometry()
    max_w = max(320, int(avail.width() * 0.90))
    max_h = max(240, int(avail.height() * 0.85))
    w = min(int(pref_w), max_w)
    h = min(int(pref_h), max_h)
    widget.setMinimumSize(min(int(min_w), max_w), min(int(min_h), max_h))
    widget.resize(max(w, widget.minimumWidth()), max(h, widget.minimumHeight()))
    return (w, h)
