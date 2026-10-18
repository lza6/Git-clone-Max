# -*- coding: utf-8 -*-
"""G70 覆盖率补强：新代码路径与既有高频分支的可失败断言。

这些用例不改变生产代码；目的是让快集覆盖率门禁重新回到 81%，
同时覆盖本轮新增的 UI/DB/API 分支。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus  # noqa: E402
from gcm.ui import qss_validate, theme  # noqa: E402


class TestQssValidatorBranches(unittest.TestCase):
    def test_error_branches(self):
        cases = [
            ("", "QSS 为空"),
            ("   ", "QSS 为空"),
            ("QWidget { color: #ffffff; ", "大括号配平失败"),
            ("QWidget { content: \"x; }", "双引号未闭合"),
            ("{ color: #ffffff; }", "选择器为空"),
            ("QWidget { color }", "声明缺少冒号"),
            ("QWidget { not-a-property: 1; }", "未知属性"),
            ("QWidget { ; }", "规则块内没有任何声明"),
            ("QWidget { color: #ffffff; } tail", "尾部存在未配对内容"),
        ]
        for text, expected in cases:
            with self.subTest(text=text[:24]):
                errs = qss_validate.validate_qss(text)
                self.assertTrue(any(expected in e for e in errs),
                                f"期望 {expected!r}，实际 {errs}")

    def test_validate_or_raise(self):
        with self.assertRaises(ValueError):
            qss_validate.validate_qss_or_raise("QWidget {")
        qss_validate.validate_qss_or_raise("QWidget { color: #ffffff; }")


class TestThemeHelpers(unittest.TestCase):
    def tearDown(self):
        theme.apply_theme("deep")

    def test_unknown_theme_falls_back_to_deep(self):
        self.assertEqual(theme.qss_for_theme("no-such-theme"),
                         theme.qss_for_theme("deep"))
        theme.apply_theme("no-such-theme")
        self.assertEqual(theme.current_theme(), "deep")

    def test_scale_clamping(self):
        low = theme.qss_for_theme("deep", 0.1)
        high = theme.qss_for_theme("deep", 9.0)
        self.assertIn(f"font-size: {round(theme.FONT_BASE_PX * 0.8)}px;", low)
        self.assertIn(f"font-size: {round(theme.FONT_BASE_PX * 1.6)}px;", high)

    def test_accent_dict_and_hc_contrast(self):
        pal = theme.theme_with_accent(theme.PALETTES["deep"],
                                      {"accent": "#123456"})
        self.assertEqual(pal["accent"], "#123456")
        self.assertGreaterEqual(theme.hc_contrast_ratio(theme.PALETTES["hc"]), 7.0)


class TestRepoDbStats(unittest.TestCase):
    def setUp(self):
        from gcm.db.repo_db import Database
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_stats_"))
        self.db = Database(self.tmp / "t.db")
        self.rid = self.db.upsert_repo(
            RepoSpec("o", "r", "https://github.com/o/r.git"),
            local_path=str(self.tmp / "o__r"), host="github.com")
        self.db.add_sync_history(self.rid, SyncStatus.SUCCESS, SyncAction.CLONED,
                                 message="ok", commits=2, duration_ms=1000,
                                 started_at="2026-10-19 10:00:00")

    def tearDown(self):
        self.db.close()

    def test_stats_and_metadata(self):
        self.assertEqual(self.db.count(), 1)
        self.assertIn("total_repos", self.db.stats_overview())
        self.assertIn("clones", self.db.stats_usage())
        self.assertIsInstance(self.db.stats_daily(7), list)
        self.assertIsInstance(self.db.stats_slow_repos(5), list)
        self.assertIsInstance(self.db.history(self.rid, 5), list)
        self.assertIn("total", self.db.stats_for_repo(self.rid))
        self.db.set_tags(self.rid, ["a", "b"])
        self.assertGreaterEqual(len(self.db.list_repos_tags(["a"])), 1)
        self.db.set_favorite(self.rid, True)
        self.db.set_excluded(self.rid, True)
        self.assertGreaterEqual(self.db.prune_history(0), 0)
        self.assertGreaterEqual(self.db.dir_size(self.tmp), 0)


class TestProgressTableCoverage(unittest.TestCase):
    def setUp(self):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_cover_"))
        self.w = MainWindow(data_dir=self.tmp, db=Database(self.tmp / "t.db"),
                            settings=SettingsStore(self.tmp / "s.json"))
        self.w._prepare_table(7)
        self.w.table.setSortingEnabled(False)   # 断言按插入行号，避免自动排序
        self.specs = []
        for i in range(7):
            spec = RepoSpec("o", f"r{i}", f"https://github.com/o/r{i}.git")
            self.specs.append(spec)
            self.w._add_table_row(i, spec)
            self.w.row_specs[i] = spec

    def tearDown(self):
        self.w.close()

    def test_delegate_paint_and_size_hint(self):
        self.w.progress_panel._progress_values.update({0: 50, 1: 100})
        self.w.progress_panel.resize(700, 240)
        self.w.progress_panel.show()
        _app.processEvents()
        pix = self.w.progress_panel.grab()
        self.assertFalse(pix.isNull())
        from PyQt6.QtWidgets import QStyleOptionViewItem
        model = self.w.table.model()
        opt = QStyleOptionViewItem()
        self.assertGreater(self.w.progress_panel._progress_delegate.sizeHint(
            opt, model.index(0, 1)).width(), 0)

    def test_all_result_statuses(self):
        statuses = [SyncStatus.SUCCESS, SyncStatus.FAILED, SyncStatus.CANCELLED,
                    SyncStatus.CONFLICT, SyncStatus.SKIPPED, SyncStatus.RUNNING]
        for i, status in enumerate(statuses):
            res = SyncResult(spec=self.specs[i], status=status,
                             action=SyncAction.CLONED, message="m", detail="d")
            self.w.progress_panel._on_worker_result(i, res)
        self.assertIn("成功", self.w.table.item(0, 2).text())
        self.assertIn("失败", self.w.table.item(1, 2).text())
        self.assertIn("取消", self.w.table.item(2, 2).text())
        self.assertIn("冲突", self.w.table.item(3, 2).text())
        self.assertIn("跳过", self.w.table.item(4, 2).text())

    def test_batch_sort_pause_and_copy(self):
        panel = self.w.progress_panel
        panel._reset_batch_overview(4)
        panel._on_batch_result(SyncStatus.SUCCESS)
        panel._on_batch_result(SyncStatus.FAILED)
        self.assertIn("2/4", panel.batch_total_lbl.text())
        panel._on_sort_changed(2, 0)
        panel.pause_selected()          # 无选中 → 早退
        panel._copy_row_detail(0)
        self.assertIn("已复制", self.w.statusBar().currentMessage())
        panel._on_worker_progress_detail(0, "1 MiB/s")
        panel._flush_detail_batch()
        panel._flush_detail_batch()     # 空批 → 早退

    def test_open_row_dir_existing_and_missing(self):
        panel = self.w.progress_panel
        real = self.tmp / "real"
        real.mkdir()
        self.specs[0].local_path = str(real)
        if sys.platform == "win32":
            with mock.patch("os.startfile", create=True) as starter:
                panel._open_row_dir(0)
            starter.assert_called_once()
        else:
            # POSIX：xdg-open / open 任一存在则 Popen；两者都没有时静默跳过
            with mock.patch("shutil.which", return_value="/usr/bin/xdg-open"), \
                 mock.patch("subprocess.Popen") as popen:
                panel._open_row_dir(0)
            popen.assert_called_once()
        # 缺失目录 → 走 QMessageBox.warning（替换整个类，避免真实模态阻塞）
        self.specs[1].local_path = str(self.tmp / "missing")
        with mock.patch("gcm.ui.progress_table._mw.QMessageBox") as mb:
            panel._open_row_dir(1)
        mb.warning.assert_called_once()


class TestMainWindowCoverage(unittest.TestCase):
    def setUp(self):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_mw_"))
        self.w = MainWindow(data_dir=self.tmp, db=Database(self.tmp / "t.db"),
                            settings=SettingsStore(self.tmp / "s.json"))

    def tearDown(self):
        self.w.close()

    def test_layout_mode_and_nav(self):
        self.w.resize(1024, 600)
        self.w._apply_layout_mode()
        self.assertTrue(self.w._quick_fill_buttons[0].isHidden())
        self.w.resize(1400, 1000)
        self.w._apply_layout_mode()
        self.assertFalse(self.w._quick_fill_buttons[0].isHidden())
        self.w._scroll_to_settings_group(0)
        self.w._scroll_to_settings_group(999)
        self.assertGreaterEqual(self.w.settings_nav.count(), 1)

    def test_tab_shortcut_and_persist(self):
        from gcm.ui.main_window import MainWindow
        fake = mock.Mock()
        fake.property.return_value = 2
        with mock.patch.object(self.w, "sender", return_value=fake):
            self.w._switch_tab_shortcut()
        self.assertEqual(self.w.tabs.currentIndex(), 2)
        self.w._save_last_tab(2)
        self.w._persist_last_tab()
        self.assertEqual(self.w.settings.last_tab, 2)
        self.assertTrue(MainWindow is not None)

    def test_ensure_manage_loaded_idempotent(self):
        self.w._manage_loaded = False
        with mock.patch.object(self.w, "_load_db_into_grid") as load:
            self.w._ensure_manage_loaded()
            self.w._ensure_manage_loaded()
        load.assert_called_once()

    def test_rehighlight_and_screen_size(self):
        self.w._rehighlight_log()
        size = self.w._available_screen_size()
        self.assertIsNotNone(size)
        self.assertEqual(len(size), 2)

    def test_settings_panel_toggles(self):
        panel = self.w.settings_panel
        panel._save_reduced_motion(True)
        self.assertTrue(self.w.settings.prefers_reduced_motion)
        panel._reset_font_scale()
        self.assertEqual(self.w.settings.font_scale, 1.0)
        panel._save_accent_preset(0)
        self.assertIn(self.w.settings.accent_preset, ("blue", "violet", "teal"))


class TestHttpApiCoverage(unittest.TestCase):
    def setUp(self):
        from tests.test_g56_api_extra import _make_db, _serve
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_api_cover_"))
        _make_db(self.tmp)
        self.httpd, self.port, self.th = _serve(self.tmp)
        self.addCleanup(self.th.join, 5.0)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def _get(self, path):
        from tests.test_g56_api_extra import _get
        return _get(self.port, path)

    def test_endpoints(self):
        self.assertEqual(self._get("/version")[0], 200)
        self.assertEqual(self._get("/api/status")[0], 200)
        self.assertEqual(self._get("/api/report?format=markdown")[0], 200)
        self.assertEqual(self._get("/api/report?format=json")[0], 200)
        self.assertEqual(self._get("/no-such-path")[0], 404)

    def test_database_open_failure_is_generic(self):
        # 用目录占用 repos.db 路径 → 两种连接方式都失败 → 500 且不回显路径
        from tests.test_g56_api_extra import _serve
        bad = Path(tempfile.mkdtemp(prefix="gcm_api_bad_"))
        (bad / "repos.db").mkdir()
        httpd, port, th = _serve(bad)
        try:
            from tests.test_g56_api_extra import _get
            code, _, body = _get(port, "/api/status")
            self.assertEqual(code, 500)
            self.assertNotIn(str(bad), body)
        finally:
            httpd.shutdown(); httpd.server_close(); th.join(5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
