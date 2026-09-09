# -*- coding: utf-8 -*-
"""G02-4 URL 历史持久化测试：最近同步地址留档、去重、上限裁剪、右键回填。"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.history import UrlHistory, MAX_HISTORY


class TestUrlHistory(unittest.TestCase):
    def test_add_and_load(self):
        d = Path(tempfile.mkdtemp())
        h = UrlHistory(d / "history.json")
        h.add("https://github.com/a/b")
        h.add("https://gitlab.com/c/d")
        items = h.items()
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0], "https://gitlab.com/c/d")  # 最新在前
        self.assertEqual(items[1], "https://github.com/a/b")

    def test_dedupe(self):
        d = Path(tempfile.mkdtemp())
        h = UrlHistory(d / "history.json")
        h.add("https://github.com/a/b")
        h.add("https://github.com/a/b")
        self.assertEqual(len(h.items()), 1)

    def test_cap(self):
        d = Path(tempfile.mkdtemp())
        h = UrlHistory(d / "history.json")
        for i in range(MAX_HISTORY + 10):
            h.add(f"https://github.com/x/{i}")
        self.assertLessEqual(len(h.items()), MAX_HISTORY)

    def test_clear(self):
        d = Path(tempfile.mkdtemp())
        h = UrlHistory(d / "history.json")
        h.add("https://github.com/a/b")
        h.clear()
        self.assertEqual(h.items(), [])

    def test_persist_across_instances(self):
        d = Path(tempfile.mkdtemp())
        p = d / "history.json"
        h1 = UrlHistory(p)
        h1.add("https://github.com/a/b")
        h2 = UrlHistory(p)
        self.assertIn("https://github.com/a/b", h2.items())

    def test_corrupt_falls_back(self):
        d = Path(tempfile.mkdtemp())
        p = d / "history.json"
        p.write_text("{oops", encoding="utf-8")
        h = UrlHistory(p)
        self.assertEqual(h.items(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
