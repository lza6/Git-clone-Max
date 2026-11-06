"""i18n 覆盖守卫（Rev7 X-5）。

**它防的是什么**：`tr("中文文案")` 的兜底是「返回 key 本身」——
所以一个键只要**不在字典里**，英文模式下就**原样显示中文**，而且不会报错、不会崩、
界面看起来"正常"。本项目实测有 **72 个** 这样的键在用（ZH/EN 都是 494 键，看着对称，
所以从没人怀疑过），其中包含「保存」「确定」「搜索设置项」这种到处都是的常用词。

**三条断言**（都能被反例证伪）：
1. 代码里每个 `tr("字面量")` 的键 ∈ ZH 且 ∈ EN（报错带出**第一个出现位置**，方便定位）；
2. ZH 与 EN 键集合完全相等（防"只改一本"）；
3. 含中文的键，EN 的值不得再含中文（防"占位未翻译"——那等价于回退中文）。

**注意**：只静态检查 `tr("字面量")`。`tr(变量)` / f-string 无法在编译期求值，
不在此守卫范围内（这类用法本身也不好，应改成字面量 + `.format()`）。
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import i18n_coverage as cov  # noqa: E402

_CJK = re.compile(r"[一-鿿]")


class TestI18nCoverage(unittest.TestCase):
    def test_no_missing_or_untranslated_keys(self):
        problems = cov.check()
        self.assertEqual(
            problems, [],
            f"i18n 覆盖问题 {len(problems)} 条（跑 `python scripts/i18n_coverage.py` 看全量）：\n  "
            + "\n  ".join(problems[:20]))

    def test_both_dictionaries_exist(self):
        zh = cov._dict(ROOT / "gcm/i18n/zh.py", "ZH")
        en = cov._dict(ROOT / "gcm/i18n/en.py", "EN")
        self.assertGreater(len(zh), 400, "zh 字典读取异常")
        self.assertEqual(set(zh), set(en), "zh/en 键集合必须完全相等")

    def test_guard_is_not_hollow(self):
        """反向自检：扫描器必须真的扫到大量键（否则正则/遍历坏了也是"绿"）。"""
        used = cov._used()
        self.assertGreater(len(used), 400, f"tr() 字面量键只扫到 {len(used)} 个，扫描器可疑")

    def test_short_common_words_are_translated(self):
        """抽查最容易"漏网"的常用短词：它们在英文模式下显示中文最刺眼。"""
        en = cov._dict(ROOT / "gcm/i18n/en.py", "EN")
        for key in ("保存", "确定", "取消这些卡死任务", "黑匣子日志", "搜索设置项"):
            self.assertIn(key, en, f"常用词 {key!r} 不在 en 字典里")
            self.assertIsNone(_CJK.search(str(en[key])),
                              f"常用词 {key!r} 的英文值仍是中文：{en[key]!r}")


if __name__ == "__main__":
    unittest.main()

class TestTrArityAndRatchet(unittest.TestCase):
    """Rev14 新增：`tr()` 参数个数 + 未翻译棘轮必须真的跑在测试里。"""

    def test_tr_takes_exactly_one_argument(self):
        """`tr()` 只接受 **1 个位置参数**。

        真事故（Rev14）：批量迁移把 `QMessageBox.warning(self, "保存失败", str(e))`
        切成了 `tr("保存失败", str(e))` —— 六处错误路径（导出 CSV/元数据/PNG、导入、
        保存备注、保存清单）在失败时都会在槽里抛 TypeError，PyQt6 随即 abort
        （实测退出码 0xC0000409），不是弹错误框。这条守卫钉住参数个数。
        """
        import ast
        offenders = []
        for p in (ROOT / "gcm").rglob("*.py"):
            if "i18n" in p.as_posix():
                continue
            tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            for n in ast.walk(tree):
                if (isinstance(n, ast.Call) and getattr(n.func, "id", "") == "tr"
                        and len(n.args) > 1):
                    offenders.append(f"{p.relative_to(ROOT).as_posix()}:{n.lineno}")
        self.assertEqual(offenders, [],
                         f"tr() 多实参会在槽里抛 TypeError 并让进程 abort：{offenders}")

    def test_untranslated_ratchet_is_enforced(self):
        """未走 `tr()` 的中文字面量棘轮必须被测试执行（否则它只是手动脚本）。"""
        self.assertEqual(cov.check_untranslated(), [])
