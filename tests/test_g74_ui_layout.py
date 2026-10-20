"""G74 UI 布局回归：按钮可见性 / 溢出 / 主题继承 / 状态文本。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QLabel, QScrollArea  # noqa: E402

_app = QApplication.instance() or QApplication([])

from gcm.db.repo_db import Database  # noqa: E402
from gcm.db.settings import SettingsStore  # noqa: E402
from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus  # noqa: E402
from gcm.ui.main_window import MainWindow  # noqa: E402


class TestMainWindowLayout(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_g74_ui_"))
        self.win = MainWindow(
            data_dir=self.tmp, db=Database(self.tmp / "t.db"),
            settings=SettingsStore(self.tmp / "s.json"))
        self.win.show()
        _app.processEvents()

    def tearDown(self):
        try:
            self.win.close()
        except Exception:
            pass

    def _mode(self, w: int, h: int) -> None:
        self.win.resize(w, h)
        self.win._apply_layout_mode()
        _app.processEvents()

    def test_primary_actions_are_outside_top_scroll(self):
        self._mode(1024, 768)
        self.assertTrue(self.win.btn_start.isVisible(),
                        "1024 下「开始」按钮必须可见")
        self.assertFalse(self.win.download_top_scroll.isAncestorOf(
            self.win.btn_start), "主操作按钮不能放在顶部滚动区里")
        self.assertFalse(self.win.download_top_scroll.isAncestorOf(
            self.win.btn_cancel))
        self.assertFalse(self.win.download_top_scroll.isAncestorOf(
            self.win.btn_diag_bundle))

    def test_quick_fill_hidden_in_compact_and_visible_standard(self):
        self._mode(1024, 768)
        self.assertTrue(self.win.quick_scroll.isHidden(),
                        "Compact 下快捷填充应隐藏，避免横向溢出")
        self._mode(1440, 900)
        self.assertFalse(self.win.quick_scroll.isHidden(),
                         "标准宽度下快捷填充应可见")
        self.assertEqual(self.win.quick_scroll.horizontalScrollBarPolicy(),
                         Qt.ScrollBarPolicy.ScrollBarAsNeeded)

    def test_manage_search_min_width(self):
        self.assertGreaterEqual(self.win.manage_panel.manage_search.minimumWidth(), 180)
        self.assertLessEqual(self.win.manage_panel.manage_search.maximumWidth(), 400)

    def test_settings_nav_short_labels_and_no_hscroll(self):
        texts = [self.win.settings_nav.item(i).text()
                 for i in range(self.win.settings_nav.count())]
        self.assertTrue(texts, "设置导航不能为空")
        for t in texts:
            self.assertNotIn("（", t, f"导航项过长：{t}")
        self.assertEqual(self.win.settings_nav.horizontalScrollBarPolicy(),
                         Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def test_progress_detail_column_stretches_and_status_text_plain(self):
        from PyQt6.QtWidgets import QHeaderView
        hdr = self.win.progress_panel.table.horizontalHeader()
        self.assertEqual(hdr.sectionResizeMode(4), QHeaderView.ResizeMode.Stretch)
        spec = RepoSpec(owner="a", repo="b", url_https="https://github.com/a/b.git",
                        folder_name="a__b")
        self.win.progress_panel._prepare_table(1)
        self.win.progress_panel._add_table_row(0, spec)
        self.win.progress_panel._on_worker_result(0, SyncResult(
            spec=spec, status=SyncStatus.SUCCESS, action=SyncAction.CLONED,
            message="克隆完成"))
        text = self.win.progress_panel.table.item(0, 2).text()
        self.assertIn("成功", text)
        for bad in ("✓", "✕", "⚠", "⊘"):
            self.assertNotIn(bad, text)


class TestDialogThemingAndScroll(unittest.TestCase):
    def test_diag_dialog_inherits_theme(self):
        tmp = Path(tempfile.mkdtemp(prefix="gcm_g74_diag_"))
        win = MainWindow(data_dir=tmp, db=Database(tmp / "t.db"),
                         settings=SettingsStore(tmp / "s.json"))
        win.show()
        _app.processEvents()
        try:
            from gcm.ui.diag_dialog import DiagReportDialog
            dlg = DiagReportDialog("t", "b", "c", parent=win, data_dir=tmp)
            self.assertGreater(len(dlg.styleSheet()), 100,
                               "对话框应显式继承主窗 QSS")
            dlg.close()
        finally:
            win.close()

    def test_statistics_content_is_scrollable(self):
        tmp = Path(tempfile.mkdtemp(prefix="gcm_g74_stats_"))
        db = Database(tmp / "t.db")
        try:
            from gcm.ui.statistics_dialog import StatisticsDialog
            dlg = StatisticsDialog(db=db)
            self.assertIsNotNone(dlg.findChild(QScrollArea),
                                 "统计内容必须可滚动，避免雷达图被按钮裁掉")
            dlg.close()
        finally:
            db.close()


class TestG74PolishRegressions(unittest.TestCase):
    """G74 二轮截图审查发现的细节：空标签/数字框箭头/日期列/主题补丁/文案。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_g74_polish_"))
        self.win = MainWindow(
            data_dir=self.tmp, db=Database(self.tmp / "t.db"),
            settings=SettingsStore(self.tmp / "s.json"))
        self.win.show()
        _app.processEvents()

    def tearDown(self):
        try:
            self.win.close()
        except Exception:
            pass

    def test_repo_valid_label_hidden_until_input(self):
        self.assertTrue(self.win.repo_valid_lbl.isHidden(),
                        "空输入时不应占一行（否则 1366 下凭空出现滚动条）")
        self.win.repo_input.setPlainText("https://github.com/a/b")
        self.win._refresh_url_validation()
        self.assertFalse(self.win.repo_valid_lbl.isHidden())
        self.assertIn("有效 1", self.win.repo_valid_lbl.text())
        self.win.repo_input.clear()
        self.win._refresh_url_validation()
        self.assertTrue(self.win.repo_valid_lbl.isHidden())

    def test_manage_time_column_is_wide_enough(self):
        from PyQt6.QtWidgets import QHeaderView
        hdr = self.win.manage_table.horizontalHeader()
        self.assertEqual(hdr.sectionResizeMode(3), QHeaderView.ResizeMode.Fixed)
        self.assertGreaterEqual(self.win.manage_table.columnWidth(3), 150,
                                "太窄会把 2026-10-20 20:47:31 从中间省略")

    def test_settings_spinboxes_hide_native_arrows(self):
        from PyQt6.QtWidgets import QSpinBox
        self.assertEqual(self.win.spin_concurrency.buttonSymbols(),
                         QSpinBox.ButtonSymbols.NoButtons)

    def test_qss_keeps_labels_and_checkboxes_transparent(self):
        qss = self.win._themed_qss()
        self.assertIn("QLabel, QCheckBox, QRadioButton", qss)
        self.assertIn("QSpinBox::up-button", qss)

    def test_retry_dlq_row_shows_action_not_just_title(self):
        from gcm.app.retry_queue import RetryQueue
        from gcm.ui.retry_panel import RetryQueuePanel
        q = RetryQueue(path=self.tmp / "rq.json", enabled=True, max_retries=1,
                       jitter=lambda d: d)
        spec = RepoSpec(owner="o", repo="r", url_https="https://github.com/o/r.git",
                        folder_name="o__r")
        q.record_failure(spec, "Connection timed out")
        q.record_failure(spec, "Connection timed out")
        panel = RetryQueuePanel(queue=q)
        try:
            panel.ck_show_dropped.setChecked(True)
            panel.refresh()
            self.assertEqual(panel.table.rowCount(), 1)
            why = panel.table.item(0, 4).text()
            self.assertIn("网络连接失败", why)
            self.assertTrue("检查网络" in why or "重试" in why,
                            f"已放弃视图也要给出「怎么办」，实际：{why!r}")
        finally:
            panel.close()

    def test_checklist_close_button_is_chinese(self):
        from PyQt6.QtWidgets import QDialogButtonBox

        from gcm.ui.checklist import ITEMS, ChecklistDialog
        dlg = ChecklistDialog({k: False for k, _t, _d, _a in ITEMS}, parent=self.win)
        try:
            box = dlg.findChild(QDialogButtonBox)
            self.assertIsNotNone(box)
            btn = box.button(QDialogButtonBox.StandardButton.Close)
            self.assertEqual(btn.text(), "关闭")
        finally:
            dlg.close()

    def test_repo_detail_path_wraps_and_has_tooltip(self):
        from gcm.ui.repo_detail_dialog import RepoDetailDialog
        long_path = ("C:\\Users\\someone\\a-very-long-directory-name\\another-deep-folder"
                     "\\owner__repo")
        row = {"id": 1, "owner": "o", "repo": "r", "local_path": long_path,
               "host": "github.com", "head_sha": "", "last_sync_at": "",
               "default_branch": ""}
        dlg = RepoDetailDialog(row, [], parent=self.win)
        try:
            found = [lbl for lbl in dlg.findChildren(QLabel)
                     if lbl.toolTip() == long_path]
            self.assertTrue(found, "详情页应保留完整路径 tooltip")
            self.assertIn("…", found[0].text(), "超长路径应中间省略而不是硬裁")
            self.assertFalse(found[0].wordWrap())
        finally:
            dlg.close()


if __name__ == "__main__":
    unittest.main()
