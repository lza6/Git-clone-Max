"""G36-9 跟随系统深浅色：纯逻辑层测试（全 mock，不读真实注册表）。

覆盖 detect_system_light_theme / map_system_to_theme：
- reg 注入 1 / 0 / None → True / False / None
- reg 抛异常（OSError / FileNotFoundError）→ None
- 组合：reg=1 → detect → map 得 "light"
- 非 Windows 上默认实现返回 None（平台隔离，不真实调用 winreg）
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.system_theme import detect_system_light_theme, map_system_to_theme  # noqa: E402


class TestDetectSystemLightTheme(unittest.TestCase):
    """detect_system_light_theme：reg 可注入；未知/失败 → None。"""

    def test_reg_returns_1_is_light(self):
        # 系统为浅色（AppsUseLightTheme = 1）
        self.assertIs(detect_system_light_theme(reg=lambda: 1), True)

    def test_reg_returns_0_is_dark(self):
        # 系统为深色（AppsUseLightTheme = 0）
        self.assertIs(detect_system_light_theme(reg=lambda: 0), False)

    def test_reg_returns_none_is_unknown(self):
        # 无法判定 → None（调用方保持现状不切换）
        self.assertIsNone(detect_system_light_theme(reg=lambda: None))

    def test_reg_raises_oserror_is_unknown(self):
        # 注册表读取失败 → None
        def boom() -> int:
            raise OSError("reg access denied")

        self.assertIsNone(detect_system_light_theme(reg=boom))

    def test_reg_raises_filenotfound_is_unknown(self):
        # 键不存在 → None
        def boom() -> int:
            raise FileNotFoundError("no such key")

        self.assertIsNone(detect_system_light_theme(reg=boom))

    def test_default_no_reg_calls_winreg_on_windows(self):
        # 不注入 reg 时：Windows 走 winreg，非 Windows 返回 None。
        # 用 mock.patch 掩盖真实平台，验证 winreg 调用路径本身（不碰真实注册表）。
        fake_winreg = mock.MagicMock()
        fake_winreg.OpenKey.return_value.__enter__.return_value = object()
        fake_winreg.QueryValueEx.return_value = (1, 1)
        with mock.patch.dict(sys.modules, {"winreg": fake_winreg}), \
                mock.patch("sys.platform", "win32"):
            self.assertIs(detect_system_light_theme(), True)
            fake_winreg.OpenKey.assert_called_once()

    def test_default_no_reg_non_windows_returns_none(self):
        # 非 Windows 平台：不碰 winreg（winreg 不会在函数外被加载），返回 None
        with mock.patch("sys.platform", "linux"):
            # 模块顶层不应暴露 winreg（函数内 import 的平台隔离保证）
            self.assertFalse(hasattr(sys.modules["gcm.app.system_theme"], "winreg"))
            self.assertIsNone(detect_system_light_theme())


class TestMapSystemToTheme(unittest.TestCase):
    """map_system_to_theme：1→light、0→deep、None→空（不切换）。"""

    def test_light_flag_maps_to_light(self):
        self.assertEqual(map_system_to_theme(1), "light")

    def test_dark_flag_maps_to_deep(self):
        self.assertEqual(map_system_to_theme(0), "deep")

    def test_unknown_maps_to_empty(self):
        self.assertEqual(map_system_to_theme(None), "")


class TestCombined(unittest.TestCase):
    """组合：detect 结果喂给 map，应得到正确主题名。"""

    def test_system_light_leads_to_light_theme(self):
        result = map_system_to_theme(detect_system_light_theme(reg=lambda: 1))
        self.assertEqual(result, "light")

    def test_system_dark_leads_to_deep_theme(self):
        result = map_system_to_theme(detect_system_light_theme(reg=lambda: 0))
        self.assertEqual(result, "deep")

    def test_unknown_leads_to_no_change(self):
        result = map_system_to_theme(detect_system_light_theme(reg=lambda: None))
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
