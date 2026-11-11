"""Rev15 R15-03/04：设置页 72/28 工作区与 900px 紧凑导航。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from gcm.db.settings import SettingsStore  # noqa: E402
from gcm.ui.main_window import MainWindow  # noqa: E402


class TestSettingsWorkspace(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gcm_r15_settings_")
        self.dir = Path(self._tmp.name)
        store = SettingsStore(self.dir / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        store.save(s)
        self.win = MainWindow(data_dir=self.dir)
        self.win.show()
        _app.processEvents()
        self.win.tabs.setCurrentIndex(2)
        _app.processEvents()
        _app.processEvents()

    def tearDown(self) -> None:
        self.win.busy = False
        self.win.close()
        self.win.deleteLater()
        _app.processEvents()
        self._tmp.cleanup()

    def test_default_split_is_about_72_28(self) -> None:
        sizes = self.win.settings_workspace.sizes()
        self.assertEqual(len(sizes), 2)
        self.assertGreater(sum(sizes), 0)
        ratio = sizes[0] / sum(sizes)
        self.assertGreaterEqual(ratio, 0.64)
        self.assertLessEqual(ratio, 0.80)

    def test_1024_settings_content_gets_more_room_than_before(self) -> None:
        self.win.resize(1024, 768)
        self.win._apply_layout_mode()
        _app.processEvents()
        self.assertGreaterEqual(self.win.settings_scroll.height(), 300)
        self.assertGreaterEqual(self.win.log_view.height(), 80)

    def test_900_uses_compact_combo_instead_of_hiding_nav(self) -> None:
        self.win.resize(900, 480)
        self.win._apply_layout_mode()
        _app.processEvents()
        self.assertTrue(self.win.settings_nav.isHidden())
        self.assertFalse(self.win.settings_nav_combo.isHidden())
        self.assertEqual(self.win.settings_nav_combo.count(),
                         self.win.settings_nav.count())

    def test_1024_uses_list_nav(self) -> None:
        self.win.resize(1024, 768)
        self.win._apply_layout_mode()
        _app.processEvents()
        self.assertFalse(self.win.settings_nav.isHidden())
        self.assertTrue(self.win.settings_nav_combo.isHidden())

    def test_split_is_persisted_as_ratio(self) -> None:
        ws = self.win.settings_workspace
        ws.setSizes([420, 180])
        expected = [int(v) for v in ws.sizes()]
        ws._save_sizes()
        saved = SettingsStore(self.dir / "settings.json").load().ui_state.get(
            "settings_split")
        self.assertIsInstance(saved, list)
        self.assertEqual(len(saved), 2)
        ratio = expected[0] / sum(expected)
        self.assertAlmostEqual(float(saved[0]), ratio, places=3)

    def test_settings_pane_cannot_collapse_to_zero(self) -> None:
        ws = self.win.settings_workspace
        ws.setSizes([0, 600])
        _app.processEvents()
        self.assertGreaterEqual(ws.sizes()[0], 240,
                                "设置区是主任务，不允许被拖到 0")
        self.assertGreaterEqual(self.win.settings_scroll.height(), 200)

    def test_split_ratio_survives_height_change(self) -> None:
        ws = self.win.settings_workspace
        ws.setSizes([420, 180])
        ws._save_sizes()
        saved = SettingsStore(self.dir / "settings.json").load().ui_state.get(
            "settings_split")
        self.win.resize(1240, 900)
        _app.processEvents()
        ws.apply_saved_sizes()
        sizes = ws.sizes()
        self.assertAlmostEqual(sizes[0] / sum(sizes), float(saved[0]), places=2)


if __name__ == "__main__":
    unittest.main()
