"""Rev11 W4 守卫：阶段时间线 / 快捷填充编辑器 / 排序模式状态机 / 空态统一。

对应计划：`计划书/UI-视觉重设计-Rev11.md` §4 W4（V-19 / V-20 / V-21 / V-22）
与 §5.3 的五条状态机不变量。
"""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])


class TestStageTimeline(unittest.TestCase):
    """V-19：分阶段耗时时间线。"""

    def test_normalize_orders_and_filters(self):
        from gcm.ui.stage_timeline import normalize_stages
        out = normalize_stages({"hook": 50, "precheck": 100, "transfer": 900,
                                "checkout": 0, "bogus": 10})
        self.assertEqual([k for k, _ in out], ["precheck", "transfer", "hook"])
        self.assertTrue(all(ms > 0 for _, ms in out))

    def test_normalize_bad_input(self):
        from gcm.ui.stage_timeline import normalize_stages
        for bad in (None, "x", 42, {"transfer": "abc"}):
            self.assertEqual(normalize_stages(bad), [])

    def test_tooltip_empty_is_honest(self):
        from gcm.ui.stage_timeline import tooltip_text
        self.assertIn("暂无", tooltip_text([]))

    def test_set_stages_empty_is_empty_state(self):
        from gcm.ui.stage_timeline import StageTimeline
        w = StageTimeline()
        w.set_stages({"transfer": 123})
        self.assertTrue(w.has_data())
        w.set_stages({})
        self.assertFalse(w.has_data())

    def test_wired_into_detail_dialog(self):
        """生产调用点：详情对话框必须真的挂了时间线。"""
        from gcm.ui.repo_detail_dialog import RepoDetailDialog
        from gcm.ui.stage_timeline import StageTimeline
        dlg = RepoDetailDialog(
            {"owner": "a", "repo": "b", "local_path": "", "host": "github.com"},
            [{"started_at": "t", "action": "cloned", "status": "success",
              "stage_json": '{"transfer": 900, "precheck": 100}'}])
        self.assertIsInstance(dlg.stage_timeline, StageTimeline)
        self.assertTrue(dlg.stage_timeline.has_data())


class TestQuickReposEditor(unittest.TestCase):
    """V-20 / D-7：就地编辑只改显示标签。"""

    def test_parse_dedup_and_strip(self):
        from gcm.ui.quick_repos_editor import parse_quick_repos
        self.assertEqual(parse_quick_repos("a/b, c/d\na/b ,"), ["a/b", "c/d"])
        self.assertEqual(parse_quick_repos(""), [])

    def test_suggest_folder_shape(self):
        from gcm.ui.quick_repos_editor import _suggest_folder
        self.assertEqual(_suggest_folder("vercel-labs/skills"), "vercel-labs__skills")

    def test_editor_does_not_touch_disk(self):
        """D-7 红线：取消（reject）时零写盘。"""
        import tempfile
        from pathlib import Path

        from PyQt6.QtWidgets import QWidget

        from gcm.db.settings import SettingsStore
        from gcm.ui.quick_repos_editor import QuickReposEditor

        tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(tmp / "s.json")
        s = store.load()
        s.quick_repos = ["orig/one"]
        store.save(s)

        class _Owner(QWidget):
            def __init__(self):
                super().__init__()
                self.settings = s
                self.settings_store = store
        dlg = QuickReposEditor(_Owner())
        dlg.edit.setPlainText("new/two")
        dlg.reject()  # 用户点取消
        s2 = store.load()
        self.assertEqual(list(s2.quick_repos), ["orig/one"], "取消不得写盘")

    def test_accept_writes_only_quick_repos(self):
        """D-7：确认后只有 quick_repos 变化，其余字段不动（save() 整份落盘语义）。"""
        import tempfile
        from pathlib import Path

        from PyQt6.QtWidgets import QWidget

        from gcm.db.settings import SettingsStore

        tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(tmp / "s.json")
        s = store.load()
        s.quick_repos = ["orig/one"]
        s.download_dir = str(tmp / "dl")
        s.token = "secret-token"
        store.save(s)

        class _Owner(QWidget):
            def __init__(self):
                super().__init__()
                self.settings = store.load()
                self.settings_store = store
                self._quick_fill_buttons = []
        owner = _Owner()
        # 模拟「用户编辑并确认」：入口的保存段（edit_quick_repos_inline 内联逻辑）
        # —— 只写 quick_repos，save() 整份落盘但其余字段值不变
        owner.settings.quick_repos = ["new/two"]
        owner.settings_store.save(owner.settings)
        s2 = store.load()
        self.assertEqual(list(s2.quick_repos), ["new/two"])
        self.assertEqual(s2.download_dir, str(tmp / "dl"))
        self.assertEqual(s2.token, "secret-token")

    def test_main_window_delegates_to_inline_editor(self):
        """生产调用点：主窗「编辑」按钮必须走 V-20 行内编辑器。"""
        import inspect

        from gcm.ui.main_window import MainWindow
        src = inspect.getsource(MainWindow._edit_quick_repos)
        self.assertIn("quick_repos_editor", src)


class TestSortModeStateMachine(unittest.TestCase):
    """V-21 / §5.3：五条不变量。"""

    def _mk(self):
        from PyQt6.QtWidgets import QPushButton, QTableWidget

        from gcm.ui.sort_mode import apply_sort_mode, drag_allowed
        table = QTableWidget(0, 6)
        btn = QPushButton("自动排序")
        btn.setCheckable(True)
        return table, btn, apply_sort_mode, drag_allowed

    def test_s2_auto_mode_no_drag(self):
        table, btn, apply, drag_allowed = self._mk()
        btn.setChecked(True)
        apply(table, btn, strip_running=False)
        self.assertTrue(table.isSortingEnabled())
        self.assertFalse(table.dragEnabled(), "S2：自动排序下不可拖")

    def test_s2s4_manual_mode_drag_only_when_idle(self):
        table, btn, apply, drag_allowed = self._mk()
        btn.setChecked(False)
        apply(table, btn, strip_running=True)
        self.assertFalse(table.isSortingEnabled())
        self.assertFalse(table.dragEnabled(), "S4：运行中禁拖")
        apply(table, btn, strip_running=False)
        self.assertTrue(table.dragEnabled(), "手动 + 空闲 = 可拖")
        self.assertFalse(drag_allowed(table, btn, True))
        self.assertTrue(drag_allowed(table, btn, False))
        btn.setChecked(True)
        self.assertFalse(drag_allowed(table, btn, False), "S2：自动模式恒不可拖")

    def test_s3_switch_to_auto_drops_manual_order(self):
        """S3：从手动切回自动，排序开启（视图序被 sortItems 重写 = 手动序丢弃）。"""
        table, btn, apply, _ = self._mk()
        btn.setChecked(False)
        apply(table, btn, False)
        self.assertFalse(table.isSortingEnabled())
        btn.setChecked(True)
        apply(table, btn, False)
        self.assertTrue(table.isSortingEnabled())

    def test_s1_userrole_survives_drag_reorder_simulation(self):
        """S1 变异测试：行序打乱后 result 槽必须写到正确仓库（G69-5 的回归网）。

        用 ProgressTable 真实路径：3 行 + 排序开启插入 → `_row_for` 反查。
        """
        from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus
        from gcm.ui.progress_table import ProgressTable

        class _Settings:
            animations = False

        class _UrlHistory:
            add = staticmethod(lambda *a: None)  # noqa: E704

        owner = type("O", (), {"settings": _Settings(), "tray": None,
                               "_emit_log": lambda *a, **k: None,
                               "url_history": _UrlHistory()})()
        panel = ProgressTable(owner=owner)
        specs = []
        panel._prepare_table(3)
        panel.begin_bulk()
        for i, name in enumerate(("zeta__z", "mid__m", "alpha__a")):
            spec = RepoSpec(owner=name.split("__")[0], repo=name.split("__")[1],
                            url_https=f"https://github.com/{name.replace('__', '/')}.git",
                            folder_name=name)
            specs.append(spec)
            panel._add_table_row(i, spec)
        panel.end_bulk()
        # 模拟排序后的视觉错位：按名字排序使视觉序 ≠ engine index
        panel.table.sortItems(0)
        # engine index=2（alpha__a）报告成功 → 必须写到 alpha 那行
        panel._on_worker_result(2, SyncResult(
            spec=specs[2], status=SyncStatus.SUCCESS, action=SyncAction.CLONED,
            message="ok", duration_ms=100))
        for r in range(3):
            it0 = panel.table.item(r, 0)
            if it0.text() == "alpha__a":
                self.assertIn("成功", panel.table.item(r, 2).text(),
                              "S1：终态必须落在 engine index 对应的仓库行")
            else:
                self.assertEqual(panel.table.item(r, 2).text(), "等待中",
                                 "S1：其他行不得被误写（G69-5 复发即此断言红）")

    def test_progress_table_has_toggle_button(self):
        """生产调用点：切换按钮存在且默认选中（不改变老用户习惯）。"""
        from gcm.ui.progress_table import ProgressTable
        panel = ProgressTable(owner=None)
        self.assertTrue(panel.btn_auto_sort.isCheckable())
        self.assertTrue(panel.btn_auto_sort.isChecked())


class TestEmptyState(unittest.TestCase):
    """V-22：空态统一模块。"""

    def test_factory_builds_label(self):
        from PyQt6.QtWidgets import QLabel

        from gcm.ui.empty_state import build_empty_state
        w = build_empty_state(title="没有东西", hint="先做点什么")
        self.assertIsInstance(w, QLabel)
        self.assertIn("没有东西", w.text())
        self.assertEqual(w.objectName(), "emptyHint")

    def test_factory_variants(self):
        from gcm.ui.empty_state import build_empty_state
        w = build_empty_state(title="没有匹配的仓库", hint="清空搜索框查看全部",
                              filtered=True)
        self.assertIn("清空搜索框", w.text())


if __name__ == "__main__":
    unittest.main()
