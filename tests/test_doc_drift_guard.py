"""C1-4 守卫自测：`scripts/doc_drift_check.py` 必须**真的**能报出漂移。

**为什么这本身需要测试**：本项目在「守卫只覆盖一侧」上付过两次学费 ——
`test_g67_docs_freshness` 只守版本号与路径，不守行数/函数数/技能包数，
于是六处数字同时过期而全绿；`TestG69_18NoMainWindowRefCycle` 曾因不调 `show()`
而永远绿。所以任何新守卫都必须自带「改坏会红」的反向自检，
否则它只是一段永远返回 0 的代码。

变异验证方式（不写文件、不改真实文档）：monkeypatch `doc_drift_check._read`，
让某个文档在被读取时返回**被改坏的内容**，断言对应 check 函数报出问题。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import doc_drift_check as ddc  # noqa: E402


class TestDocDriftGuardIsAlive(unittest.TestCase):
    """正向：当前仓库状态必须是 DRIFT 0。"""

    def test_repo_is_clean(self):
        self.assertEqual(
            ddc.main([]), 0,
            "仓库当前有文档漂移 —— 跑 `python scripts/doc_drift_check.py` 看具体项")

    def test_every_check_finds_something(self):
        actual = ddc._actual_lines()
        self.assertGreater(len(actual), 50, "模块行数表读不到东西")
        self.assertGreater(len(ddc.dev_metrics.long_functions()), 0)


class TestGuardCatchesDrift(unittest.TestCase):
    """反向：把文档内容改坏，对应检查必须报错（否则守卫是空转）。"""

    def setUp(self):
        self._orig_read = ddc._read

    def tearDown(self):
        ddc._read = self._orig_read

    def _fake(self, overrides: dict[str, str]):
        def _read(rel: str) -> str:
            if rel in overrides:
                return overrides[rel]
            return self._orig_read(rel)
        ddc._read = _read
        return _read

    # 审查 Required-2：这两条原本用 `skipTest` 兜「文档里的旧值变了」——
    # **恰恰在行数真的变了（守卫最该生效的时刻）它静默跳过**，变异验证可以永久失效。
    # 现改为从文档里现取一个真实数字，再把它改坏；取不到就 `fail`（而不是跳过）。
    def _corrupt_first_line_count(self, rel: str, check) -> list[str]:
        import re as _re
        text = self._orig_read(rel)
        pat = (r"`([A-Za-z0-9_]+\.py)`\s*\*\*(\d+)\*\*" if rel.endswith(".md")
               and not rel.startswith("docs/architecture")
               else r"\|\s*`(gcm/[^`]+)`\s*\|\s*(\d+)\s*\|")
        m = _re.search(pat, text)
        self.assertIsNotNone(
            m, f"{rel} 里找不到可改坏的模块行数（格式变了？本用例必须同步，不能跳过）")
        name, num = m.group(1), m.group(2)
        bogus = str(int(num) + 111)
        broken = text.replace(m.group(0), m.group(0).replace(num, bogus), 1)
        self.assertNotEqual(text, broken, "改坏失败，本用例无效")
        self._fake({rel: broken})
        problems = check(ddc._actual_lines())
        self.assertTrue(any(name in p and bogus in p for p in problems),
                        f"把 {name} 从 {num} 改成 {bogus} 却没报出来：{problems}")
        return problems

    def test_catches_module_line_drift_in_claude_md(self):
        problems = self._corrupt_first_line_count("CLAUDE.md", ddc.check_claude_md)
        self.assertTrue(problems, "CLAUDE.md 行数被改坏后 check_claude_md 未报出任何问题")

    def test_catches_module_line_drift_in_architecture_md(self):
        problems = self._corrupt_first_line_count(
            "docs/architecture.md", ddc.check_architecture_md)
        self.assertTrue(problems, "architecture.md 行数被改坏后未报出任何问题")

    def test_catches_long_function_count_drift(self):
        import re as _re
        text = self._orig_read("CLAUDE.md")
        m = _re.search(r"\*\*(\d+)\s*个\*\*\s*函数超\s*100\s*行", text)
        self.assertIsNotNone(m, "CLAUDE.md 的函数规模声明格式变了，请更新本用例")
        broken = text.replace(m.group(0), f"**{int(m.group(1)) + 1} 个**函数超 100 行")
        self.assertNotEqual(text, broken)
        self._fake({"CLAUDE.md": broken})
        self.assertTrue(ddc.check_long_function_count(),
                        "把 20 改回 14 却没报出来")

    def test_catches_test_file_count_drift(self):
        import re as _re
        text = self._orig_read("README.md")
        m = _re.search(r"（(\d+) 个测试文件）", text)
        self.assertIsNotNone(m, "README 的测试文件数声明格式变了，请更新本用例")
        broken = text.replace(m.group(0), f"（{int(m.group(1)) + 1} 个测试文件）")
        self.assertNotEqual(text, broken)
        self._fake({"README.md": broken})
        self.assertTrue(ddc.check_test_file_count())

    def test_catches_skills_count_drift(self):
        import re as _re
        text = self._orig_read("README.md")
        m = _re.search(r"提供 (\d+) 个开箱技能包", text)
        self.assertIsNotNone(m, "README 的技能包数声明格式变了，请更新本用例")
        broken = text.replace(m.group(0), f"提供 {int(m.group(1)) + 1} 个开箱技能包")
        self.assertNotEqual(text, broken)
        self._fake({"README.md": broken})
        self.assertTrue(ddc.check_skills())

    def test_catches_version_drift(self):
        import re as _re
        text = self._orig_read("README.md")
        m = _re.search(r"当前版本[：:]\s*\**v?\d+\.\d+\.\d+", text)
        self.assertIsNotNone(m, "README 的当前版本行格式变了，请更新本用例")
        broken = text.replace(m.group(0), "当前版本：v0.0.1")
        self.assertNotEqual(text, broken)
        self._fake({"README.md": broken})
        self.assertTrue(ddc.check_version())


if __name__ == "__main__":
    unittest.main()
