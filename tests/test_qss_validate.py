"""G55 工程与测试体系：QSS 静态校验器测试。

覆盖合法/非法 QSS 断言，以及 theme.py 集成：现有主题 QSS 必须通过校验。
"""
from __future__ import annotations

import unittest

from gcm.ui.qss_validate import validate_qss, validate_qss_or_raise


class TestValidateQss(unittest.TestCase):
    def test_valid_simple_block(self):
        self.assertEqual(validate_qss("QWidget { background: #fff; color: #000; }"), [])

    def test_valid_multiple_selectors(self):
        qss = "QPushButton, QLineEdit { border: 1px solid red; }\nQGroupBox::title { color: blue; }"
        self.assertEqual(validate_qss(qss), [])

    def test_valid_empty(self):
        self.assertEqual(validate_qss(""), ["QSS 为空"])

    def test_unbalanced_braces(self):
        errs = validate_qss("QWidget { background: #fff;")
        self.assertTrue(any("大括号" in e for e in errs))

    def test_unbalanced_extra_close(self):
        errs = validate_qss("QWidget {} }")
        self.assertTrue(any("大括号" in e for e in errs))

    def test_unbalanced_parens(self):
        errs = validate_qss("QWidget { background: url( #fff; }")
        self.assertTrue(any("圆括号" in e for e in errs))

    def test_unclosed_quote(self):
        errs = validate_qss('QWidget { font-family: "Arial; }')
        self.assertTrue(any("引号" in e for e in errs))

    def test_empty_selector(self):
        errs = validate_qss("  { background: #fff; }")
        self.assertTrue(any("选择器为空" in e for e in errs))

    def test_unknown_property(self):
        errs = validate_qss("QWidget { not-a-property: 1px; }")
        self.assertTrue(any("未知属性" in e for e in errs))

    def test_comment_ignored(self):
        qss = "/* comment { unbalanced */ QWidget { background: #fff; }"
        self.assertEqual(validate_qss(qss), [])

    def test_font_family_multiline_ok(self):
        qss = 'QWidget {\n  font-family: "Microsoft YaHei UI", "PingFang SC", sans-serif;\n}'
        self.assertEqual(validate_qss(qss), [])

    def test_validate_or_raise_pass(self):
        validate_qss_or_raise("QLabel { color: red; }")

    def test_validate_or_raise_fail(self):
        with self.assertRaises(ValueError):
            validate_qss_or_raise("QWidget { background: #fff;")


class TestThemeIntegration(unittest.TestCase):
    def test_all_theme_qss_valid(self):
        from gcm.ui.theme import PALETTES, _build_qss
        for name, pal in PALETTES.items():
            for scale in (0.9, 1.0, 1.6):
                for motion in (True, False):
                    qss = _build_qss(pal, scale, motion)
                    self.assertEqual(validate_qss(qss), [], f"theme {name} scale {scale} motion {motion}")

    def test_theme_module_imports_with_validation(self):
        import gcm.ui.theme as theme
        self.assertEqual(validate_qss(theme.QSS), [])


if __name__ == "__main__":
    unittest.main()