"""批次③（桌面集成）守卫：防休眠 / 完成后睡眠 / mirror·degrade 可见后缀。

三个能力全部**默认关**（用户拍板的默认值口径），守卫钉死三件事：
1. `gcm/app/power.py`：Windows API 封装在非 Windows/异常下安全返回，永不抛；
2. 设置三字段**写入侧与消费侧都接线**（G80-B2 口径：只写不读 = 虚伪实现）；
3. 批次生命周期：launch 开防休眠、finish 撤防休眠 + 按设置起倒计时、
   新批次取消待执行睡眠。
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
import weakref

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402


class TestPowerModuleSafety(unittest.TestCase):
    """power.py 在任何环境下都不得抛（CI 三平台 + 权限不足机）。"""

    def test_non_windows_returns_false_and_never_raises(self):
        from gcm.app import power
        with patch.object(power, "_is_windows", return_value=False):
            self.assertFalse(power.prevent_sleep_start())
            self.assertIsNone(power.prevent_sleep_end())
            self.assertFalse(power.request_sleep())

    def test_windows_path_calls_kernel32(self):
        from gcm.app import power
        calls = []

        class FakeK32:
            def SetThreadExecutionState(self, flags):
                calls.append(flags)
                return 1

        fake = type("K", (), {"kernel32": FakeK32()})()
        with patch.object(power, "_is_windows", return_value=True), \
                patch.dict("sys.modules", {"ctypes": type("C", (), {"windll": fake})}):
            self.assertTrue(power.prevent_sleep_start())
            power.prevent_sleep_end()
        self.assertEqual(len(calls), 2)
        # 第二次调用是"恢复"（只带 CONTINUOUS，不带 SYSTEM_REQUIRED）
        self.assertEqual(calls[1] & 0x1, 0)

    def test_request_sleep_failure_is_false(self):
        from gcm.app import power

        class BadLib:
            def LoadLibrary(self, _):
                raise OSError("no powrprof")

        class FakeCtypes:
            windll = type("W", (), {"LoadLibrary": BadLib().LoadLibrary})()

        with patch.object(power, "_is_windows", return_value=True), \
                patch.dict("sys.modules", {"ctypes": FakeCtypes}):
            self.assertFalse(power.request_sleep())


class TestSettingsWiring(unittest.TestCase):
    """三个新设置字段：写侧（save_*）与读侧（power_guard 消费）都接通。"""

    def _wiring_and_owner(self):
        from gcm.db.settings import Settings, SettingsStore
        import tempfile
        d = Path(tempfile.mkdtemp())
        store = SettingsStore(d / "settings.json")
        owner = type("O", (), {"settings": Settings(), "settings_store": store})()
        from gcm.ui.settings_actions import SettingsWiring
        wiring = SettingsWiring.__new__(SettingsWiring)
        panel = type("P", (), {"owner": owner})()
        wiring._panel_ref = weakref.ref(panel)
        wiring._test_panel = panel  # 保活：weakref 目标被 GC 会让 _save_field 静默失效
        return owner, wiring

    def test_save_three_fields(self):
        owner, wiring = self._wiring_and_owner()
        wiring.save_prevent_sleep_during_batch(True)
        wiring.save_sleep_after_batch(True)
        wiring.save_sleep_after_seconds(120)
        self.assertTrue(owner.settings.prevent_sleep_during_batch)
        self.assertTrue(owner.settings.sleep_after_batch)
        self.assertEqual(owner.settings.sleep_after_batch_seconds, 120)

    def test_defaults_are_off(self):
        from gcm.db.settings import Settings
        s = Settings()
        self.assertFalse(s.prevent_sleep_during_batch)
        self.assertFalse(s.sleep_after_batch)
        self.assertEqual(s.sleep_after_batch_seconds, 60)


class TestBatchSleepLifecycle(unittest.TestCase):
    """launch → finish 的防休眠/睡眠生命周期（QTimer 真实例，行为断言）。"""

    def _owner(self, **settings_kw):
        from gcm.db.settings import Settings
        from PyQt6.QtCore import QObject
        s = Settings(**settings_kw)
        class O(QObject):
            pass
        owner = O()
        owner.settings = s
        owner.busy = False
        owner._sleep_timer = None
        owner.tray = None
        return owner

    def test_launch_starts_guard_and_cancels_pending_sleep(self):
        from gcm.ui import power_guard
        owner = self._owner(prevent_sleep_during_batch=True,
                            sleep_after_batch=True)
        owner._sleep_timer = object()  # 模拟存在待执行睡眠
        # 直接调 _launch 里的同一段逻辑：取消待执行睡眠
        power_guard.cancel_pending_sleep(owner)
        self.assertIsNone(owner._sleep_timer)
        # 防休眠启动（Windows 真机才生效；这里验证调用路径存在且不抛）
        power_guard.batch_sleep_guard_start(owner)

    def test_finish_ends_guard_and_arms_timer(self):
        from gcm.ui import power_guard
        owner = self._owner(sleep_after_batch=True,
                            sleep_after_batch_seconds=10)
        power_guard.batch_sleep_guard_end(owner)
        timer = getattr(owner, "_sleep_timer", None)
        self.assertIsNotNone(timer, "开启 sleep_after_batch 后应布防倒计时")
        self.assertTrue(timer.isActive())

    def test_finish_without_setting_no_timer(self):
        from gcm.ui import power_guard
        owner = self._owner(sleep_after_batch=False)
        power_guard.batch_sleep_guard_end(owner)
        self.assertIsNone(getattr(owner, "_sleep_timer", None))

    def test_do_sleep_skipped_when_busy(self):
        from gcm.ui import power_guard
        owner = self._owner()
        owner.busy = True
        with patch("gcm.app.power.request_sleep") as spy:
            power_guard._do_sleep(owner)
            spy.assert_not_called()


class TestMirrorDegradeVisibleSuffix(unittest.TestCase):
    """mirror_used/degrade_path 的「本次」列可见后缀（Rev19）。

    后缀拼接在 `progress_table._on_worker_result` 内联（GUI 组件重、不走此桩）；
    这里钉住**契约**：字段存在于 SyncResult、i18n key 已入中英字典、
    生产代码里后缀与 tooltip 同源（同一个 _mu/_dp 判定），防两处漂移。
    """

    def test_fields_exist_on_sync_result(self):
        from gcm.models import RepoSpec, SyncResult, SyncStatus
        r = SyncResult(spec=RepoSpec(owner="o", repo="r", url_https="https://x"),
                       status=SyncStatus.SUCCESS,
                       mirror_used="https://mirror.example/", degrade_path="treeless")
        self.assertEqual(r.mirror_used, "https://mirror.example/")
        self.assertEqual(r.degrade_path, "treeless")

    def test_suffix_keys_in_i18n(self):
        from gcm.i18n import en, zh
        keys = (" · 镜像", " · 降级", "睡眠倒计时", "睡眠倒计时（秒）",
                "批次进行期间阻止系统休眠", "批次完成后让电脑睡眠",
                "睡眠倒计时（秒）", "{s} 秒后让电脑睡眠（运行新批次会自动取消）")
        for key in keys:
            self.assertIn(key, zh.ZH, key)
            self.assertIn(key, en.EN, key)

    def test_progress_table_uses_suffix(self):
        """AST 断言：progress_table 里存在「 · 镜像」后缀拼接（生产路径在用）。"""
        import ast
        src = (ROOT / "gcm" / "ui" / "progress_table.py").read_text(encoding="utf-8")
        self.assertIn('tr(" · 镜像")', src)
        self.assertIn('tr(" · 降级")', src)
        tree = ast.parse(src)
        joined = ast.dump(tree)
        self.assertIn("mirror_used", joined)
        self.assertIn("degrade_path", joined)


class TestStopReason(unittest.TestCase):
    """批次⑤：终态原因枚举（str 兼容）+ 显式转换表。"""

    def test_str_compatibility(self):
        from gcm.app.batch_state import StopReason
        self.assertEqual(StopReason.CANCELLED, "cancelled")
        self.assertEqual(StopReason.COMPLETED.value, "completed")

    def test_coerce_legacy_strings(self):
        from gcm.app.batch_state import StopReason
        self.assertIs(StopReason.coerce("cancelled"), StopReason.CANCELLED)
        self.assertIs(StopReason.coerce(StopReason.FAILED), StopReason.FAILED)
        # 未知旧字符串不抛，回退 COMPLETED（向后兼容红线）
        self.assertIs(StopReason.coerce("whatever-legacy"), StopReason.COMPLETED)

    def test_task_state_conversion_table(self):
        from gcm.app.batch_state import StopReason, TASK_STATE_TO_STOP_REASON
        self.assertIs(TASK_STATE_TO_STOP_REASON["failed"], StopReason.FAILED)
        self.assertIs(TASK_STATE_TO_STOP_REASON["skipped"], StopReason.COMPLETED)


class TestProgressCas(unittest.TestCase):
    """批次⑤：progress.json 的 revision CAS（matterloop checkpoint 语义）。"""

    def test_cas_roundtrip_and_stale_write_rejected(self):
        from gcm.db.progress_file import (load_progress, load_progress_with_revision,
                                          save_progress_cas)
        import tempfile
        p = Path(tempfile.mkdtemp()) / "progress.json"
        data, rev = load_progress_with_revision(p)
        self.assertEqual(rev, 0)
        self.assertTrue(save_progress_cas(p, data, rev))
        data2, rev2 = load_progress_with_revision(p)
        self.assertEqual(rev2, 1)
        # 陈旧 revision 写入必须被拒绝（不覆盖对方）
        self.assertFalse(save_progress_cas(p, data2, rev))
        self.assertTrue(save_progress_cas(p, data2, rev2))
        self.assertEqual(load_progress_with_revision(p)[1], 2)

    def test_plain_load_ignores_revision_key(self):
        """普通 load_progress 兼容：_revision 附加键不破坏旧消费方。"""
        from gcm.db.progress_file import load_progress, save_progress_cas, \
            load_progress_with_revision
        import tempfile
        p = Path(tempfile.mkdtemp()) / "progress.json"
        data, rev = load_progress_with_revision(p)
        save_progress_cas(p, data, rev)
        plain = load_progress(p)
        self.assertIn("finished", plain)
        self.assertIn("in_progress", plain)
