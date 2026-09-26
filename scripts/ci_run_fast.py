"""G59-5 CI 快集运行器：运行非慢集测试（全量 - 慢集黑名单）。

慢集清单与 docs/慢测定位.md 一致（真实 git E2E，默认全量 ~14.7min）。
快集用于主矩阵 coverage 门禁，慢集由 CI 独立 slow job 承接。
用法：python scripts/ci_run_fast.py [--coverage]
"""
from __future__ import annotations

import sys
from pathlib import Path

SLOW = {
    "test_engine", "test_v7_fixes", "test_core", "test_extra", "test_v3_p0",
    "test_submodule", "test_publish", "test_scanner", "test_tag_branch",
    "test_progress_search",
}

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    sys.path.insert(0, str(ROOT))
    import unittest

    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    tests_dir = ROOT / "tests"
    fast = sorted(p.stem for p in tests_dir.glob("test_*.py")
                  if p.stem not in SLOW)
    for stem in fast:
        try:
            mod = __import__(f"tests.{stem}", fromlist=["*"])
        except Exception as exc:  # noqa: BLE001
            print(f"[ci_run_fast] import {stem} failed: {exc}")
            return 1
        suite.addTests(loader.loadTestsFromModule(mod))
    runner = unittest.TextTestRunner(verbosity=1)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
