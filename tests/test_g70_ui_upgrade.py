# -*- coding: utf-8 -*-
"""G70 UI/UX 升级回归：语义色对比度 / 断点 / 进度 delegate / 主题传播 / API 分页。

这些测试全部为**可失败**断言：色值、可见行数、角色数据、响应字段都写精确值，
不依赖“跑通即通过”。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import Qt  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

_app = QApplication.instance() or QApplication([])

from gcm.models import RepoSpec  # noqa: E402
from gcm.ui import qss_validate, theme  # noqa: E402
from gcm.ui.layout import (  # noqa: E402
    clamp_window_size,
    effective_minimum,
    fit_dialog_to_screen,
    layout_mode,
)
from gcm.ui.progress_table import (  # noqa: E402
    DETAIL_ROLE,
    ProgressDelegate,
    ProgressTable,
)


class TestSemanticContrast(unittest.TestCase):
    """G70：所有主题的语义色必须满足 AA（文本 4.5 / UI 边界 3）。"""

    def test_on_accent_text_contrast(self):
        for name, pal in theme.PALETTES.items():
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    theme.contrast_ratio(pal["on_accent"], pal["accent"]), 4.5,
                    f"{name}: 主按钮文字对比度不足")

    def test_muted_text_contrast(self):
        for name, pal in theme.PALETTES.items():
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    theme.contrast_ratio(pal["text_dim"], pal["panel"]), 4.5,
                    f"{name}: 次要文本对面板对比度不足")

    def test_error_text_contrast(self):
        for name, pal in theme.PALETTES.items():
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    theme.contrast_ratio(pal["error"], pal["panel2"]), 4.5,
                    f"{name}: 错误色对控件底色对比度不足")

    def test_border_strong_non_text_contrast(self):
        for name, pal in theme.PALETTES.items():
            with self.subTest(theme=name):
                self.assertGreaterEqual(
                    theme.contrast_ratio(pal["border_strong"], pal["panel2"]), 3.0,
                    f"{name}: 输入框边界对比度不足 3:1")

    def test_accent_preset_recomputes_on_accent(self):
        # 深色主题 + violet：白字 5.05:1，应保持白字；蓝 accent 才用深字
        pal = theme.theme_with_accent(theme.PALETTES["deep"], "violet")
        self.assertEqual(pal["on_accent"], "#ffffff")
        self.assertGreaterEqual(
            theme.contrast_ratio(pal["on_accent"], pal["accent"]), 4.5)


class TestQssContract(unittest.TestCase):
    def test_tab_pane_selector_is_valid(self):
        q = theme.qss_for_theme("deep")
        self.assertIn("QTabWidget::pane", q)
        self.assertNotIn("QTabs::pane", q)

    def test_primary_uses_on_accent(self):
        q = theme.qss_for_theme("deep")
        self.assertIn("color: #0d1117", q)

    def test_selector_typo_is_rejected(self):
        errs = qss_validate.validate_qss(
            "QTabs::pane { background: #000000; color: #ffffff; }")
        self.assertTrue(any("未知控件类名" in e and "QTabs" in e for e in errs), errs)

    def test_font_scale_covers_title_and_console(self):
        q = theme.qss_for_theme("deep", 1.5)
        self.assertIn("font-size: 30px;", q)   # pageTitle 20 × 1.5
        self.assertIn("font-size: 18px;", q)   # console 12 × 1.5


class TestLayoutBreakpoints(unittest.TestCase):
    def test_modes(self):
        self.assertEqual(layout_mode(1019, 700), "unsupported")
        self.assertEqual(layout_mode(1024, 500), "unsupported")  # 高 < 560
        self.assertEqual(layout_mode(1024, 600), "compact")      # 高 < 760
        self.assertEqual(layout_mode(1280, 900), "standard")
        self.assertEqual(layout_mode(1440, 1000), "wide")

    def test_effective_minimum_for_1366x768_at_125pct(self):
        # 逻辑工作区约 1093×614：宽度下限可降到 ~983，高度降到 ~521
        self.assertEqual(effective_minimum(1093, 614), (983, 521))

    def test_clamp_window_size(self):
        w, h = clamp_window_size(2000, 1400, 1280, 680)
        self.assertLessEqual(w, 1216)   # 1280 × 95%
        self.assertLessEqual(h, 646)    # 680 × 95%
        self.assertGreaterEqual(w, 1020)
        self.assertGreaterEqual(h, 560)

    def test_fit_dialog_to_screen(self):
        dlg = QDialog()
        screen = _app.primaryScreen()
        avail = screen.availableGeometry()
        w, h = fit_dialog_to_screen(dlg, 860, 560, 680, 420)
        self.assertLessEqual(w, int(avail.width() * 0.90) or 320)
        self.assertLessEqual(h, int(avail.height() * 0.85) or 240)
        self.assertLessEqual(dlg.minimumWidth(), dlg.width())
        dlg.close()


class TestProgressDelegate(unittest.TestCase):
    def setUp(self):
        self.pt = ProgressTable(owner=None)

    def tearDown(self):
        self.pt.deleteLater()

    def _add(self, names):
        self.pt._prepare_table(len(names))
        for i, name in enumerate(names):
            self.pt._add_table_row(
                i, RepoSpec("o", name, f"https://github.com/o/{name}.git"))

    def test_no_cell_widget_and_delegate_installed(self):
        self._add(["alpha"])
        self.assertIsNone(self.pt.table.cellWidget(0, 1))
        self.assertIsInstance(self.pt.table.itemDelegateForColumn(1), ProgressDelegate)

    def test_progress_and_detail_roles(self):
        self._add(["alpha"])
        self.pt._on_worker_progress(0, 42)
        self.pt._flush_progress_batch()
        self.pt._on_worker_progress_detail(0, "1.2 MiB/s")
        self.pt._flush_detail_batch()
        item = self.pt.table.item(0, 1)
        self.assertEqual(self.pt._progress_values.get(0), 42,
                         "进度值应写入面板字典（delegate 读取）")
        self.assertEqual(item.data(DETAIL_ROLE), "1.2 MiB/s")
        self.assertIsNone(item.data(Qt.ItemDataRole.UserRole + 2),
                          "进度不逐行 setData（500 行单批会阻塞 UI）")

    def _row_of(self, name: str) -> int:
        for r in range(self.pt.table.rowCount()):
            item = self.pt.table.item(r, 0)
            text = item.text() if item is not None else ""
            if text == name or text.endswith(f"__{name}"):
                return r
        raise AssertionError(f"未找到行 {name}")

    def test_filter_uses_latest_text(self):
        self._add(["alpha", "zeta"])
        self.pt._schedule_progress_filter("alpha")
        self.pt._schedule_progress_filter("zeta")
        self.pt._pf_timer.timeout.emit()      # 定时器到点
        # 排序开启时行号会变，按仓库名反查
        self.assertTrue(self.pt.table.isRowHidden(self._row_of("alpha")),
                        "alpha 不应匹配 zeta")
        self.assertFalse(self.pt.table.isRowHidden(self._row_of("zeta")),
                         "zeta 应匹配最新关键字")

    def test_filter_to_zero_shows_empty_state(self):
        self._add(["alpha"])
        self.pt._filter_progress_rows("no-such-repo")
        self.assertFalse(self.pt._empty_download.isHidden(),
                         "过滤到 0 行时必须显示空态提示")
        self.assertIn("没有匹配", self.pt._empty_download.text())
        self.pt._filter_progress_rows("")
        self.assertTrue(self.pt._empty_download.isHidden())

    def test_delegate_maps_engine_index_after_sort(self):
        self._add(["alpha", "zeta"])
        # 排序开启时行号可能已变化；进度必须按行内 engine index 落到正确仓库
        self.pt._progress_values[1] = 77
        zeta_row = self._row_of("zeta")
        alpha_row = self._row_of("alpha")
        model = self.pt.table.model()
        self.assertEqual(
            self.pt._progress_delegate._percent_for(model.index(zeta_row, 1)), 77)
        self.assertEqual(
            self.pt._progress_delegate._percent_for(model.index(alpha_row, 1)), -1)

    def test_accessible_name(self):
        self.assertEqual(self.pt.table.accessibleName(), "下载进度表")
        self.assertEqual(self.pt.progress_filter.accessibleName(), "按仓库名过滤进度")


class _FakeDB:
    def __init__(self, path: Path):
        self.path = path

    def stats_overview(self):
        return {"total_repos": 0, "total_syncs": 0,
                "success_syncs": 0, "failed_syncs": 0, "by_host": {}}

    def stats_usage(self):
        return {"clones": 0, "updates": 0, "commits": 0}

    def stats_daily(self, days=30):
        return []

    def stats_slow_repos(self, limit=10):
        return []

    def list_repos(self, host=None):
        return []

    def history(self, rid, limit=20):
        return []

    def dir_size(self, path):
        return None


class TestStatisticsDialogTheme(unittest.TestCase):
    def tearDown(self):
        theme.apply_theme("deep")

    def test_no_static_palette_or_qss_and_real_newlines(self):
        from gcm.ui import statistics_dialog as sd
        self.assertFalse(hasattr(sd, "PALETTE"), "不得再导入静态 PALETTE")
        self.assertFalse(hasattr(sd, "QSS"), "不得再导入静态 QSS")
        tmp = Path(tempfile.mkdtemp())
        dlg = sd.StatisticsDialog(_FakeDB(tmp))
        text = dlg.lbl_storage.text()
        self.assertNotIn("\\n", text, "存储看板不得出现字面量 \\\\n")
        self.assertIn("\n", text)
        self.assertTrue(dlg.trend_canvas.accessibleName())
        dlg.close()


class _ProbeDialog(QDialog):
    pass


class TestDialogThemePropagation(unittest.TestCase):
    def tearDown(self):
        theme.apply_theme("deep")

    def test_dialog_gets_current_theme_not_static_deep(self):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp())
        w = MainWindow(data_dir=tmp, db=Database(tmp / "t.db"),
                       settings=SettingsStore(tmp / "s.json"))
        try:
            theme.apply_theme("light")
            w.settings.theme = "light"
            w.setStyleSheet(w._themed_qss())
            dlg = _ProbeDialog(w)
            w._apply_theme_to_children()
            self.assertIn("background: #ffffff", dlg.styleSheet())
            self.assertNotIn("background: #0d1117", dlg.styleSheet())
            dlg.close()
        finally:
            w.close()


class TestReadonlyDatabase(unittest.TestCase):
    """G70：HTTP 读端点走只读连接，跳过建表/迁移写事务。"""

    def setUp(self):
        from gcm.db.repo_db import Database
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_ro_"))
        self.path = self.tmp / "repos.db"
        db = Database(self.path)
        db.upsert_repo(RepoSpec("o", "r", "https://github.com/o/r.git"),
                       local_path=str(self.tmp / "o__r"), host="github.com")
        db.close()

    def test_readonly_connection_reads_without_migrate(self):
        import sqlite3
        from unittest import mock
        from gcm.db import repo_db
        with mock.patch.object(repo_db, "_migrate",
                               side_effect=AssertionError("readonly 不应跑迁移")):
            db = repo_db.Database(self.path, readonly=True)
        try:
            rows = db.list_repos()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["owner"], "o")
        finally:
            db.close()
        with self.assertRaises(sqlite3.OperationalError):
            repo_db.Database(self.tmp / "missing.db", readonly=True)

    def test_readonly_connection_rejects_writes(self):
        import sqlite3
        from gcm.db.repo_db import Database
        db = Database(self.path, readonly=True)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                db.upsert_repo(RepoSpec("x", "y", "https://github.com/x/y.git"),
                               local_path=str(self.tmp / "x__y"), host="github.com")
        finally:
            db.close()

    def test_readonly_rejects_old_schema(self):
        import sqlite3
        from gcm.db.repo_db import Database
        old = self.tmp / "old.db"
        conn = sqlite3.connect(str(old))
        conn.execute("CREATE TABLE repos (id INTEGER PRIMARY KEY, owner TEXT)")
        conn.commit()
        conn.close()
        with self.assertRaises(sqlite3.OperationalError):
            Database(old, readonly=True)


class TestRepoDbPaging(unittest.TestCase):
    def test_page_and_total(self):
        from gcm.db.repo_db import Database
        tmp = Path(tempfile.mkdtemp(prefix="gcm_page_"))
        db = Database(tmp / "repos.db")
        try:
            for i in range(5):
                db.upsert_repo(
                    RepoSpec("o", f"r{i}", f"https://github.com/o/r{i}.git"),
                    local_path=str(tmp / f"o__r{i}"), host="github.com")
            db.upsert_repo(RepoSpec("g", "gl", "https://gitlab.com/g/gl.git"),
                           local_path=str(tmp / "gitlab__g__gl"), host="gitlab.com")
            rows, total = db.list_repos_page(limit=2, offset=1)
            self.assertEqual(total, 6)
            self.assertEqual(len(rows), 2)
            rows_gh, total_gh = db.list_repos_page(host="github.com", limit=10)
            self.assertEqual(total_gh, 5)
            self.assertEqual(len(rows_gh), 5)
        finally:
            db.close()


class TestWindowMinimum(unittest.TestCase):
    def test_repo_input_fully_visible_at_min_height(self):
        """G70 审查阻塞项：1020×560 下输入区不能被父容器裁切。"""
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp(prefix="gcm_min_"))
        w = MainWindow(data_dir=tmp, db=Database(tmp / "t.db"),
                       settings=SettingsStore(tmp / "s.json"))
        try:
            w.show()
            _app.processEvents()
            w.resize(1020, 560)
            _app.processEvents()
            visible = w.repo_input.visibleRegion().boundingRect().height()
            self.assertEqual(visible, w.repo_input.height(),
                             "最小窗口下输入框被裁切")
            # 顶部可滚动，tab 的布局最小高度不再把窗口顶到 637
            self.assertIsNotNone(w.download_top_scroll.widget())
            self.assertLessEqual(w.tab_download.minimumSizeHint().height(), 560)
        finally:
            w.close()


class TestHighlighterTheme(unittest.TestCase):
    def tearDown(self):
        theme.apply_theme("deep")

    def test_rehighlight_recolors_existing_blocks(self):
        from PyQt6.QtGui import QTextDocument
        theme.apply_theme("deep")
        doc = QTextDocument("[ERROR] boom https://x")
        hl = theme.make_highlighter(doc)
        hl.rehighlight()
        deep = [r.format.foreground().color().name()
                for r in doc.firstBlock().layout().formats()]
        theme.apply_theme("light")
        hl.rehighlight()
        light = [r.format.foreground().color().name()
                 for r in doc.firstBlock().layout().formats()]
        self.assertIn("#f85149", deep)
        self.assertIn("#58a6ff", deep)
        self.assertIn("#cf222e", light)
        self.assertIn("#0969da", light)
        self.assertNotEqual(deep, light)


class TestMainWindowDeferredLoad(unittest.TestCase):
    def test_manage_loads_on_first_tab_activation(self):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp(prefix="gcm_lazy_"))
        db = Database(tmp / "t.db")
        for i in range(2):
            db.upsert_repo(RepoSpec("o", f"r{i}", f"https://github.com/o/r{i}.git"),
                           local_path=str(tmp / f"o__r{i}"), host="github.com")
        w = MainWindow(data_dir=tmp, db=db, settings=SettingsStore(tmp / "s.json"))
        try:
            self.assertEqual(w.manage_model.rowCount(), 0, "启动时不应加载仓库列表")
            w.tabs.setCurrentIndex(1)
            _app.processEvents()
            self.assertEqual(w.manage_model.rowCount(), 2)
            self.assertTrue(w._manage_loaded)
        finally:
            w.close()


class TestThemeSaveConsistency(unittest.TestCase):
    def tearDown(self):
        theme.apply_theme("deep")

    def test_theme_save_keeps_accent_preset(self):
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp(prefix="gcm_theme_"))
        w = MainWindow(data_dir=tmp, db=Database(tmp / "t.db"),
                       settings=SettingsStore(tmp / "s.json"))
        try:
            w.settings.accent_preset = "violet"
            idx = w.theme_combo.findData("light")
            self.assertGreaterEqual(idx, 0)
            w.settings_panel._save_theme(idx)
            qss = w.styleSheet()
            self.assertEqual(w.settings.theme, "light")
            self.assertIn("#8250df", qss, "切主题后强调色不应退回默认蓝")
            self.assertIn("background: #ffffff", qss)
        finally:
            w.close()


class TestAutostartDeferred(unittest.TestCase):
    def test_refresh_autostart_state(self):
        from unittest import mock
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp(prefix="gcm_auto_"))
        w = MainWindow(data_dir=tmp, db=Database(tmp / "t.db"),
                       settings=SettingsStore(tmp / "s.json"))
        try:
            with mock.patch("gcm.app.autostart.autostart_enabled",
                            return_value=True), \
                 mock.patch("gcm.app.autostart.set_autostart") as sa:
                w._refresh_autostart_state()
            self.assertTrue(w.ck_autostart.isChecked())
            self.assertFalse(sa.called, "回填状态不应写任务计划")
            self.assertTrue(w._autostart_state_loaded)
        finally:
            w.close()


class TestHttpApiPaging(unittest.TestCase):
    def setUp(self):
        from tests.test_g56_api_extra import _make_db, _serve
        self.tmp = Path(tempfile.mkdtemp(prefix="gcm_g70_api_"))
        _make_db(self.tmp)
        self.httpd, self.port, self.th = _serve(self.tmp)
        self.addCleanup(self.th.join, 5.0)
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def _get(self, path):
        from tests.test_g56_api_extra import _get
        return _get(self.port, path)

    def test_paging_fields(self):
        code, _, body = self._get("/api/repos?limit=1&offset=1")
        self.assertEqual(code, 200)
        data = json.loads(body)
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["limit"], 1)
        self.assertEqual(data["offset"], 1)
        self.assertEqual(len(data["repos"]), 1)
        self.assertFalse(data["truncated"], "offset=1 + count=1 已取完，不应标记截断")
        code2, _, body2 = self._get("/api/repos?limit=1&offset=0")
        self.assertEqual(code2, 200)
        self.assertTrue(json.loads(body2)["truncated"], "还有下一页时应标记 truncated")

    def test_invalid_limit_rejected(self):
        for bad in ("0", "5001", "abc"):
            with self.subTest(limit=bad):
                code, _, _ = self._get(f"/api/repos?limit={bad}")
                self.assertEqual(code, 400)

    def test_negative_offset_rejected(self):
        code, _, _ = self._get("/api/repos?offset=-1")
        self.assertEqual(code, 400)

    def test_overlong_numeric_param_rejected(self):
        code, _, body = self._get("/api/repos?limit=" + "9" * 20)
        self.assertEqual(code, 400)
        self.assertNotIn("99999999999999999999", body)


if __name__ == "__main__":
    unittest.main(verbosity=2)
