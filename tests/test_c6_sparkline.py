"""Rev8 C6-3 守卫：吞吐 sparkline（内存 ring buffer，无网络无第三方依赖）。

三条不变量：
1. `ui/sparkline.py` 提供 `Sparkline(QWidget)`：`push(v)` 追加、按 5 分钟粒度
   保留最近 288 点（= 24h），paintEvent 画折线；窗口关闭不崩。
2. ring buffer 是**纯函数数据结构**（`ThrottleRing`）：可独立于 Qt 测试。
3. 纯函数 `downsample()` / `bucket_key()` 的行为确定（测试不依赖真实时钟快慢）。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402


class TestThrottleRing(unittest.TestCase):
    def test_capacity_288(self):
        from gcm.ui.sparkline import ThrottleRing
        ring = ThrottleRing(capacity=288)
        for i in range(300):
            ring.push(i)
        self.assertEqual(ring.size, 288)
        self.assertEqual(ring.values[-1], 299)   # 最新
        self.assertEqual(ring.values[0], 12)     # 最旧被挤出

    def test_empty_values(self):
        from gcm.ui.sparkline import ThrottleRing
        ring = ThrottleRing()
        self.assertEqual(ring.values, [])

    def test_downsample_deterministic(self):
        from gcm.ui.sparkline import downsample
        vals = [1, 2, 3, 4, 5, 6]
        self.assertEqual(downsample(vals, 3), [1.5, 3.5, 5.5])  # 每桶均值


class TestSparklineWidget(unittest.TestCase):
    def test_push_and_render(self):
        from gcm.ui.sparkline import Sparkline
        w = Sparkline()
        for i in range(10):
            w.push(float(i))
        self.assertEqual(w.ring.size, 10)
        w.resize(120, 28)
        w.repaint()  # paintEvent 不崩
        w.deleteLater()

    def test_empty_paint_no_crash(self):
        from gcm.ui.sparkline import Sparkline
        w = Sparkline()
        w.resize(80, 28)
        try:
            w.repaint()
        except Exception as exc:  # noqa: BLE001
            self.fail(f"空数据 paintEvent 崩溃：{exc!r}")
        w.deleteLater()


class TestWiring(unittest.TestCase):
    def test_status_strip_or_main_window_has_sparkline(self):
        """齿条或主窗必须真的创建 Sparkline（生产调用点，不许只有测试引用）。"""
        src = (ROOT / "gcm/ui/status_strip.py").read_text(encoding="utf-8")
        src2 = (ROOT / "gcm/ui/main_window.py").read_text(encoding="utf-8")
        self.assertTrue("Sparkline" in src or "Sparkline" in src2,
                        "Sparkline 无生产调用点")


if __name__ == "__main__":
    unittest.main()
