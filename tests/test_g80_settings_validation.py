"""G80-B5 守卫：设置项**声明期取值校验**（三字段试点 + 退避基数接线）。

四条不变量：
1. 越界值**不抛异常**，纠正到最近合法值（兼容旧配置脏值）；
2. UI 的 `setRange` 必须与后端校验**同一个常量**（否则会出现「UI 说 32、后端允许 999」）；
3. `retry_backoff_base_sec` 的**写入侧与消费侧都要通**（known-issues #14 最后一个只读字段）；
4. 直接改 settings.json 也能被纠正（不能只靠 UI 的 setRange 挡）。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402


class TestClamp(unittest.TestCase):
    def test_inside_range_is_untouched(self):
        from gcm.db.validators import clamp_int

        value, changed = clamp_int("concurrency", 8)
        self.assertEqual(value, 8)
        self.assertFalse(changed)

    def test_above_range_clamps_down(self):
        from gcm.db.validators import clamp_int

        self.assertEqual(clamp_int("concurrency", 999), (32, True))

    def test_below_range_clamps_up(self):
        from gcm.db.validators import clamp_int

        self.assertEqual(clamp_int("concurrency", 0), (1, True))
        self.assertEqual(clamp_int("clone_timeout", -5), (30, True))

    def test_unparseable_falls_back_to_lower_bound(self):
        from gcm.db.validators import clamp_int

        value, changed = clamp_int("retry_backoff_base_sec", "not-a-number")
        self.assertEqual(value, 5)
        self.assertTrue(changed)

    def test_unconstrained_field_passes_through(self):
        from gcm.db.validators import clamp_int

        value, changed = clamp_int("nonexistent_field", 99999)
        self.assertEqual(value, 99999)
        self.assertFalse(changed)

    def test_ui_and_backend_share_the_same_bounds(self):
        """UI setRange 与后端校验必须同源（防「UI 说 32、后端允许 999」）。"""
        from gcm.db.validators import RANGES, int_range

        for key in ("concurrency", "clone_timeout", "retry_backoff_base_sec"):
            self.assertIn(key, RANGES)
            self.assertEqual(int_range(key), RANGES[key])
        # 源码级断言：settings_groups 真的用了 int_range，而不是写死数字
        src = Path("gcm/ui/settings_groups.py").read_text(encoding="utf-8")
        self.assertIn('int_range("concurrency")', src)
        self.assertIn('int_range("clone_timeout")', src)
        self.assertIn('int_range("retry_backoff_base_sec")', src)
        self.assertNotIn("owner.spin_concurrency.setRange(1, 32)",
                         src.replace('int_range("concurrency")', "X"),
                         "并发控件不得再写死 1..32（那会与后端校验脱钩）")


class TestSettingsLoadNormalization(unittest.TestCase):
    def test_unparseable_json_value_falls_back_to_default(self):
        """「不是数字」的脏值：`_coerce` 抛 ValueError → 该键**回退默认值**（不崩溃）。

        这是刻意的既有行为（`_coerce` 失败时 `merged[k]` 保持 `_DEFAULTS`），
        校验器只负责**能解析但越界**的值；两类异常输入各有出路，都不该让启动崩掉。
        """
        from gcm.db.settings import SettingsStore

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(json.dumps({"retry_backoff_base_sec": "not-a-number"}),
                            encoding="utf-8")
            settings = SettingsStore(path).load()
            self.assertEqual(settings.retry_backoff_base_sec, 30)

    def test_dirty_json_values_are_corrected_on_load(self):
        from gcm.db.settings import SettingsStore

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(json.dumps({
                "concurrency": 999,
                "clone_timeout": -5,
                "retry_backoff_base_sec": 9999,
            }), encoding="utf-8")
            settings = SettingsStore(path).load()
            self.assertEqual(settings.concurrency, 32)
            self.assertEqual(settings.clone_timeout, 30)
            self.assertEqual(settings.retry_backoff_base_sec, 600)

    def test_legit_values_survive_unchanged(self):
        from gcm.db.settings import SettingsStore

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "settings.json"
            path.write_text(json.dumps({
                "concurrency": 16, "clone_timeout": 900, "retry_backoff_base_sec": 60,
            }), encoding="utf-8")
            settings = SettingsStore(path).load()
            self.assertEqual(settings.concurrency, 16)
            self.assertEqual(settings.clone_timeout, 900)
            self.assertEqual(settings.retry_backoff_base_sec, 60)

    def test_normalize_returns_changed_field_names(self):
        from gcm.db.settings import Settings
        from gcm.db.validators import normalize_settings

        s = Settings(concurrency=0, clone_timeout=600, retry_backoff_base_sec=30)
        changed = normalize_settings(s)
        self.assertEqual(changed, ["concurrency"])
        self.assertEqual(s.concurrency, 1)
        self.assertEqual(s.clone_timeout, 600)


class TestRetryBackoffWiring(unittest.TestCase):
    """退避基数：UI 写入侧 + 队列消费侧都必须真的生效。"""

    def test_spin_control_exists(self):
        src = Path("gcm/ui/settings_groups.py").read_text(encoding="utf-8")
        self.assertIn("owner.spin_retry_backoff", src)
        self.assertIn("owner._save_retry_backoff", src)

    def test_save_callback_exists_and_updates_queue(self):
        from gcm.app.retry_queue import RetryQueue
        from gcm.ui.main_window import MainWindow

        q = RetryQueue(path=None, backoff_base_sec=30)
        owner = MainWindow.__new__(MainWindow)  # 不起 GUI：只测回调
        owner.settings = type("S", (), {"retry_backoff_base_sec": 30})()
        owner.settings_store = type("Store", (), {"save": lambda self, s: None})()
        owner.engine = type("Eng", (), {})()
        owner.engine.retry_queue = q

        owner._save_retry_backoff(90)
        self.assertEqual(owner.settings.retry_backoff_base_sec, 90)
        self.assertEqual(q.backoff_base_sec, 90, "消费侧（运行中的队列）也必须更新")

    def test_queue_backoff_affects_actual_delay(self):
        """改完基数后，下一次退避时间必须真的变（不是只改了个没人读的字段）。"""
        from gcm.app.retry_queue import RetryQueue

        from gcm.models import RepoSpec

        q = RetryQueue(path=None, now=lambda: 1000.0, jitter=lambda d: d)
        spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b")
        q.record_failure_ex(spec, "connection timed out")
        before = q.snapshot()[0]["next_retry_at"]
        q.set_backoff_base_sec(120)
        q.record_failure_ex(spec, "connection timed out")  # 第二次失败，重新计算退避
        after = q.snapshot()[0]["next_retry_at"]
        self.assertNotEqual(before, after, "退避基数改了但下次重试时间没变")
        self.assertGreater(after, before)


if __name__ == "__main__":
    unittest.main()
