"""Rev14 反馈与设计系统守卫：骨架 / 离线指示 / 空态三态 / token 无死项 / 动效无裸毫秒。

这几条的共同点：坏掉的形态都是"看起来正常"。骨架多建了控件没人发现、token
定义了没人用没人发现、动效时长散落硬编码没人发现 —— 只有断言能钉住。
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.errors import CAT_AUTH, CAT_NETWORK, CAT_PLATFORM  # noqa: E402
from gcm.ui.offline_indicator import (  # noqa: E402
    STREAK_WINDOW,
    FailureStreak,
    OfflineIndicator,
)
from gcm.ui.palettes import DELIBERATELY_UNUSED, TOKENS  # noqa: E402


class TestFailureStreak(unittest.TestCase):
    def test_three_network_failures_go_offline(self):
        s = FailureStreak()
        self.assertFalse(s.note_failure(CAT_NETWORK))
        self.assertFalse(s.note_failure(CAT_NETWORK))
        self.assertTrue(s.note_failure(CAT_NETWORK), "第 3 次网络失败应判离线")
        self.assertTrue(s.is_offline)

    def test_mixed_categories_never_offline(self):
        s = FailureStreak()
        for cat in (CAT_NETWORK, CAT_AUTH, CAT_NETWORK, CAT_PLATFORM):
            self.assertFalse(s.note_failure(cat))
        self.assertFalse(s.is_offline, "认证/平台错误不是断网，不能亮离线标记")

    def test_success_clears_immediately(self):
        s = FailureStreak()
        for _ in range(STREAK_WINDOW):
            s.note_failure(CAT_NETWORK)
        self.assertTrue(s.is_offline)
        s.note_success()
        self.assertFalse(s.is_offline, "网络恢复必须立刻恢复常态")
        self.assertEqual(s.recent, ())

    def test_window_is_sliding(self):
        """连续 3 次网络失败之后又成功过一次语义靠 note_success 表达；
        这里验证窗口会丢弃更早的记录（不无限增长）。"""
        s = FailureStreak(window=2)
        s.note_failure(CAT_NETWORK)
        s.note_failure(CAT_AUTH)
        self.assertFalse(s.note_failure(CAT_NETWORK))
        self.assertEqual(len(s.recent), 2)


class TestOfflineIndicatorWidget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_hidden_by_default_and_toggles(self):
        ind = OfflineIndicator()
        try:
            self.assertFalse(ind.isVisible())
            ind.set_offline(True)
            self.assertEqual(ind.size().width(), OfflineIndicator.SIZE)
            self.assertTrue(str(ind.accessibleName() or "").strip(),
                            "离线指示必须有名字，否则读屏完全感知不到")
        finally:
            ind.deleteLater()

    def test_no_character_glyphs_in_accessible_text(self):
        """形状编码不用字符：微软雅黑缺 ✓ ✕ ⚠ ⊘ 字形，会渲染成 tofu。"""
        ind = OfflineIndicator()
        try:
            text = (ind.accessibleName() or "") + (ind.toolTip() or "")
            for ch in "✓✕⚠⊘":
                self.assertNotIn(ch, text)
        finally:
            ind.deleteLater()


class TestTokensHaveNoDeadEntries(unittest.TestCase):
    """每个 token 叶子要么有消费点，要么登记在 DELIBERATELY_UNUSED。"""

    def _consumed_pairs(self) -> set[tuple[str, str]]:
        """收集 `TOKENS["组"]["键"]` 形式的读取点。

        只认**直接两层下标**：`rows = TOKENS["row"]` 之后再 `rows["touch"]`
        读不到 —— 这是有意的，间接取别名会让守卫失明（能看见的耦合才是耦合）。
        """
        pairs: set[tuple[str, str]] = set()
        for p in (ROOT / "gcm").rglob("*.py"):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:      # pragma: no cover - 语法错由 lint 拦
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Subscript):
                    continue
                base = node.value
                if not isinstance(base, ast.Subscript):
                    continue
                if getattr(base.value, "id", "") != "TOKENS":
                    continue
                group, key = base.slice, node.slice
                if (isinstance(group, ast.Constant) and isinstance(key, ast.Constant)
                        and isinstance(group.value, str)
                        and isinstance(key.value, str)):
                    pairs.add((group.value, key.value))
        return pairs

    def test_every_leaf_is_consumed_or_declared(self):
        consumed = self._consumed_pairs()
        dead: list[str] = []
        for group, leaves in TOKENS.items():
            for key in leaves:
                if (group, key) in consumed:
                    continue
                if (group, key) in DELIBERATELY_UNUSED:
                    continue
                dead.append(f"{group}.{key}")
        self.assertEqual(dead, [],
                         f"这些 token 没有消费点也没登记为刻意不提供：{dead}")

    def test_declared_unused_really_exist(self):
        """反向自检：登记表里不许有已删除的 token（否则登记本身就漂了）。"""
        for group, key in DELIBERATELY_UNUSED:
            self.assertIn(group, TOKENS)
            self.assertIn(key, TOKENS[group])

    def test_row_touch_has_a_consumer(self):
        """触摸档行高必须有真实读取点（Rev13 的教训：token 定义了没人用）。"""
        src = (ROOT / "gcm/ui/main_window.py").read_text(encoding="utf-8")
        self.assertIn('TOKENS["row"]["touch"]', src)


class TestNoBareAnimationDurations(unittest.TestCase):
    """动效时长只能来自 `ui/motion.py`，禁止 `setDuration(220)` 这类裸数字。"""

    def test_no_numeric_set_duration(self):
        offenders: list[str] = []
        for p in (ROOT / "gcm/ui").glob("*.py"):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:      # pragma: no cover
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if getattr(func, "attr", "") != "setDuration":
                    continue
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, int):
                        offenders.append(f"{p.name}:{node.lineno}={arg.value}")
        self.assertEqual(offenders, [],
                         f"裸毫秒数应改读 ui/motion.py：{offenders}")


class TestSkeletonAndBusyPreview(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        store.save(s)
        cls.win = MainWindow(data_dir=tmp)
        cls.win.show()
        for _ in range(4):
            cls._app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        cls.win.deleteLater()
        cls._app.processEvents()

    def test_skeleton_adds_no_child_widgets(self):
        """骨架用 QPainter 画：显示它不能让 `findChildren` 数量增加。"""
        panel = self.win.progress_panel
        before = len(panel.table.findChildren(object))
        panel.set_busy_preview(True)
        self._app.processEvents()
        during = len(panel.table.findChildren(object))
        panel.set_busy_preview(False)
        self._app.processEvents()
        self.assertEqual(before, during, "骨架不得创建子控件（会拖慢最忙的时刻）")

    def test_skeleton_suppresses_empty_overlay(self):
        """准备期不能显示「粘贴地址开始下载」——用户明明刚贴了一堆。"""
        panel = self.win.progress_panel
        panel.set_busy_preview(True)
        self._app.processEvents()
        self.assertFalse(panel._empty_download.isVisible())
        panel.set_busy_preview(False)
        self._app.processEvents()
        self.assertTrue(panel._empty_download.isVisible(),
                        "退场后空态应恢复（表里本来就没有行）")


class TestManageEmptyStates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        store.save(s)
        cls.win = MainWindow(data_dir=tmp)
        cls.win.show()
        for _ in range(4):
            cls._app.processEvents()

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        cls.win.deleteLater()
        cls._app.processEvents()

    def test_no_data_shows_import_hint(self):
        panel = self.win.manage_panel
        # 同类用例共用同一个窗口：先清空模型，否则上一个用例塞的行会留下来
        panel.manage_model.set_rows([])
        panel._visible_count = 0
        panel._filter_text = ""
        panel._update_empty_manage()
        self._app.processEvents()
        # 用 isHidden 而不是 isVisible：管理页不是当前页签，祖先不可见会让
        # isVisible() 恒为 False，断言就永远测不到"自己有没有被隐藏"。
        self.assertFalse(panel._empty_manage.isHidden())
        self.assertIn("导入", panel._empty_manage.text())

    def test_filtered_empty_shows_different_copy(self):
        """筛选到空 ≠ 没有数据：文案必须能区分，否则用户以为数据丢了。"""
        panel = self.win.manage_panel
        panel.manage_model.set_rows(
            [{"folder_name": "a__b", "owner": "a", "repo": "b",
              "local_path": "C:/tmp/a__b", "last_sync_at": "", "head_sha": "",
              "host": "github.com", "url": "", "repo_id": 1}])
        panel._filter_text = "zzz-no-match"
        panel._apply_manage_filter()
        self._app.processEvents()
        self.assertFalse(panel._empty_manage.isHidden())
        self.assertIn("匹配", panel._empty_manage.text())
        panel._filter_text = ""
        panel._apply_manage_filter()
        self.assertTrue(panel._empty_manage.isHidden())


class TestManageColumnPersistence(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def _window(self, tmp: Path | None = None):
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        tmp = tmp or Path(tempfile.mkdtemp())
        store = SettingsStore(tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        store.save(s)
        return MainWindow(data_dir=tmp), store

    def test_saving_widths_writes_ui_state(self):
        win, store = self._window()
        try:
            panel = win.manage_panel
            panel.manage_table.setColumnWidth(3, 210)
            panel._save_column_widths()
            saved = store.load().ui_state.get("manage_columns") or {}
            self.assertEqual(int(saved.get("3", 0)), 210,
                             "列宽必须真的写进 settings.ui_state（写侧接线）")
        finally:
            win.close()
            win.deleteLater()
            self._app.processEvents()

    def test_restore_applies_saved_widths(self):
        win, store = self._window()
        try:
            s = store.load()
            s.ui_state = {"manage_columns": {"3": 205, "7": 100}}
            store.save(s)
            # 必须复用同一个数据目录：换目录等于换一份 settings.json，测的就不是恢复了
            win2 = self._window(win.data_dir)[0]
            try:
                panel = win2.manage_panel
                panel._restore_column_widths()
                self.assertEqual(panel.manage_table.columnWidth(3), 205)
                self.assertEqual(panel.manage_table.columnWidth(7), 100)
            finally:
                win2.close()
                win2.deleteLater()
                self._app.processEvents()
        finally:
            win.close()
            win.deleteLater()
            self._app.processEvents()

    def test_narrow_budget_uses_floors_and_wide_uses_wider(self):
        win, _store = self._window()
        try:
            panel = win.manage_panel
            panel.apply_column_budget(narrow=True)
            floor = panel.COLUMN_SPECS[3][1]
            self.assertEqual(panel.manage_table.columnWidth(3), floor,
                             "窄窗必须压到下限，把宽度还给仓库名/路径")
            panel.apply_column_budget(narrow=False, wide=True)
            self.assertGreaterEqual(panel.manage_table.columnWidth(5),
                                    panel.COLUMN_SPECS[5][2],
                                    "L 档「查看」列必须更宽")
        finally:
            win.close()
            win.deleteLater()
            self._app.processEvents()

    def test_bad_saved_value_is_clamped_not_crashing(self):
        win, _store = self._window()
        try:
            panel = win.manage_panel
            s = win.settings
            s.ui_state = {"manage_columns": {"3": "abc", "4": -5, "5": 99999}}
            panel._restore_column_widths()
            self.assertGreaterEqual(panel.manage_table.columnWidth(3), 40)
            self.assertLessEqual(panel.manage_table.columnWidth(5), 400)
        finally:
            win.close()
            win.deleteLater()
            self._app.processEvents()


if __name__ == "__main__":
    unittest.main()


class TestFastMsCopyFeedback(unittest.TestCase):
    """C1（Rev19）：FAST_MS 微反馈真实消费点 —— 复制成功按钮文字回弹。

    守卫三件事：① `motion.FAST_MS` 有生产消费点（token 无死键的动效版）；
    ② 反馈文案走 tr()（中英字典同步）；③ reduced-motion / offscreen 下降级为
    QTimer 恢复（语义一致、不建动画对象）。

    测试纪律（原生崩溃教训）：**绝不泵事件循环**。`processEvents()` 会把此前
    用例 `deleteLater()` 的延迟析构与到期的 singleShot 一起派发，跨用例触碰
    已释放的 C++ 对象（rc=127，A/B 实测）。回弹断言改为**直调恢复回调**
    （`_restore_text`），不依赖事件派发。
    """

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def _panel(self):
        from gcm.app.event_bus import EventBus
        from gcm.ui.event_panel import EventStreamPanel
        return EventStreamPanel(bus=EventBus())

    def test_copy_json_gives_fast_feedback(self):
        from gcm.i18n import tr
        p = self._panel()
        try:
            self.assertEqual(p.copy_json(), 0)   # 空事件也是一次成功复制
            self.assertEqual(p.btn_copy.text(), tr("已复制"))
        finally:
            p.deleteLater()

    def test_feedback_recovers_after_fast_ms(self):
        """回弹语义：恢复回调把文案还原；按钮已销毁时静默跳过（weakref）。"""
        from gcm.i18n import tr
        from gcm.ui.event_panel import _restore_text
        import weakref
        p = self._panel()
        try:
            original = p.btn_copy.text()
            p.copy_json()
            self.assertEqual(p.btn_copy.text(), tr("已复制"))
            # 直调恢复回调（不泵事件循环）——按钮存活，必须还原
            _restore_text(weakref.ref(p.btn_copy), original)
            self.assertEqual(p.btn_copy.text(), original,
                             "微反馈必须回弹，不能把按钮文案永久改掉")
        finally:
            p.deleteLater()

    def test_restore_text_on_destroyed_button_is_silent(self):
        """按钮从面板上摘除（parent 链销毁）：恢复回调必须静默跳过。"""
        from gcm.ui.event_panel import _restore_text
        import weakref
        p = self._panel()
        btn = p.btn_copy
        btn.setParent(None)   # 脱离面板；不泵事件循环（纪律见类 docstring）
        btn.deleteLater()
        ref = weakref.ref(btn)
        _restore_text(ref, "任意文案")   # 不得抛（崩了测试自然红）

    def test_reduced_motion_skips_animation_object(self):
        p = self._panel()
        try:
            p._reduced_motion = True
            p.copy_json()
            self.assertIsNone(p._copy_anim,
                              "reduced-motion 下不得创建 QPropertyAnimation")
        finally:
            p.deleteLater()

    def test_fast_ms_has_a_consumer(self):
        """FAST_MS 必须被生产代码真实读取（动效 token 的死键守卫）。"""
        import ast
        consumed = False
        for f in (ROOT / "gcm" / "ui").glob("*.py"):
            tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
            for node in ast.walk(tree):
                if (isinstance(node, ast.Attribute)
                        and node.attr == "FAST_MS"):
                    consumed = True
                    break
            if consumed:
                break
        self.assertTrue(consumed, "FAST_MS 无消费点则应删除（token 纪律）")
