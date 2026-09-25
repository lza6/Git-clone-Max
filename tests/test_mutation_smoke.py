"""G55 工程与测试体系：变异冒烟脚本测试。

覆盖：变异器单元行为（token 级、not in、删默认值、for 循环跳过）、
三个目标函数击杀率 >= 80%、脚本整体门禁退出码 0。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_smoke():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "g55_mutation_smoke", ROOT / "scripts" / "run_mutation_smoke.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMutator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sm = _load_smoke()

    def test_mutate_single_tokens_returns_valid_code(self):
        body = "def f(x):\n    if x == 1:\n        return True\n    return False\n"
        muts = self.sm._mutate_single_tokens(body)
        self.assertTrue(muts)
        for m in muts:
            compile(m, "<m>", "exec")  # 全部可编译

    def test_mutate_single_tokens_skips_for_in(self):
        body = "def f(xs):\n    out = []\n    for x in xs:\n        out.append(x)\n    return out\n"
        muts = self.sm._mutate_single_tokens(body)
        for m in muts:
            compile(m, "<m>", "exec")  # 不得生成 for x not in xs

    def test_mutate_not_in(self):
        body = "def f(t):\n    return 1 if 'x' not in t else 0\n"
        muts = self.sm._mutate_not_in(body)
        self.assertTrue(muts)
        self.assertTrue(any("'x' in t" in m for m in muts))

    def test_drop_default(self):
        body = "def f(a, b=None):\n    return b or 1\n"
        muts = self.sm._drop_default(body)
        self.assertTrue(muts)
        self.assertNotIn("b=None", muts[0])

    def test_func_source_extracts(self):
        src = self.sm._func_source(ROOT / "gcm" / "app" / "url_lib.py", "parse_any_repo_url")
        self.assertIn("def parse_any_repo_url", src)
        self.assertIn("return RepoSpec", src)


class TestKillRate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sm = _load_smoke()

    def test_parse_any_kill_rate_ge_80(self):
        killed, total, survivors = self.sm._run_target(
            "parse_any_repo_url",
            ROOT / "gcm" / "app" / "url_lib.py",
            self.sm._cases_parse_any(),
        )
        self.assertGreaterEqual(killed / total, 0.8, f"survivors: {survivors}")

    def test_networkish_kill_rate_ge_80(self):
        killed, total, survivors = self.sm._run_target(
            "_is_networkish_error",
            ROOT / "gcm" / "git" / "service.py",
            self.sm._cases_networkish(),
        )
        self.assertGreaterEqual(killed / total, 0.8, f"survivors: {survivors}")

    def test_fetch_rows_kill_rate_ge_80(self):
        killed, total, survivors = self.sm._run_target(
            "_fetch_rows",
            ROOT / "gcm" / "reports.py",
            self.sm._cases_fetch_rows(),
        )
        self.assertGreaterEqual(killed / total, 0.8, f"survivors: {survivors}")

    def test_overall_kill_rate_ge_80(self):
        total_k = total_m = 0
        for fn, mod, cases in [
            ("parse_any_repo_url", ROOT / "gcm" / "app" / "url_lib.py", self.sm._cases_parse_any()),
            ("_is_networkish_error", ROOT / "gcm" / "git" / "service.py", self.sm._cases_networkish()),
            ("_fetch_rows", ROOT / "gcm" / "reports.py", self.sm._cases_fetch_rows()),
        ]:
            k, t, _ = self.sm._run_target(fn, mod, cases)
            total_k += k
            total_m += t
        self.assertGreaterEqual(total_k / total_m, 0.8)


class TestScriptGate(unittest.TestCase):
    def test_script_runs_and_passes_gate(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_mutation_smoke.py")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PASS", proc.stdout)

    def test_output_contains_required_fields(self):
        proc = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "run_mutation_smoke.py")],
            cwd=str(ROOT), capture_output=True, text=True, timeout=120)
        for token in ("parse_any_repo_url", "_is_networkish_error", "_fetch_rows",
                      "kill_rate", "OVERALL"):
            self.assertIn(token, proc.stdout)


if __name__ == "__main__":
    unittest.main()