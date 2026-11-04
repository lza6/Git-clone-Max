"""G84-14 / G84-15：质量门禁本身的守卫。

三道门禁都可能"看起来有、其实不拦"：
1. **vulture**：阈值/白名单写错 → 永远 0 发现，等于没门禁；
2. **质量棘轮**：基线不存在或是空 JSON → 每次都 WORSE 0；
3. **测试选取**：图建错了 → 永远返回同一批测试，等于没选。

所以这里对三个脚本各做一次**真实运行**断言（不是读源码就算）。
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
WHITELIST = ROOT / "scripts" / "vulture_whitelist.py"
BASELINE = ROOT / "evidence" / "quality_baseline.json"
EXPECTED_OVERSIZED = {"gcm/app/engine.py", "gcm/git/service.py", "gcm/ui/main_window.py"}


def _load(name: str):
    path = ROOT / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"gcm_{name}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestVultureGate(unittest.TestCase):
    def test_whitelist_exists_and_is_a_python_module(self):
        self.assertTrue(WHITELIST.is_file(), "vulture 白名单缺失")
        compile(WHITELIST.read_text(encoding="utf-8"), str(WHITELIST), "exec")

    def test_whitelist_entries_carry_reasons(self):
        """白名单文件必须说明"为什么这些符号是合法的"，否则会退化成橡皮章。"""
        text = WHITELIST.read_text(encoding="utf-8")
        self.assertIn("理由", text)
        self.assertGreater(len(text.splitlines()), 12)

    def test_vulture_reports_nothing_at_80(self):
        try:
            import vulture  # noqa: F401
        except ImportError:                        # pragma: no cover - 依赖缺失时跳过
            self.skipTest("vulture 未安装（CI 由 requirements-build.txt 提供）")
        out = subprocess.run(
            [sys.executable, "-m", "vulture", "gcm", str(WHITELIST.relative_to(ROOT)),
             "--min-confidence", "80"],
            cwd=ROOT, text=True, capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(out.returncode, 0,
                         f"vulture（≥80）发现死代码/未接线：\n{out.stdout}{out.stderr}")

    def test_vulture_dependency_is_pinned(self):
        req = (ROOT / "requirements-build.txt").read_text(encoding="utf-8")
        self.assertRegex(req, r"(?m)^vulture==\d+\.\d+",
                         "vulture 没进 requirements-build.txt（CI 上装不到就永远绿）")


class TestQualityRatchet(unittest.TestCase):
    def test_baseline_exists_and_is_populated(self):
        self.assertTrue(BASELINE.is_file(), "缺少 quality_baseline.json（门禁会是空转）")
        data = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertGreater(len(data.get("per_file", {})), 100)
        self.assertEqual(set(data.get("oversized", [])), EXPECTED_OVERSIZED,
                         "基线里的超限模块集合与登记不符（有人偷偷加了第 4 个？）")

    def test_clean_tree_reports_no_regression(self):
        mod = _load("quality_delta")
        self.assertEqual(mod.compare({"per_file": mod.snapshot()["per_file"],
                                      "oversized": mod.snapshot()["oversized"]},
                                     [], {}), [])

    def test_line_growth_is_caught(self):
        """反向自检：把基线里某文件行数调小，必须报"变坏"。"""
        mod = _load("quality_delta")
        now = mod.snapshot()
        target = next(k for k, v in now["per_file"].items()
                      if v["lines"] > mod.LINE_TOLERANCE + 5)
        fake = {"per_file": {target: {"lines": now["per_file"][target]["lines"] - 100,
                                      "excepts": now["per_file"][target]["excepts"]}},
                "oversized": now["oversized"]}
        problems = mod.compare(fake, [target], {})
        self.assertTrue(problems, "行数暴涨没被发现（棘轮失效）")

    def test_ack_requires_reason(self):
        mod = _load("quality_delta")
        rc = mod.main(["--ack", "gcm/ui/x.py:lines"])
        self.assertEqual(rc, 2, "无理由的 ack 被接受了（豁免会变橡皮章）")


class TestSelectTests(unittest.TestCase):
    def test_selects_tests_for_a_leaf_module(self):
        mod = _load("select_tests")
        res = mod.select(["gcm/db/backup.py"])
        self.assertTrue(res["tests"], "改 backup.py 没选出任何测试")
        self.assertIn("test_g84_backup_atomic", res["tests"])

    def test_hub_module_pulls_wide_blast_radius(self):
        """settings.py 被几乎所有模块 import —— 爆炸半径必须真的变大。"""
        mod = _load("select_tests")
        leaf = mod.select(["gcm/db/backup.py"])["tests"]
        hub = mod.select(["gcm/db/settings.py"])["tests"]
        self.assertGreater(len(hub), len(leaf),
                           "图没建对：核心模块的爆炸半径竟然不大于叶子模块")

    def test_non_python_change_reports_unknown_instead_of_guessing(self):
        mod = _load("select_tests")
        res = mod.select(["README.md"])
        self.assertEqual(res["tests"], [])
        self.assertTrue(res["run_unknown"], "推不出来时必须显式给 run_unknown，不能装作没事")


if __name__ == "__main__":
    unittest.main()
