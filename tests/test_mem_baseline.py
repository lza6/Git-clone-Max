"""G55 工程与测试体系：内存基线脚本 helper 的单元测试。

覆盖 parse_batch_urls/measure/classify/save-load 基线 helper（不跑大 batch）。
"""
from __future__ import annotations

import json
import pathlib
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load_mem():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "g55_mem_baseline", ROOT / "scripts" / "mem_baseline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestMemBaselineHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mem = _load_mem()

    def test_parse_batch_urls_small(self):
        valid = self.mem.parse_batch_urls(40)
        self.assertGreater(valid, 0)
        self.assertLessEqual(valid, 40)

    def test_measure_returns_fields(self):
        res = self.mem.measure(100)
        self.assertIn("peak_bytes", res)
        self.assertIn("growth_bytes", res)
        self.assertIn("valid", res)
        self.assertIn("batch", res)
        self.assertGreaterEqual(res["peak_bytes"], 0)
        self.assertEqual(res["batch"], 100)

    def test_classify(self):
        self.assertEqual(self.mem.classify(100, 100), "OK")
        self.assertEqual(self.mem.classify(115, 100), "WATCH")
        self.assertEqual(self.mem.classify(130, 100), "REGRESSION")
        self.assertEqual(self.mem.classify(100, 0), "OK")

    def test_baseline_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "base.json"
            self.assertIsNone(self.mem.load_baseline(p))
            data = {"batch": 10, "peak_bytes": 123, "growth_bytes": 45, "valid": 8, "elapsed_s": 0.1}
            self.mem.save_baseline(p, data)
            loaded = self.mem.load_baseline(p)
            self.assertEqual(loaded["peak_bytes"], 123)
            self.assertEqual(loaded["batch"], 10)

    def test_json_utf8(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "b.json"
            self.mem.save_baseline(p, {"peak_bytes": 1})
            raw = p.read_text(encoding="utf-8")
            json.loads(raw)


if __name__ == "__main__":
    unittest.main()