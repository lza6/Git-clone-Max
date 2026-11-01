"""C2 批守卫（Rev7 §5 C2）：大小列竞态 / 失败动作 / 命令面板 / 批次暂停。

四项都是「用户点下去有反馈」的基础设施，因此断言都落在**行为**上：
不是"有这个方法"，而是"信号真的连上了 / 该类错误真的给这个按钮 /
暂停后真的不再补投 / 搜到的动作真的执行"。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.engine import SyncEngine  # noqa: E402
from gcm.models import RepoSpec  # noqa: E402


def _spec(i: int) -> RepoSpec:
    return RepoSpec(owner=f"o{i}", repo=f"r{i}",
                    url_https=f"https://github.com/o{i}/r{i}.git")


class _QtCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])


# --------------------------------------------------------------------- C2-2
class TestSizeColumnNoRace(_QtCase):
    """C2-2 / U-6：目录大小必须「算完即回填」，不能靠 600ms 定时器去猜。"""

    def _panel(self):
        """用真实 MainWindow 的面板 —— ManagePanel 的 `_build_ui()` 需要 owner
        （图标/主题），单独 `ManagePanel(None)` 构造不出来（本用例第一版就想当然了）。"""
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
        win = MainWindow(data_dir=tmp)
        self.addCleanup(win.close)
        return win.manage_panel

    def test_sizes_ready_is_connected(self):
        p = self._panel()
        self.assertGreater(p.receivers(p.sizes_ready), 0,
                           "sizes_ready 必须连到槽，否则算完也没人回填")

    def test_no_guessing_timer_left_in_source(self):
        """反向自检：竞态根因是「600ms 猜算完」，源码里不得再有这个**调用**。

        注意用 AST 而不是文本匹配 —— 本文件顶部注释就写着 `QTimer.singleShot(600, …)`，
        文本匹配会被自己的说明命中（本项目 D-05 已经踩过一次同样的坑）。
        """
        import ast
        tree = ast.parse((ROOT / "gcm/ui/manage_panel.py").read_text(encoding="utf-8"))
        bad = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if (isinstance(fn, ast.Attribute) and fn.attr == "singleShot"
                    and node.args and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == 600):
                bad.append(node.lineno)
        self.assertEqual(bad, [], f"仍有用 600ms 猜目录大小的地方：line {bad}")

    def test_apply_sizes_writes_model(self):
        import unittest.mock as mock
        p = self._panel()
        p.manage_model = mock.Mock()
        p._apply_sizes([(1, "12.3 MB"), (2, "0 B")])
        p.manage_model.set_size.assert_any_call(1, "12.3 MB")
        p.manage_model.set_size.assert_any_call(2, "0 B")

    def test_cache_ttl_and_invalidate(self):
        p = self._panel()
        if True:
            p._size_cache["/x"] = (0.0, "1 MB")
            self.assertEqual(len(p._size_cache), 1)
            p.invalidate_size_cache()
            self.assertEqual(p._size_cache, {}, "「刷新」必须能清缓存让下次真重算")
            self.assertGreater(p._SIZE_TTL_SEC, 0)


# --------------------------------------------------------------------- C2-4
class TestFailureActionKind(unittest.TestCase):
    """C2-4 / B2-3：每一类错误都要给**真能解决问题**的下一步，而不是一律「重试」。"""

    def test_every_category_maps_to_whitelisted_kind(self):
        from gcm.app import errors
        cats = (errors.CAT_NETWORK, errors.CAT_AUTH, errors.CAT_PLATFORM,
                errors.CAT_LOCAL, errors.CAT_NOT_FOUND, errors.CAT_CANCELLED,
                errors.CAT_OTHER)
        for cat in cats:
            with self.subTest(cat=cat):
                self.assertIn(errors.action_kind_for(cat), errors.ACTION_KINDS)

    def test_auth_points_to_settings_not_retry(self):
        from gcm.app.errors import classify_error
        info = classify_error("HTTP 401 Unauthorized")
        self.assertEqual(info.category, "auth")
        self.assertFalse(info.retryable)
        self.assertEqual(info.action_kind, "open_settings",
                         "认证失败重试无效，必须引导去更新 Token")

    def test_not_found_has_no_action(self):
        from gcm.app.errors import classify_error
        info = classify_error("repository not found")
        self.assertEqual(info.action_kind, "none",
                         "仓库被删除时放一个点了没用的按钮等于骗用户")

    def test_network_points_to_retry(self):
        from gcm.app.errors import classify_error
        self.assertEqual(classify_error("Could not resolve host").action_kind, "retry")

    def test_kind_is_exposed_in_json(self):
        from gcm.app.errors import classify_error
        self.assertIn("action_kind", classify_error("boom").as_dict())


class TestFailurePanelRendersAction(_QtCase):
    def test_widget_type_follows_kind(self):
        from PyQt6.QtWidgets import QLabel, QPushButton
        from gcm.ui.failure_panel import FailurePanel
        p = FailurePanel(None)
        try:
            self.assertIsInstance(p._action_widget("network"), QPushButton)
            self.assertIsInstance(p._action_widget("auth"), QPushButton)
            self.assertIsInstance(p._action_widget("not_found"), QLabel,
                                  "无动作的类别应渲染成占位标签（不是灰按钮）")
        finally:
            p.deleteLater()

    def test_buttons_emit_their_signals(self):
        from gcm.ui.failure_panel import FailurePanel
        p = FailurePanel(None)
        try:
            got = {"retry": [], "settings": [], "diag": []}
            p.retry_category.connect(lambda c: got["retry"].append(c))
            p.open_settings_requested.connect(lambda s: got["settings"].append(s))
            p.diagnostics_requested.connect(lambda: got["diag"].append(1))
            p._action_widget("network").click()
            p._action_widget("auth").click()
            p._action_widget("other").click()
            self.assertEqual(got["retry"], ["network"])
            self.assertEqual(got["settings"], ["network"], "认证 → 去设置（网络分组）")
            self.assertEqual(got["diag"], [1], "未分类错误 → 导出诊断包")
        finally:
            p.deleteLater()


# --------------------------------------------------------------------- C2-6
class TestBatchPauseResume(_QtCase):
    """C2-6 / B2-2：暂停 = 不再补投新任务，在途跑完；与取消不同。"""

    def _engine(self, n: int = 20, concurrency: int = 2):
        e = SyncEngine(Path(tempfile.mkdtemp()) / "clones", concurrency=concurrency)
        started = []

        def _fake(i, s, f):
            started.append(i)
            e._running = e._running_count() + 1

        e._start_worker = _fake          # type: ignore[assignment]
        e.launch([_spec(i) for i in range(n)])
        return e, started

    def test_pause_stops_replenish(self):
        e, started = self._engine(20, 2)
        try:
            self.assertEqual(len(started), 2)
            self.assertTrue(e.pause())
            self.assertTrue(e.paused)
            e._running = 0                # 模拟两个都跑完
            e._replenish()
            self.assertEqual(len(started), 2, "暂停后不得再补投新任务")
        finally:
            e.shutdown()

    def test_resume_tops_up_immediately(self):
        e, started = self._engine(20, 2)
        try:
            e.pause()
            e._running = 0
            self.assertTrue(e.resume())
            self.assertFalse(e.paused)
            self.assertEqual(len(started), 4,
                             "恢复后应立刻补满到上限（原有 2 个在跑 + 再补 2 个）")
            self.assertEqual(e._running_count(), 2)
        finally:
            e.shutdown()

    def test_pause_does_not_cancel_inflight(self):
        e, started = self._engine(10, 2)
        try:
            e.pause()
            self.assertFalse(e._cancelled, "暂停不是取消：不得置取消位")
        finally:
            e.shutdown()

    def test_pause_without_pending_returns_false(self):
        e = SyncEngine(Path(tempfile.mkdtemp()) / "clones", concurrency=2)
        try:
            self.assertFalse(e.pause(), "没有待发任务时暂停无意义")
        finally:
            e.shutdown()

    def test_cancel_after_pause_still_works(self):
        e, _started = self._engine(10, 2)
        try:
            e.pause()
            e.cancel_all()
            self.assertTrue(e._cancelled)
            # 待发队列被清空；在途的 2 个（fake worker 不会 emit finished）仍是 pending
            self.assertEqual(e._pending_queue, [])
            self.assertEqual(e._pending, 2,
                             "取消只清待发队列；在途任务要等 worker 真正结束")
        finally:
            e.shutdown()

    def test_new_launch_clears_pause(self):
        e, started = self._engine(20, 2)
        try:
            e.pause()
            e._cancelled = True       # 让旧批次收敛
            e._pending = 0
            e.busy = False            # launch 有 `if self.busy: return 0` 的前置门
            e.launch([_spec(i) for i in range(5)])   # 新批次
            self.assertFalse(e.paused, "新批次必须从「未暂停」开始")
        finally:
            e.shutdown()

    def test_pause_emits_event(self):
        import unittest.mock as mock
        from gcm.app.event_bus import EventType
        e, _ = self._engine(10, 2)
        try:
            seen = []
            with mock.patch.object(e, "_publish", side_effect=lambda t, **kw: seen.append(t)):
                e.pause()
                e.resume()
            self.assertIn(EventType.BATCH_PAUSED, seen)
            self.assertIn(EventType.BATCH_RESUMED, seen)
            self.assertIn(EventType.BATCH_PAUSED, EventType.ALL,
                          "新事件类型必须登记进 ALL，否则 SSE 过滤会漏")
        finally:
            e.shutdown()


# --------------------------------------------------------------------- C2-5
class TestCommandPalette(_QtCase):
    """C2-5 / B2-6：命令面板读的是**真实控件**，不是硬编码清单。"""

    def test_score_ordering(self):
        from gcm.ui.command_palette import score
        self.assertGreater(score("暂停", "暂停"), score("暂", "暂停"))
        self.assertEqual(score("zzz", "暂停"), 0)
        self.assertEqual(score("", "暂停"), 0, "空查询不打分（由 rank 直接返回全部）")
        self.assertEqual(score("暂", ""), 0)

    def test_rank_filters_and_limits(self):
        from gcm.ui.command_palette import MAX_RESULTS, rank
        items = [("暂停", 1), ("继续", 2), ("打开克隆目录", 3)]
        self.assertEqual([t for t, _ in rank("暂", items)], ["暂停"])
        self.assertEqual(len(rank("", [("a", 1)])), 1)
        self.assertLessEqual(len(rank("", [(f"x{i}", i) for i in range(100)])), MAX_RESULTS)

    def test_collect_actions_from_real_window(self):
        """数据源必须是真实控件 —— 加一个按钮，面板自动多一条（不漂移成第二份真相）。"""
        from gcm.db.settings import SettingsStore
        from gcm.ui.command_palette import collect_actions
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        store.save(s)
        w = MainWindow(data_dir=tmp)
        try:
            w.resize(1440, 900)
            w.show()
            acts = collect_actions(w)
            texts = [t for t, _ in acts]
            self.assertGreater(len(acts), 15, f"收集到的动作太少：{texts[:10]}")
            for must in ("开始并行下载 / 更新", "取消全部", "暂停", "诊断包"):
                self.assertTrue(any(must in t for t in texts),
                                f"命令面板必须能搜到「{must}」：{texts}")
        finally:
            w.close()

    def test_palette_filters_and_executes(self):
        from gcm.ui.command_palette import CommandPalette
        calls = []
        pal = CommandPalette(None, actions=[("暂停批次", lambda: calls.append("paused")),
                                            ("打开克隆目录", lambda: calls.append("open"))])
        try:
            self.assertEqual(pal._refresh("暂"), 1)
            self.assertTrue(pal._run_current())
            self.assertEqual(calls, ["paused"], "回车必须真的执行选中的动作")
            self.assertEqual(pal._refresh("zzz"), 0)
            self.assertFalse(pal._run_current(), "没有结果时不得执行任何动作")
        finally:
            pal.deleteLater()


if __name__ == "__main__":
    unittest.main()


class TestNoTofuGlyphsInUi(_QtCase):
    """UI 文案不得含 emoji / 符号字形（微软雅黑缺字形 → tofu 方块）。

    这是项目自己的视觉规则（CLAUDE.md §6：「状态不得用字符符号 —— ✓ ✕ ⚠ ⊘ 在
    微软雅黑缺字形 → tofu，用 `status_pill.draw_shape` 的几何图形」），但此前只覆盖了
    「状态列」这一处。本轮截图目检又抓到 4 个漏网：`⏸ 暂停选中`（进度表按钮）、
    `暂无失败记录 🎉` / `完成设置 🎉`（空态与向导）、`📦 归档下载`（右键菜单）、
    `⚠ 发现 N 个疑似卡死任务`（失败面板）。全部已改成纯文字。

    判据：`gcm/ui/**` 里的**字符串常量**（排除 docstring —— 那里面举反例是合法的）
    不得命中 emoji / 杂项符号区段。
    """

    #: emoji 与杂项符号/装饰符号的码位区段（含 ✓ ✕ ⚠ ⊘ ⏸ 🎉 📦 等）
    _BANNED = (
        (0x1F000, 0x1FAFF),   # emoji
        (0x2300, 0x23FF),     # ⌚ ⏸ ⏳ 等
        (0x2600, 0x27BF),     # ☀ ⚠ ✓ ✕ ✨ 等
        (0x2B00, 0x2BFF),     # ⬆ ⭐ 等
        (0xFE0F, 0xFE0F),     # 变体选择符（VS16，单独出现即为字形拼接痕迹）
    )

    def _iter_string_constants(self):
        import ast
        for p in sorted((ROOT / "gcm/ui").rglob("*.py")):
            try:
                tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:      # pragma: no cover
                continue
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef,
                                     ast.FunctionDef, ast.AsyncFunctionDef)):
                    d = ast.get_docstring(node, clean=False)
                    if d:
                        docstrings.add(d)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value in docstrings:
                        continue
                    yield p.relative_to(ROOT).as_posix(), node.lineno, node.value

    def test_no_emoji_in_ui_strings(self):
        bad = []
        for f, ln, text in self._iter_string_constants():
            for ch in text:
                cp = ord(ch)
                if any(lo <= cp <= hi for lo, hi in self._BANNED):
                    bad.append(f"{f}:{ln} 含字形 {ch!r}(U+{cp:04X})")
                    break
        self.assertEqual(
            bad, [],
            "UI 文案里出现 emoji/符号字形（微软雅黑缺字形 → 显示成方块）：\n  "
            + "\n  ".join(bad[:10]))

    def test_scan_is_not_hollow(self):
        """反向自检：扫描器必须真的扫到大量 UI 字符串。"""
        count = sum(1 for _ in self._iter_string_constants())
        self.assertGreater(count, 500, f"只扫到 {count} 条 UI 字符串，扫描器可疑")


# --------------------------------------------------------------------- C2-3
class TestUnifiedBusy(_QtCase):
    """C2-3 / B2-1：忙态必须「进入置忙 → 退出**必恢复**」，异常路径也一样。"""

    def _owner(self):
        from PyQt6.QtWidgets import QPushButton
        class _O:
            def __init__(self):
                self.btn_diag_bundle = QPushButton("诊断包")
                self.btn_diag_bundle.setEnabled(True)
        return _O()

    def test_disables_then_restores(self):
        from gcm.ui.busy import busy
        o = self._owner()
        with busy(o, "diag", buttons=("btn_diag_bundle",)):
            self.assertFalse(o.btn_diag_bundle.isEnabled(), "进入忙态必须禁用入口")
        self.assertTrue(o.btn_diag_bundle.isEnabled(), "退出必须恢复")

    def test_restores_even_on_exception(self):
        from gcm.ui.busy import busy
        o = self._owner()
        with self.assertRaises(RuntimeError):
            with busy(o, "diag", buttons=("btn_diag_bundle",)):
                raise RuntimeError("boom")
        self.assertTrue(o.btn_diag_bundle.isEnabled(),
                        "异常路径也必须恢复，否则按钮永久灰掉")

    def test_restores_to_previous_state_not_always_enabled(self):
        """本来就该禁用的按钮不该被点亮（恢复的是进入前的状态）。"""
        from gcm.ui.busy import busy
        o = self._owner()
        o.btn_diag_bundle.setEnabled(False)
        with busy(o, "diag", buttons=("btn_diag_bundle",)):
            pass
        self.assertFalse(o.btn_diag_bundle.isEnabled())

    def test_publishes_job_events(self):
        from gcm.app.event_bus import EventType, get_event_bus
        from gcm.ui.busy import busy
        o = self._owner()
        with busy(o, "export", buttons=("btn_diag_bundle",)):
            pass
        types = [e.type for e in get_event_bus().snapshot(limit=20)]
        self.assertIn(EventType.JOB_STARTED, types)
        self.assertIn(EventType.JOB_FINISHED, types)
        self.assertIn(EventType.JOB_STARTED, EventType.ALL,
                      "新事件类型必须登记进 ALL，否则 SSE 过滤会漏")

    def test_real_entries_use_busy(self):
        """接线断言：诊断包与报表导出两处必须真的走忙态（AST 只看真实调用）。"""
        import ast
        hits = []
        for rel in ("gcm/ui/main_window.py", "gcm/ui/download_tools.py"):
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.With):
                    for item in node.items:
                        ctx = item.context_expr
                        if (isinstance(ctx, ast.Call) and getattr(ctx.func, "id", "") == "_busy"):
                            hits.append(rel)
        self.assertGreaterEqual(len(set(hits)), 2,
                                f"诊断包与报表导出都应接忙态，实际只有：{set(hits)}")
