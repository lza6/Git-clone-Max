"""Rev15：auto 主题必须即时解析，且不能把用户选择写成 light/deep。

历史缺陷：设置页选「跟随系统」后 `_save_theme` 把 "auto" 交给 `apply_theme`，
而 `apply_theme` 只认四套色板 → 静默退回 deep；用户看到的是“选了跟随系统却变黑”。
"""
from __future__ import annotations

import types
import unittest
from unittest import mock


class TestResolveThemeName(unittest.TestCase):
    def test_manual_theme_is_passthrough(self) -> None:
        from gcm.ui.theme import resolve_theme_name
        self.assertEqual(resolve_theme_name("light"), "light")
        self.assertEqual(resolve_theme_name("nord"), "nord")
        self.assertEqual(resolve_theme_name("unknown"), "deep")

    def test_auto_uses_injected_scheme(self) -> None:
        from gcm.ui.theme import resolve_theme_name
        self.assertEqual(resolve_theme_name("auto", scheme="light"), "light")
        self.assertEqual(resolve_theme_name("auto", scheme="dark"), "deep")

    def test_auto_falls_back_to_light_preference(self) -> None:
        from gcm.ui.theme import resolve_theme_name
        self.assertEqual(
            resolve_theme_name("auto", scheme="unknown", light_pref=True), "light")
        self.assertEqual(
            resolve_theme_name("auto", scheme="unknown", light_pref=False), "deep")
        with mock.patch("gcm.app.system_theme.detect_system_light_theme",
                        return_value=None):
            self.assertEqual(
                resolve_theme_name("auto", scheme="unknown", light_pref=None), "")


class TestSaveThemeAuto(unittest.TestCase):
    def test_auto_is_resolved_but_setting_stays_auto(self) -> None:
        from gcm.ui.settings_panel import SettingsPanel

        class _Combo:
            def itemData(self, _index):
                return "auto"

        owner = types.SimpleNamespace(
            theme_combo=_Combo(),
            settings=types.SimpleNamespace(theme="deep"),
            settings_store=types.SimpleNamespace(save=lambda _s: None),
            setStyleSheet=lambda _q: None,
            _themed_qss=lambda: "",
            _apply_theme_to_children=lambda: None,
            _rehighlight_log=lambda: None,
        )
        panel = types.SimpleNamespace(owner=owner)

        with mock.patch("gcm.ui.theme.resolve_theme_name", return_value="light"), \
                mock.patch("gcm.ui.theme.apply_theme") as apply:
            SettingsPanel._save_theme(panel, 0)

        self.assertEqual(owner.settings.theme, "auto",
                         "保存后必须仍是 auto，不能写成 light/deep")
        apply.assert_called_once_with("light")


if __name__ == "__main__":
    unittest.main()
