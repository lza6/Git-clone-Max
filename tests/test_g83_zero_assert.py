"""守卫：零断言测试清零（G83）。

历史：`_gcm_audit/zero_assert_scan.py`（G83 批次引入 scripts/）在 2026-11-03 实测
抓出 8 个零断言测试——它们全是"不抛即过"的形状，异常发生时静默绿。
全部补真实断言后清零。本守卫防止再混入。

直接复用 scripts/zero_assert_scan.py 的 AST 分析，不重复实现。
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import zero_assert_scan as zas  # noqa: E402


class TestNoZeroAssertionTests(unittest.TestCase):
    def test_every_test_method_has_an_assertion(self):
        offenders: list[str] = []
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    for item in node.body:
                        if (isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                                and item.name.startswith("test")):
                            info = zas.analyze_function(item)
                            if not info["asserts"]:
                                offenders.append(
                                    f"{path.name}:{item.lineno} {node.name}.{item.name}")
        self.assertEqual(offenders, [],
                         "发现零断言测试（不抛即过，异常时会静默绿）：\n"
                         + "\n".join(offenders))


if __name__ == "__main__":
    unittest.main()
