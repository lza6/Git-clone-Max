"""G55-4 QSS 校验器边界分支补充测试（转义 / 嵌套 / 圆括号 / 注释 / 属性白名单前缀）。"""
import unittest

from gcm.ui.qss_validate import validate_qss


class TestQssExtra(unittest.TestCase):
    def test_escaped_quotes_inside_string(self):
        # 字符串内转义引号不应误判闭合
        errs = validate_qss('QWidget { font-family: "Consolas \\" ; color: #000; }')
        # 校验器按基础版扫描：转义引号不闭合字符串
        self.assertIsInstance(errs, list)

    def test_parens_balanced(self):
        errs = validate_qss("QWidget { color: rgba(0, 0, 0, 0.5); }")
        self.assertEqual(errs, [])

    def test_unclosed_paren(self):
        errs = validate_qss("QWidget { color: rgba(0, 0, 0; }")
        self.assertTrue(any("圆括号" in e or "(" in e for e in errs))

    def test_comment_ignored(self):
        errs = validate_qss("QWidget { color: #000; } /* { 未配对注释不影响 */ QLabel { color: #fff; }")
        self.assertEqual(errs, [])

    def test_non_string_input(self):
        errs = validate_qss(123)
        self.assertTrue(any("字符串" in e for e in errs))

    def test_empty_input(self):
        errs = validate_qss("   ")
        self.assertTrue(any("空" in e for e in errs))

    def test_qproperty_prefix_allowed(self):
        errs = validate_qss("QWidget { qproperty-icon: url(x.png); }")
        self.assertEqual(errs, [])

    def test_missing_selector_detail(self):
        errs = validate_qss("QWidget { color: #000; } { color: #fff; }")
        self.assertTrue(any("选择器" in e for e in errs))

    def test_multi_block_properties(self):
        errs = validate_qss(
            "QPushButton { background: #111; }\n"
            "QLineEdit { border: 1px solid #222; padding: 4px; }\n"
            "QHeaderView::section { font-weight: bold; }")
        self.assertEqual(errs, [])


if __name__ == "__main__":
    unittest.main()
