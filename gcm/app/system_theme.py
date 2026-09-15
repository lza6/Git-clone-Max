"""G36-9 跟随系统深浅色：纯逻辑层。

- detect_system_light_theme：检测 Windows 系统深浅色（注册表 Personalize\\AppsUseLightTheme）
- map_system_to_theme：系统深浅标志 → 项目主题名（light/deep/空=不切换）

不依赖 Qt；winreg 仅在 Windows 函数内 import（平台隔离）。
主控（main_window）负责把结果接到 apply_theme / settings.theme 联动。
"""
from __future__ import annotations

import sys

# 注册表根键：HKCU 下的主题个性化路径
_REG_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
# 应用深浅色值名：1=浅色，0=深色（REG_DWORD）
_REG_VALUE_NAME = "AppsUseLightTheme"


def detect_system_light_theme(reg=None) -> bool | None:
    """检测 Windows 系统是否浅色。

    - reg：可注入的 callable，返回 int（1 浅 / 0 深）或 None（未知）；
      默认实现用 winreg 读取当前平台注册表。
    - 返回 True（浅色）/ False（深色）/ None（未知，调用方保持现状不切换）。
    - 读取失败 / 非 Windows / 找不到键均返回 None。
    """
    if reg is not None:
        # 注入路径：测试与外部可自定义取值源
        try:
            value = reg()
        except Exception:
            return None
        if value is None:
            return None
        return bool(int(value))

    # 默认实现：仅 Windows 读取注册表
    if sys.platform != "win32":
        return None
    try:
        import winreg  # 平台隔离：非 Windows 不加载
    except ImportError:
        return None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_KEY_PATH) as key:
            value, _ = winreg.QueryValueEx(key, _REG_VALUE_NAME)
        return bool(int(value))
    except (OSError, FileNotFoundError, ValueError):
        # 键不存在 / 权限失败 / 值类型异常 → 未知
        return None


def map_system_to_theme(pref: int | None) -> str:
    """系统深浅标志 → 项目主题名。

    - 1 → "light"（系统浅色）
    - 0 → "deep"（系统深色）
    - None → ""（未知，空串=不切换）
    """
    if pref is None:
        return ""
    return "light" if pref else "deep"
