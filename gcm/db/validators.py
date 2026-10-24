"""设置项的**声明期取值校验**（G80-B5）。

为什么需要：64 个设置字段只有类型标注，**没有取值校验**。UI 控件的 `setRange`
只是一层"软约束"——直接编辑 `settings.json`、旧版本留下的脏值、或别的写入路径
都能绕过它，于是「并发 999」「clone 超时 -5」这种值会一路传到引擎。
这里把"合法区间"声明成唯一真相，**UI 的上下限也从这里派生**，
保证不会出现「UI 说最多 32、后端却允许 999」的双口径。

行为约定（刻意宽松，兼容旧配置）：**越界不抛异常**，纠正到最近的合法值；
需要提示时由调用方（设置页/启动日志）决定，不在纯逻辑里弹窗。
"""
from __future__ import annotations

from typing import Any

# 只收三类约束：整数区间 / 枚举 / 非空字符串。字段名与 Settings dataclass 一致。
_INT_RANGES: dict[str, tuple[int, int]] = {
    "concurrency": (1, 32),
    "clone_timeout": (30, 7200),
    "retry_backoff_base_sec": (5, 600),
}

# 只读视图（UI 的 setRange 从这里取，避免两套限制）
RANGES = _INT_RANGES


def int_range(key: str) -> tuple[int, int]:
    """该字段的合法区间（无约束时抛 KeyError，逼调用方别乱用）。

    UI 的 `setRange(*int_range("concurrency"))` 直接从这里派生 —— 单一真相。
    """
    return _INT_RANGES[key]


def clamp_int(key: str, value: Any) -> tuple[Any, bool]:
    """把值纠正到合法区间。返回 (纠正后的值, 是否被纠正过)。

    无法解析成 int 时返回**默认下界**并标记纠正（保守：宁可小也不能大）。
    """
    bounds = _INT_RANGES.get(key)
    if bounds is None:
        return (value, False)
    lo, hi = bounds
    try:
        n = int(value)
    except (TypeError, ValueError):
        return (lo, True)
    if n < lo:
        return (lo, True)
    if n > hi:
        return (hi, True)
    return (n, False)


def normalize(key: str, value: Any) -> tuple[Any, bool]:
    """统一入口：目前只有整数区间；未来加枚举/字符串约束时只改这里。"""
    if key in _INT_RANGES:
        return clamp_int(key, value)
    return (value, False)


def normalize_settings(settings: Any) -> list[str]:
    """对一个 Settings 对象就地纠正所有受约束字段，返回**被纠正的字段名列表**。

    就地修改是刻意的：调用方在 `SettingsStore.load()` 尾部调用它，
    这样**任何来源**（UI 保存、手改 JSON、旧版本脏值）都过同一道闸。
    """
    changed: list[str] = []
    for key in _INT_RANGES:
        if not hasattr(settings, key):
            continue
        current = getattr(settings, key)
        new, corrected = normalize(key, current)
        if corrected:
            try:
                setattr(settings, key, new)
                changed.append(key)
            except (AttributeError, TypeError):
                continue
    return changed
