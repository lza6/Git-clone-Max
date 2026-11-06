"""Rev14 像素回归守卫：比对器必须真的能报警（反向自检）。

`scripts/pixel_diff.py` 存在的理由是历史事故：`ui_screenshot_audit.py` 只存图
不断言，于是「保存清单 / 载入 / 导入 Star」那一行在 1366×768 下被裁成 4 个空框，
连续 4 轮 UI 迭代没人发现。但如果阈值宽到 2px 位移也发现不了，它同样在守空 ——
所以这里断言的是**比对器本身**有没有牙。
"""
from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "pixel_diff", ROOT / "scripts" / "pixel_diff.py")
pixel_diff = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pixel_diff)


class TestPixelDiffSelfCheck(unittest.TestCase):
    def test_identical_passes_and_2px_shift_is_caught(self):
        res = pixel_diff.self_check()
        self.assertTrue(res["identical_ok"], "完全相同的图必须判通过")
        self.assertTrue(res["shifted_caught"],
                        "平移 2px 必须被抓到 —— 否则阈值宽到守不住任何东西")
        self.assertGreater(res["shifted_ratio"], pixel_diff.MAX_DIFF_RATIO)


class TestCompareImagesEdgeCases(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def _img(self, path: Path, w: int, h: int, color: str) -> Path:
        from PyQt6.QtGui import QColor, QImage
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(QColor(color))
        img.save(str(path))
        return path

    def test_size_change_fails_not_ratio_masked(self):
        """尺寸变化是结构改动，不能被"比例"糊过去。"""
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        a = self._img(tmp / "a.png", 100, 80, "#101418")
        b = self._img(tmp / "b.png", 120, 80, "#101418")
        res = pixel_diff.compare_images(a, b)
        self.assertFalse(res["ok"])
        self.assertIn("尺寸", res["reason"])

    def test_unreadable_image_fails_loudly(self):
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        good = self._img(tmp / "a.png", 20, 20, "#101418")
        res = pixel_diff.compare_images(good, tmp / "missing.png")
        self.assertFalse(res["ok"])
        self.assertIn("读取失败", res["reason"])

    def test_missing_file_in_dir_is_a_failure(self):
        """基线有、当前没截到 → 也是回归（少一张图不能让回归静默通过）。"""
        import tempfile
        tmp = Path(tempfile.mkdtemp())
        base = tmp / "base"
        cur = tmp / "cur"
        base.mkdir()
        cur.mkdir()
        self._img(base / "only_in_base.png", 20, 20, "#101418")
        res = pixel_diff.compare_dirs(base, cur)
        self.assertFalse(res["ok"])
        self.assertIn("only_in_base.png", res["missing"])


if __name__ == "__main__":
    unittest.main()
