"""G83 P1-1：自适应自动更新间隔（NewsNow 式动态抓取间隔）。

问题域与 NewsNow 同题：固定间隔轮询，仓库没变也全量打一轮（对 host 是无谓
请求，429 风险随仓库数上升）。策略：
- 最近一轮更新**有变更** → 回到基础间隔（用户设置值）；
- 连续 N 轮**全部无变更** → 间隔按 ×1.5 退避，封顶 4× 基础值；
- 基础值 ≤0（功能关）或无历史 → 不启用（返回原值）。

纯函数实现，方便测试与注入时钟。
"""
from __future__ import annotations

import unittest

from gcm.app.adaptive_interval import next_interval_minutes


class TestNextInterval(unittest.TestCase):
    def test_disabled_returns_base(self):
        self.assertEqual(next_interval_minutes(0, no_change_streak=3), 0)
        self.assertEqual(next_interval_minutes(-5, no_change_streak=0), -5)

    def test_no_history_returns_base(self):
        self.assertEqual(next_interval_minutes(30, no_change_streak=None), 30)

    def test_changes_returns_base(self):
        self.assertEqual(next_interval_minutes(30, no_change_streak=0), 30)

    def test_backoff_multiplies_by_half_step(self):
        self.assertEqual(next_interval_minutes(30, no_change_streak=1), 45)
        self.assertEqual(next_interval_minutes(30, no_change_streak=2), 68)  # 67.5→68

    def test_backoff_caps_at_4x(self):
        self.assertEqual(next_interval_minutes(30, no_change_streak=10), 120)
        self.assertEqual(next_interval_minutes(5, no_change_streak=99), 20)

    def test_fractional_base_rounds_up(self):
        # 1 分钟起步（最小档），退避 1.5 → 2（不能取整到 1 不动）
        self.assertEqual(next_interval_minutes(1, no_change_streak=1), 2)

    def test_result_is_int_minutes_not_zero(self):
        for streak in range(0, 12):
            v = next_interval_minutes(30, no_change_streak=streak)
            self.assertIsInstance(v, int)
            self.assertGreaterEqual(v, 30)


if __name__ == "__main__":
    unittest.main()
