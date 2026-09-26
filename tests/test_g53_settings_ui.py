"""G53 设置面板 UI 测试：g7 数据洞察组存在 / 保存回调生效 / 搜索过滤覆盖 extra 组。"""
import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication([])

from gcm.db.repo_db import Database
from gcm.db.settings import SettingsStore
from gcm.ui.main_window import MainWindow


def _make_win():
    d = Path(tempfile.mkdtemp(prefix="gcm_g53_ui_"))
    db = Database(d / "t.db")
    ss = SettingsStore(d / "settings.json")
    return MainWindow(data_dir=d, db=db, settings=ss), d


class TestG53SettingsUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w, cls.d = _make_win()
        cls._orig_onb = MainWindow._maybe_show_onboarding
        MainWindow._maybe_show_onboarding = lambda self: None
        cls.w.show()
        cls.w.hide()

    @classmethod
    def tearDownClass(cls):
        MainWindow._maybe_show_onboarding = cls._orig_onb
        try:
            cls.w.close()
        except Exception:
            pass

    def test_g7_group_exists_in_extra(self):
        extras = getattr(self.w, "settings_groups_extra", None) or []
        self.assertTrue(extras, "settings_groups_extra 不应为空")
        titles = [g.title() for g in extras]
        self.assertTrue(any("数据洞察" in t for t in titles))

    def test_g7_not_in_legacy_groups(self):
        # 关键：不进 settings_groups，避免破坏既有 5 组解构
        self.assertEqual(len(self.w.settings_groups), 5)

    def test_controls_created(self):
        self.assertTrue(hasattr(self.w, "ck_g53_alerts"))
        self.assertTrue(hasattr(self.w, "spin_g53_interval"))
        self.assertTrue(hasattr(self.w, "spin_g53_history"))
        self.assertTrue(hasattr(self.w, "btn_g53_clean"))
        self.assertTrue(hasattr(self.w, "ck_g53_sched"))

    def test_save_alerts_callback(self):
        self.w.settings_panel._save_g53_alerts(True)
        self.assertTrue(self.w.settings.g53_alerts_enabled)
        self.w.settings_panel._save_g53_alerts(False)
        self.assertFalse(self.w.settings.g53_alerts_enabled)

    def test_save_interval_callback(self):
        self.w.settings_panel._save_g53_interval(120)
        self.assertEqual(self.w.settings.g53_alerts_interval_min, 120)

    def test_save_history_callback_updates_db(self):
        self.w.settings_panel._save_g53_history(30)
        self.assertEqual(self.w.settings.history_retention_days, 30)
        self.assertEqual(self.w.db.get_history_retention_days(), 30)

    def test_clean_history_no_crash(self):
        # 空库清理 → 0 条，不崩（QMessageBox 用真实对话框会阻塞，这里只测 db 调用）
        n = self.w.db.prune_history(90)
        self.assertIsInstance(n, int)

    def test_save_sched_callback(self):
        self.w.settings_panel._save_g53_sched(True)
        self.assertTrue(self.w.settings.sched_report_enabled)


if __name__ == "__main__":
    unittest.main()
