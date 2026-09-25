"""G52-4 进度文件治理测试（滚动保留 / 归档 / 阈值裁剪）。"""
import json
import tempfile
import unittest
from pathlib import Path

from gcm.db import progress_gc


def _progress(n_finished: int = 10) -> dict:
    return {
        "finished": [{"key": f"o/r{i}", "status": "success", "message": "ok",
                      "time": "2026-09-25 00:00:00"} for i in range(n_finished)],
        "in_progress": {},
        "failed": [],
    }


class TestProgressGc(unittest.TestCase):
    def test_rotate_trims_and_archives(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "progress.json"
            p.write_text(json.dumps(_progress(300)), encoding="utf-8")
            stats = progress_gc.rotate_progress(p, max_finished=200)
            self.assertGreaterEqual(stats["trimmed"], 100)
            self.assertLessEqual(stats["rows_kept"], 200)
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertLessEqual(len(data["finished"]), 200)
            # 结构兼容 load_progress
            self.assertIn("finished", data)
            self.assertIn("in_progress", data)
            self.assertIn("failed", data)
            arch = list((Path(td) / "progress_archive").glob("*.json"))
            self.assertTrue(arch)

    def test_small_file_untouched(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "progress.json"
            p.write_text(json.dumps(_progress(10)), encoding="utf-8")
            stats = progress_gc.rotate_progress(p, max_finished=200)
            self.assertEqual(stats["trimmed"], 0)
            self.assertEqual(stats["rows_kept"], 10)

    def test_missing_file_no_crash(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "nope.json"
            stats = progress_gc.rotate_progress(p)
            self.assertIn("trimmed", stats)
            self.assertIn("rows_kept", stats)

    def test_corrupt_file_no_crash(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "progress.json"
            p.write_text("{broken", encoding="utf-8")
            stats = progress_gc.rotate_progress(p)
            self.assertIn("trimmed", stats)


if __name__ == "__main__":
    unittest.main()
