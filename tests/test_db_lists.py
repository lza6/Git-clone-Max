# -*- coding: utf-8 -*-
"""G35-4 任务清单持久化测试：save/list_names/items/load/delete 与文件清洗/分享性。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.db.lists import TaskLists  # noqa: E402 —— 依赖上方 sys.path 注入


def _make() -> TaskLists:
    return TaskLists(Path(tempfile.mkdtemp()))


class TestTaskListsInit(unittest.TestCase):
    def test_init_creates_dir(self):
        d = Path(tempfile.mkdtemp()) / "lists"
        TaskLists(d)
        self.assertTrue(d.is_dir())


class TestTaskListsRoundTrip(unittest.TestCase):
    def test_save_list_load_items_round_trip(self):
        tl = _make()
        row1 = "https://github.com/openai/whisper"
        row2 = "git@github.com:torvalds/linux.git"
        row3 = "torvalds/linux"  # 短格式
        path = tl.save("快速下载", [row1, row2, row3])
        self.assertTrue(Path(path).is_file())
        self.assertEqual(tl.list_names(), ["快速下载"])
        self.assertEqual(tl.items("快速下载"), [row1, row2, row3])
        # load 读回的结构化 dict
        data = tl.load("快速下载")
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["name"], "快速下载")
        self.assertEqual(data["items"], [row1, row2, row3])
        for key in ("created_at", "updated_at"):
            self.assertIn(key, data)
            self.assertIsInstance(data[key], str)
            self.assertTrue(data[key])

    def test_save_twice_preserves_created_at_updates_updated_at(self):
        tl = _make()
        p1 = tl.save("backup", ["a/b"])
        created1 = tl.load("backup")["created_at"]
        updated1 = tl.load("backup")["updated_at"]
        p2 = tl.save("backup", ["c/d", "e/f"])
        data2 = tl.load("backup")
        assert data2 is not None
        # items 被整体替换
        self.assertEqual(data2["items"], ["c/d", "e/f"])
        # created_at 保持不变
        self.assertEqual(data2["created_at"], created1)
        # updated_at 被更新（时间精度为秒，覆盖场景下必然变化）
        self.assertNotEqual(data2["updated_at"], updated1)
        self.assertEqual(p1, p2)  # 同名校覆盖，路径一致

    def test_empty_name_raises(self):
        tl = _make()
        with self.assertRaises(ValueError):
            tl.save("", ["a"])

    def test_all_illegal_name_raises(self):
        tl = _make()
        with self.assertRaises(ValueError):
            tl.save('<>:"/\\|?*', ["a"])


class TestTaskListsFilenameCleaning(unittest.TestCase):
    def test_illegal_chars_cleaned(self):
        tl = _make()
        # 非法字符 <>:"/\|?* 全部替换为 _
        rows = ["a/b", "a:b", "a*b"]
        for name in rows:
            p = tl.save(name, ["https://github.com/x/y"])
            self.assertEqual(p.name, "a_b.json", f"{name} 清洗后应存为 a_b.json")
        # 三个非法名清洗后都指向同一文件 → 后写覆盖，最终仅一个清单
        names = tl.list_names()
        self.assertEqual(names, ["a_b"])
        data = tl.load("a_b")
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data["name"], "a_b")

    def test_cleaned_name_list_load_items(self):
        tl = _make()
        tl.save("weird>name", ["line"])
        self.assertEqual(tl.list_names(), ["weird_name"])
        self.assertEqual(tl.items("weird_name"), ["line"])
        self.assertIsNotNone(tl.load("weird_name"))


class TestTaskListsSaveReturn(unittest.TestCase):
    def test_save_returns_path_like(self):
        tl = _make()
        p = tl.save("lst", ["x"])
        self.assertIsInstance(p, Path)
        # 返回的路径就是可被 load 的路径
        self.assertEqual(Path(p).parent, tl.dir)


class TestTaskListsLoadMissingCorrupt(unittest.TestCase):
    def test_load_missing_returns_none(self):
        tl = _make()
        self.assertIsNone(tl.load("不存在的清单"))

    def test_items_missing_returns_empty(self):
        tl = _make()
        self.assertEqual(tl.items("不存在的清单"), [])

    def test_load_corrupt_returns_none(self):
        tl = _make()
        tl.save("bad", ["v"])
        (tl.dir / "bad.json").write_text("{oops", encoding="utf-8")
        self.assertIsNone(tl.load("bad"))

    def test_load_wrong_type_returns_none(self):
        tl = _make()
        (tl.dir / "listy.json").write_text("[1, 2]", encoding="utf-8")
        self.assertIsNone(tl.load("listy"))


class TestTaskListsDelete(unittest.TestCase):
    def test_delete_removes_list(self):
        tl = _make()
        tl.save("临时清单", ["a"])
        self.assertTrue(tl.delete("临时清单"))
        self.assertNotIn("临时清单", tl.list_names())
        self.assertIsNone(tl.load("临时清单"))
        self.assertEqual(tl.items("临时清单"), [])

    def test_delete_missing_returns_false(self):
        tl = _make()
        self.assertFalse(tl.delete("不存在的清单"))


class TestTaskListsShareable(unittest.TestCase):
    def test_file_is_plain_json(self):
        tl = _make()
        tl.save("分享清单", ["https://github.com/a/b", "中文行🎯"])
        raw = json.loads((tl.dir / "分享清单.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["name"], "分享清单")
        self.assertEqual(raw["items"], ["https://github.com/a/b", "中文行🎯"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
