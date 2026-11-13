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
# Rev13 R13-03：窄窗阈值必须**可达**。原实现写成 `w < 768`，而 `effective_minimum()`
# 的下限是 900（`ABSOLUTE_MIN`）—— 那个分支永远进不去。这里取 `WINDOW_MIN[0]`，
# 即「比声明的最小宽度还窄」才算窄，与真实可达范围一致。
NARROW_WIDTH: int = WINDOW_MIN[0]


def should_fold_tools(top_available: int, top_needed: int) -> bool:
    """Rev13 R13-02：顶部区放不下「结论条 + 可滚动区 + 两行动作区」时折叠工具行。

    判定用**实测算出的需求 vs 可分到的空间**，不写死窗口高度 —— 因为
    菜单栏、状态栏、页签栏、字号缩放都会改变同一窗口高度下的可用空间。
    实测基线（字号 1.0）：窗口 584 高时顶部区可分 243px、需求 238px（不折叠）；
    窗口 504 高时只分到 180px（必须折叠）。
    """
    try:
        return int(top_available) < int(top_needed)
    except (TypeError, ValueError):
        return False


# 「两行动作区完整可见」所需的**窗口高度**（实测，含 18px 菜单栏）。
# Rev13 首测：580 / 600 / 620（字号 1.0/1.3/1.6），斜率约 66.7 px 每 0.1 档。
# **Rev14 F1/F7 重测抬高 +70**：把「下载位置/克隆模式」从可滚动区搬成常驻行后，
# 顶部容器固定高 +70，同一个窗口高度下留给动作区的空间少了 70 → 折叠必须更早触发，
# 否则工具行会被裁（实测 1216×684 下 btn_open 只剩 16/42）。
# 这次抬高是**用滚动换常驻**的必然代价：搬出来的两个控件原先被裁到 6px（更严重）。
TOOLS_FOLD_HEIGHT_BASE = 790
TOOLS_FOLD_HEIGHT_SLOPE = 66.7


def tools_fold_height(font_scale: float = 1.0) -> int:
    """窗口高度低于此值 → 把工具行折叠进「更多 ▸」（R13-02 的判定口径）。

    为什么按**窗口高度**判而不是按顶部区剩余空间：Qt 的 splitter 分配发生在
    `_apply_layout_mode` 之后，读到的尺寸是上一帧的；而窗口高度在本函数内
    就是**当前**值，不会有一帧滞后。

    数值来自逐 10px 扫描（字号 1.0 / 1.3 / 1.6 首次不裁切的高度 = **720 / 740 / 760**）。
    Rev14 F1 把「下载位置/克隆模式」搬出滚动区后顶部常驻区 +140px，阈值随之抬高。
    **不加余量**：1020×560 的实际窗口是 584，584 < 720 会进入折叠态 —— 这正是设计
    （折叠后常驻按钮才完整可见），`test_ui_compact_visibility` 已按「折叠也算通过、
    但入口必须在「更多 ▸」菜单里」的口径断言。
    """
    try:
        s = float(font_scale)
    except (TypeError, ValueError):
        s = 1.0
    s = max(0.8, min(1.6, s))
    return int(round(TOOLS_FOLD_HEIGHT_BASE + (s - 1.0) * TOOLS_FOLD_HEIGHT_SLOPE))


# Rev14 R14-01：四档断点语义。阈值**复用**上面的常量，不另立一套 ——
# `layout_mode()` 继续按原值返回（既有测试与调用方不受影响），
# `breakpoint_name()` 只是把 "unsupported" 读成 XS 并补上 L 档的消费语义。
BREAKPOINT_XS = "XS"     # 受支持下限：只能由 effective_minimum() 按屏幕工作区夹出
BREAKPOINT_S = "S"       # 小笔记本 / 分屏窄窗
BREAKPOINT_M = "M"       # 主流办公
BREAKPOINT_L = "L"       # 大屏 / 竖屏副显
# XS 的高度分界 = **声明的最小窗口高度**语义下的高度门槛（580 = WINDOW_MIN[1] + 余量）。
# **不要再引用 `TOOLS_FOLD_HEIGHT_BASE`**：Rev14 F1 把折叠阈值从 580 抬到 720 之后，
# 这种引用会让 XS 档的语义被静默改成「h < 720」——1020×584 / 1240×729 / 1366×768
# 会一起从 S 掉进 XS，而它们明明是「小笔记本」而不是「低于受支持下限」。
# 折叠阈值回答「动作行放不放得下」，XS 回答「这个窗口还受不受支持」，两件事。
XS_MAX_HEIGHT = 580


def coerce_int(value: object, fallback: int) -> int:
    """把 `int / float / 数字字符串` 转成 int，坏输入返回 `fallback`。

    用它而不是 `try/except ValueError`：断点计算在每次 resize 的路径上，
    用类型判定代替异常控制流，也省掉一个异常子句（异常预算棘轮只降不升）。
    """
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else fallback
    if isinstance(value, str):
        text = value.strip()
        if text.lstrip("+-").isdigit():
            return int(text)
    return fallback


def breakpoint_name(width: int, height: int) -> str:
    """返回 XS / S / M / L（Rev14 R14-01）。

    判定顺序与 `layout_mode()` 完全一致，只是把 `unsupported` 命名为 XS：
    - XS：宽 < 1020 **或** 高 < 580 —— 正常拖拽到不了（最小宽 900 会被
      `effective_minimum` 夹到 900/480~504），只有屏幕工作区太小才会落进来；
    - S：宽 < 1100 **或** 高 < 820 —— 1366×768（1240×729）、1020×584 都在这档；
    - L：宽 ≥ 1440 **且** 高 ≥ 1000（`WIDE_WIDTH/WIDE_HEIGHT`）；
    - M：其余。

    **L 档必须有真实行为差异**，否则它就只是 `layout_mode()` 里那个
    「返回了但全库没人消费」的死枚举（Rev13 R4）。
    """
    w = coerce_int(width, WINDOW_MIN[0])
    h = coerce_int(height, WINDOW_MIN[1])
    if w < WINDOW_MIN[0] or h < XS_MAX_HEIGHT:
        return BREAKPOINT_XS
    if w < COMPACT_WIDTH or h < COMPACT_HEIGHT:
        return BREAKPOINT_S
    if w >= WIDE_WIDTH and h >= WIDE_HEIGHT:
        return BREAKPOINT_L
    return BREAKPOINT_M


def is_compact_bp(name: str) -> bool:
    """XS/S 两档共用「紧凑」行为（隐快捷填充、隐设置导航、结论条单行）。"""
    return str(name) in (BREAKPOINT_XS, BREAKPOINT_S)


def resolve_screen(screens: list[dict], wanted: str = "") -> dict:
    """按屏幕名挑一块屏；挑不到回落到列表第一块（Rev14 R14-02 的纯函数内核）。

    `screens`：`[{"name": "\\\\.\\DISPLAY1", "available": (w, h)}, ...]`，
    由调用方从 `QGuiApplication.screens()` 取；纯函数便于注入假屏幕做单测。

    返回 `{"name", "available", "fell_back"}`。`fell_back=True` 表示
    「上次所在的屏已经不在了」（拔屏 / 远程桌面 / 改分辨率）—— 调用方必须记
    一条 WARN，不能静默。
    """
    if not screens:
        return {"name": "", "available": (0, 0), "fell_back": False}
    wanted = str(wanted or "")
    for s in screens:
        if str(s.get("name") or "") == wanted:
            aw, ah = s.get("available") or (0, 0)
            return {"name": wanted, "available": (int(aw), int(ah)),
                    "fell_back": False}
    aw, ah = screens[0].get("available") or (0, 0)
    return {"name": str(screens[0].get("name") or ""),
            "available": (int(aw), int(ah)),
            "fell_back": bool(wanted)}


def minimum_for_screen(screens: list[dict], wanted: str = "") -> tuple[int, int]:
    """某块屏上的最小窗口尺寸 = `effective_minimum(该屏工作区)`。"""
    resolved = resolve_screen(screens, wanted)
    aw, ah = resolved["available"]
    if aw <= 0 or ah <= 0:
        return WINDOW_MIN
    return effective_minimum(aw, ah)


def has_touchscreen() -> bool:
    """当前是否有触摸屏输入设备（Rev14 R14-07 的行高下限判据）。

    **必须排除 offscreen**：Qt 的离屏平台会伪造一个 TouchScreen 设备 ——
    实测 `QApplication` 建好之后 `QInputDevice.devices()` 就返回一个
    `DeviceType.TouchScreen`。不排除的话所有离屏测试都会跑在"触摸档"下，
    行高断言全部失真。

    无 `QApplication`（纯逻辑测试）返回 False；不包 `try/except` ——
    `QInputDevice.devices()` 在各平台都是静态查询，没有可失败路径
    （异常预算棘轮只降不升）。
    """
    from PyQt6.QtGui import QInputDevice
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance()
    if app is None or str(app.platformName()).lower() == "offscreen":
        return False
    return any(d.type() == QInputDevice.DeviceType.TouchScreen
               for d in QInputDevice.devices())


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
