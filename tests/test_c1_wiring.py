"""C1 批守卫：接线与门禁收口（Rev7 §5 C1）。

覆盖的 7 个引擎级缺陷（每个都对应《下一步改进指南》Rev7 §3/§4 的一条）：

| 用例 | 缺陷 | 修复前的真实表现 |
|------|------|-----------------|
| `TestHostConcurrencyInjection` | X-1 可配置性伪装 | `SyncEngine(host_concurrency=9)._host_concurrency == 4`（构造参数被 `__init__` 末尾硬编码覆盖）；传 0（语义=不限流）也是 4 |
| `TestSetConcurrencyUpdatesAdaptMax` | C-25 | 用户把并发设 3，3 次成功后自适应把它顶回 8 |
| `TestBatchStartLimitDerived` | C-12 | 首批入队上限恒 64，而线程池上限 32 → 最多 63 个 worker 持 Service 干等 |
| `TestDiscardStopsAllTimers` | C-6 | `discard()` 只停 `_retry_timer`，progress/stuck 定时器继续跑 |
| `TestConcurrencyMemoryNotShortCircuited` | C-13 | `_restore_concurrency_state()` 被 `if self._retry_queue is not None:` 短路，默认配置下并发记忆永不恢复 |
| `TestDeadFlushIntervalRemoved` | C-22 | 模块 docstring 宣称「周期落盘」，但 `_FLUSH_INTERVAL` 零引用 |
| `TestWalCheckpointOnBatchFinish` | C-24 | `db.wal_checkpoint()` 有实现零生产调用点，长挂机后 WAL 不收缩 |

**设计取舍：**
- 每条都用**行为断言**（真的构造对象/真的起定时器/真的跑批次），不用「grep 源码里有这行」——
  源码级断言只能证明"写在那"，证不了"起作用"（本项目在 `TestG69_18NoMainWindowRefCycle` 上吃过这个亏）。
- 涉及 Qt 定时器的用例必须先建 `QApplication`，否则 `isActive()` 恒 False（假绿）。
- `_BATCH_START_LIMIT` 的语义从「模块级常量 64」改为「按线程池上限派生」，
  对应更新 `tests/test_g43_engine.py` 的既有断言（**断言精度不降**：仍然断言精确相等，只是基准换成派生值）。
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
from gcm.models import SyncStatus  # noqa: E402


def _engine(**kw):
    return SyncEngine(Path(tempfile.mkdtemp()) / "clones", concurrency=8, **kw)


class TestHostConcurrencyInjection(unittest.TestCase):
    """X-1：`host_concurrency` 构造参数必须真的生效，0 必须真的等于不限流。"""

    def test_injected_value_survives_construction(self):
        e = _engine(host_concurrency=9)
        try:
            self.assertEqual(
                e._host_concurrency, 9,
                "构造参数 host_concurrency=9 被 __init__ 末尾的硬编码覆盖了 —— "
                "per-host 限流实际上不可配置（Rev7 §3.2 X-1）")
        finally:
            e.shutdown()

    def test_zero_means_unlimited(self):
        e = _engine(host_concurrency=0)
        try:
            self.assertEqual(e._host_concurrency, 0,
                             "host_concurrency=0 的语义是「不限流」，不能被改成 4")
            self.assertIsNone(e._host_gate("github.com"),
                              "host_concurrency=0 时 _host_gate 必须返回 None（不限流）")
        finally:
            e.shutdown()

    def test_default_is_four(self):
        """默认值仍是 4 —— 修 X-1 不能顺手把默认行为改掉。"""
        e = _engine()
        try:
            self.assertEqual(e._host_concurrency, 4)
            self.assertIsNotNone(e._host_gate("github.com"))
        finally:
            e.shutdown()


class TestSetConcurrencyUpdatesAdaptMax(unittest.TestCase):
    """C-25：用户显式改并发后，自适应回升的上限必须跟着改，不能顶回旧值。"""

    def test_adapt_max_follows_user_setting(self):
        e = _engine()
        try:
            e.set_concurrency(3)
            self.assertEqual(e._adapt_max, 3,
                             "set_concurrency(3) 后 _adapt_max 仍是旧值 → "
                             "连续成功会被自适应顶回旧并发（C-25）")
            self.assertEqual(e._adapt_current, 3)
        finally:
            e.shutdown()

    def test_adaptation_does_not_exceed_user_setting(self):
        """真实路径：设 3 → 连续 3 次成功 → 并发必须仍是 3（不是 8）。"""
        e = _engine()
        try:
            e.set_concurrency(3)
            e._adapt_current = 1          # 模拟已被网络抖动降到 1
            e._net_ok_streak = 2          # 再成功 1 次即触发回升
            e._adapt_result(SyncStatus.SUCCESS, "ok")
            self.assertEqual(e._adapt_current, 3,
                             "自适应回升必须回到用户设的 3，而不是构造时的 8")
            self.assertEqual(e.pool.maxThreadCount(), 3)
        finally:
            e.shutdown()

    def test_set_concurrency_clamps_to_1_32(self):
        """回归：上下限钳制不能被这次改动破坏。"""
        e = _engine()
        try:
            self.assertEqual(e.set_concurrency(0), 1)
            self.assertEqual(e._adapt_max, 1)
            self.assertEqual(e.set_concurrency(999), 32)
            self.assertEqual(e._adapt_max, 32)
        finally:
            e.shutdown()


class TestBatchStartLimitDerived(unittest.TestCase):
    """C-12：首批入队上限必须由线程池上限派生，不得恒为 64。"""

    def test_start_limit_never_exceeds_pool_capacity(self):
        e = _engine()
        try:
            e.set_concurrency(4)
            limit = e.batch_start_limit()
            self.assertLessEqual(
                limit, e.pool.maxThreadCount(),
                f"首批入队上限 {limit} > 线程池上限 {e.pool.maxThreadCount()} —— "
                "多出来的 worker 会持 Service 空转（C-12）")
        finally:
            e.shutdown()

    def test_start_limit_scales_with_concurrency(self):
        e = _engine()
        try:
            for n in (2, 8, 16, 32):
                e.set_concurrency(n)
                self.assertEqual(e.batch_start_limit(), n,
                                 f"并发 {n} 时首批上限应等于 {n}（超出部分是纯浪费）")
        finally:
            e.shutdown()

    def test_launch_only_starts_up_to_limit(self):
        """真实路径：并发 3 + 20 个任务 → 只启动 3 个，其余进待发队列。"""
        from gcm.models import RepoSpec
        e = _engine()
        try:
            e.set_concurrency(3)
            started = []
            e._start_worker = lambda i, s, f: started.append(i)  # type: ignore[assignment]
            specs = [RepoSpec(owner=f"o{i}", repo=f"r{i}", url_https="https://github.com/o/r")
                     for i in range(20)]
            e.launch(specs)
            self.assertEqual(len(started), 3,
                             "并发 3 时首批只应启动 3 个 worker")
            self.assertEqual(len(e._pending_queue), 17)
        finally:
            e.shutdown()


class TestDiscardStopsAllTimers(unittest.TestCase):
    """C-6：`discard()` 必须让本引擎彻底安静（三个定时器全停）。

    `discard()` 的调用点在 `MainWindow._launch`（替换引擎），
    若旧引擎的心跳/巡检定时器还在跑，会在新引擎上继续 emit 过期进度。
    """

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_all_timers_stopped(self):
        e = _engine()
        try:
            e._progress_timer.start()
            e._stuck_timer.start()
            e._retry_timer.start()
            self.assertTrue(e._progress_timer.isActive())
            self.assertTrue(e._stuck_timer.isActive())
            self.assertTrue(e._retry_timer.isActive())

            e.discard()

            self.assertFalse(e._progress_timer.isActive(), "progress 心跳定时器没停")
            self.assertFalse(e._stuck_timer.isActive(), "卡死巡检定时器没停")
            self.assertFalse(e._retry_timer.isActive(), "重试轮询定时器没停")
        finally:
            e.shutdown()


class TestConcurrencyMemoryNotShortCircuited(unittest.TestCase):
    """C-13：并发记忆的「恢复」不得依赖重试队列是否启用。"""

    def test_restore_runs_without_retry_queue(self):
        d = Path(tempfile.mkdtemp())
        state = d / "concurrency_state.json"
        state.write_text('{"concurrency": 3}', encoding="utf-8")
        e = SyncEngine(d / "clones", concurrency=8,
                       concurrency_state_path=state)
        try:
            self.assertEqual(
                e._adapt_current, 3,
                "上次会话被降到 3 并发，重启后应恢复 3；"
                "当前被 `if self._retry_queue is not None` 短路了（C-13）")
        finally:
            e.shutdown()

    def test_restore_still_respects_upper_bound(self):
        """回归：恢复值不得超过用户当前设置的并发上限。"""
        d = Path(tempfile.mkdtemp())
        state = d / "concurrency_state.json"
        state.write_text('{"concurrency": 30}', encoding="utf-8")
        e = SyncEngine(d / "clones", concurrency=4,
                       concurrency_state_path=state)
        try:
            self.assertEqual(e._adapt_current, 4,
                             "记忆值 30 > 当前上限 4 → 必须被钳到 4")
        finally:
            e.shutdown()


class TestDeadFlushIntervalRemoved(unittest.TestCase):
    """C-22：`_FLUSH_INTERVAL` 是死常量（模块 docstring 却宣称「周期落盘」）。"""

    def test_constant_is_gone_or_used(self):
        import gcm.app.engine as mod
        self.assertFalse(
            hasattr(mod, "_FLUSH_INTERVAL"),
            "_FLUSH_INTERVAL 是零引用的死常量，应删除（落盘节流已由 "
            "_progress_flush_maybe 的 _PROGRESS_FLUSH_MIN_INTERVAL 承担）")

    def test_module_docstring_does_not_overclaim(self):
        import gcm.app.engine as mod
        doc = mod.__doc__ or ""
        self.assertNotIn(
            "周期落盘间隔", doc,
            "模块 docstring 不得再宣称存在一个已删除的周期落盘机制")


class TestWalCheckpointOnBatchFinish(unittest.TestCase):
    """C-24：批次正常收尾必须触发一次 `wal_checkpoint(PASSIVE)`。

    长挂机 + 未结束事务会让 WAL 涨到几十 MB 不收缩；被动 checkpoint 是安全操作
    （不阻塞读者、不截断正在被读的帧），故放在批次收尾而不在每任务结束。
    """

    def test_checkpoint_called_on_finish(self):
        import unittest.mock as mock
        e = _engine()
        try:
            db = mock.Mock()
            db.wal_checkpoint.return_value = {"busy": 0, "log": 0, "checkpointed": 0}
            e.db = db
            e._batch_total = 1
            e._cancelled = False
            e._pending = 0
            with mock.patch.object(e, "flush_all_buffers"), \
                    mock.patch.object(e, "flush_progress"), \
                    mock.patch.object(e, "_session_mark_completed"), \
                    mock.patch.object(e, "_run_progress_gc"), \
                    mock.patch.object(e, "_persist_concurrency_state"), \
                    mock.patch.object(e, "_publish"), \
                    mock.patch.object(e, "_stop_buffers"), \
                    mock.patch.object(e, "_release_tasks"):
                e.finished.connect(lambda: None)
                e._on_worker_done()
            db.wal_checkpoint.assert_called_once_with()
        finally:
            e.shutdown()

    def test_checkpoint_failure_does_not_break_finish(self):
        """WAL 收缩失败绝不能把批次收尾带崩（数据库已关/被占用是真实场景）。"""
        import unittest.mock as mock
        e = _engine()
        try:
            db = mock.Mock()
            db.wal_checkpoint.side_effect = RuntimeError("connection closed")
            e.db = db
            e._batch_total = 1
            e._cancelled = False
            e._pending = 0
            with mock.patch.object(e, "flush_all_buffers"), \
                    mock.patch.object(e, "flush_progress"), \
                    mock.patch.object(e, "_session_mark_completed"), \
                    mock.patch.object(e, "_run_progress_gc"), \
                    mock.patch.object(e, "_persist_concurrency_state"), \
                    mock.patch.object(e, "_publish"), \
                    mock.patch.object(e, "_stop_buffers"), \
                    mock.patch.object(e, "_release_tasks"):
                e._on_worker_done()   # 不得抛
        finally:
            e.shutdown()


if __name__ == "__main__":
    unittest.main()


class TestSettingsNetworkWiring(unittest.TestCase):
    """C1-2：`host_concurrency` / `time_budget_min` 的**三段接线**全通。

    三段 = ①控件存在；②控件变更 → 保存回调 → 落盘；③消费方（引擎）真的变了。
    只断①②是本项目历史上反复出现的「伪闭环」形态（见 `.agent/skills/plan-doc-audit.md` §3）。
    """

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def _win(self):
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
        return MainWindow(data_dir=tmp), store

    def test_widgets_exist_and_ranges_come_from_validators(self):
        from gcm.db.validators import int_range
        w, _ = self._win()
        try:
            self.assertEqual(w.spin_host_concurrency.minimum(),
                             int_range("host_concurrency")[0])
            self.assertEqual(w.spin_host_concurrency.maximum(),
                             int_range("host_concurrency")[1])
            self.assertEqual(w.spin_time_budget.minimum(),
                             int_range("time_budget_min")[0])
            self.assertEqual(w.spin_time_budget.maximum(),
                             int_range("time_budget_min")[1])
        finally:
            w.close()

    def test_host_concurrency_persists_and_applies_to_engine(self):
        """改控件 → 落盘 → 引擎的 per-host 闸门真的换成新容量。"""
        w, store = self._win()
        try:
            w.spin_host_concurrency.setValue(7)
            self.assertEqual(store.load().host_concurrency, 7,
                             "改控件后 settings.json 里必须是 7（写侧断链）")
            e = w.engine
            self.assertIsNotNone(e, "主窗启动后必须已有引擎（否则本用例会假绿）")
            self.assertEqual(e._host_concurrency, 7,
                             "运行中的引擎必须应用新值（消费侧断链）")
            gate = e._host_gate("github.com")
            self.assertIsNotNone(gate)
            self.assertEqual(gate._value, 7)   # Semaphore 内部计数
        finally:
            w.close()

    def test_time_budget_persists_and_applies_to_engine(self):
        w, store = self._win()
        try:
            w.spin_time_budget.setValue(15)
            self.assertEqual(store.load().time_budget_min, 15)
            e = w.engine
            self.assertIsNotNone(e)
            self.assertAlmostEqual(e.time_budget, 900.0, places=3,
                                   msg="15 分钟必须换算成 900 秒传给引擎")
        finally:
            w.close()

    def test_zero_means_unlimited_end_to_end(self):
        """0 的语义必须一路通到引擎：per-host 闸门返回 None、时间预算为 0。"""
        w, _ = self._win()
        try:
            w.spin_host_concurrency.setValue(0)
            w.spin_time_budget.setValue(0)
            e = w.engine
            self.assertIsNotNone(e)
            self.assertIsNone(e._host_gate("github.com"))
            self.assertEqual(e.time_budget, 0.0)
        finally:
            w.close()


class TestStuckTaskUiEntry(unittest.TestCase):
    """C1-3：卡死任务必须有 UI 入口（`cancel_stuck_tasks` 此前零生产调用点）。"""

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def test_failure_panel_has_stuck_bar_hidden_by_default(self):
        from gcm.ui.failure_panel import FailurePanel
        p = FailurePanel(None)
        try:
            self.assertFalse(p.stuck_row.isVisible(),
                             "没有卡死任务时这条不该占位")
            self.assertEqual(p._refresh_stuck(), [])
        finally:
            p.deleteLater()

    def test_stuck_bar_shows_and_cancels(self):
        """有卡死任务 → 显示 + 点按钮真的调用引擎的 cancel_stuck_tasks。"""
        import unittest.mock as mock
        from gcm.ui.failure_panel import FailurePanel
        owner = mock.Mock()
        owner.engine.stuck_task_keys.return_value = ["a__x", "b__y"]
        owner.engine.cancel_stuck_tasks.return_value = 2
        p = FailurePanel(owner)
        try:
            keys = p._refresh_stuck()
            self.assertEqual(keys, ["a__x", "b__y"])
            self.assertIn("2", p.stuck_lbl.text())
            self.assertIn("a__x", p.stuck_lbl.text())
            n = p._on_cancel_stuck()
            self.assertEqual(n, 2)
            owner.engine.cancel_stuck_tasks.assert_called_once_with()
        finally:
            p.deleteLater()

    def test_ui_module_references_cancel_stuck_tasks(self):
        """接线断言（AST）：`gcm/ui/` 必须真的引用 `cancel_stuck_tasks`。

        这是 `TestG69_18WiringClosure` 的同款口径 —— 「只在 tests 里出现 = 虚伪实现」。
        """
        import ast
        src = (ROOT / "gcm/ui/failure_panel.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        found = {n.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Attribute) and n.attr == "cancel_stuck_tasks"}
        self.assertIn("cancel_stuck_tasks", found,
                      "failure_panel 必须调用 engine.cancel_stuck_tasks（否则该能力对用户不存在）")


class TestDependencyPinning(unittest.TestCase):
    """C1-12：依赖与 CI 的静态检查器必须锁版本（Rev7 §4 B1-3 的等价物）。

    背景：`requirements.txt` 曾长期写 `PyQt6>=6.6`（源码用户装到什么版本随缘，
    而 CI 锁 6.11.0）；CI 的 Lint/Type 步骤**裸装** `pip install ruff` / `mypy`
    —— 于是「requirements-build.txt 里有锁」被误当成「CI 已锁」（G80 实测登记过的坑）。

    判据：
    1. 两个 requirements 文件的每条依赖都必须有 `==` 精确版本；
    2. CI 不得再裸装 ruff/mypy，必须走 `-r requirements-build.txt`；
    3. PyQt6 的版本在 `requirements.txt` 与 CI 里必须**同一串**（单一真相）。
    """

    ROOT = ROOT

    def _deps(self, rel: str) -> list[str]:
        out = []
        for line in (self.ROOT / rel).read_text(encoding="utf-8").splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                out.append(line)
        return out

    def test_runtime_requirements_are_pinned(self):
        deps = self._deps("requirements.txt")
        self.assertTrue(deps, "requirements.txt 不能为空")
        unpinned = [d for d in deps if "==" not in d]
        self.assertEqual(
            unpinned, [],
            "运行时依赖必须锁精确版本（>= 会让源码用户与 CI 装到不同版本）："
            f"{unpinned}")

    def test_build_requirements_are_pinned(self):
        unpinned = [d for d in self._deps("requirements-build.txt") if "==" not in d]
        self.assertEqual(unpinned, [], f"构建依赖必须锁精确版本：{unpinned}")

    def test_ci_does_not_bare_install_linters(self):
        text = (self.ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        offenders = [
            ln.strip() for ln in text.splitlines()
            if "pip install" in ln and any(t in ln for t in ("ruff", "mypy"))
            and "requirements-build.txt" not in ln
            and not ln.strip().startswith("#")      # 注释里提到它不算（本例的修复说明就写在注释里）
        ]
        self.assertEqual(
            offenders, [],
            "CI 不得裸装 ruff/mypy（版本会随 PyPI 最新版漂移）：" + "；".join(offenders))
        self.assertIn("pip install -r requirements-build.txt", text,
                      "CI 必须从 requirements-build.txt 安装锁定版本的静态检查工具")

    def test_pyqt_version_is_single_source(self):
        """`requirements.txt` 的 PyQt6 版本 == CI 里 `pip install PyQt6==X` 的 X。"""
        import re
        req = (self.ROOT / "requirements.txt").read_text(encoding="utf-8")
        m_req = re.search(r"PyQt6==(\d+\.\d+\.\d+)", req)
        self.assertIsNotNone(m_req, "requirements.txt 必须锁 PyQt6==X.Y.Z")
        text = (self.ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        versions = set(re.findall(r"pip install PyQt6==(\d+\.\d+\.\d+)", text))
        self.assertTrue(versions, "CI 必须锁 PyQt6==X.Y.Z")
        self.assertEqual(
            versions, {m_req.group(1)},
            f"CI 里的 PyQt6 版本 {sorted(versions)} 与 requirements.txt "
            f"{m_req.group(1)} 不一致（双口径）")


class TestNoMarkdownInUserFacingText(unittest.TestCase):
    """用户可见文案不得残留 Markdown 标记（`**粗体**` / `|表格|`）。

    **为什么这是 bug 而不是风格问题**：`QLabel` / `setToolTip` / `QMessageBox`
    都是**纯文本**渲染，`**` 会原样显示成两个星号。本轮视觉验收就抓到一个实例 ——
    新加的限流提示行在真机截图里显示成「以上两项对**下一个开始的任务**生效」，
    而当时 27 条单测全绿（测试断言的是控件存在与取值，没人断言"文案里别带星号"）。

    顺带修掉一个同类的既有残留：`settings_actions.py` 的克隆后钩子确认框
    （「这条命令会在**每个**仓库克隆成功后…」）。
    """

    def _tr_literals(self):
        import ast
        for p in sorted((ROOT / "gcm").rglob("*.py")):
            if "i18n" in p.as_posix():
                continue
            try:
                tree = ast.parse(p.read_text(encoding="utf-8"))
            except SyntaxError:      # pragma: no cover
                continue
            for node in ast.walk(tree):
                if (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr"
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    yield p.as_posix(), node.lineno, node.args[0].value

    def test_no_bold_markers_in_tr_literals(self):
        bad = [(f, ln, v[:40]) for f, ln, v in self._tr_literals() if "**" in v]
        self.assertEqual(
            bad, [],
            "用户可见文案里出现 Markdown 粗体标记（QLabel/QMessageBox 是纯文本，会显示成星号）："
            f"{bad}")

    def test_guard_is_not_hollow(self):
        """反向自检：本仓库确实有大量 tr() 字面量，扫描器不是空转。"""
        self.assertGreater(len(list(self._tr_literals())), 300)


class TestStartupEngineReflectsSettings(unittest.TestCase):
    """设置 → 引擎在**启动路径**上也要通（本轮真机实测抓到的第二个伪闭环）。

    发现过程：改设置后重启应用，控件回填正确（11/20）而引擎仍是默认值（4 / 0s）——
    因为 `MainWindow.__init__` 手写着**第二份**参数列表，与 `engine_factory`
    那份慢慢漂移，漏掉了 `host_concurrency` / `time_budget` / `concurrency_state_path`。
    单测当时全绿：它们改的是**运行中**引擎（走 `_launch` → 工厂），恰好绕开了启动路径。

    现在启动也走工厂（唯一映射点），本用例锁死这条路径。
    """

    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls._app = QApplication.instance() or QApplication([])

    def _win_with(self, **overrides):
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp())
        store = SettingsStore(tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        for k, v in overrides.items():
            setattr(s, k, v)
        store.save(s)
        return MainWindow(data_dir=tmp)

    def test_startup_engine_reads_persisted_limits(self):
        w = self._win_with(host_concurrency=11, time_budget_min=20)
        try:
            self.assertEqual(w.engine._host_concurrency, 11,
                             "启动时的引擎必须读到持久化的 host_concurrency（否则重启即失效）")
            self.assertAlmostEqual(w.engine.time_budget, 1200.0, places=3)
            gate = w.engine._host_gate("github.com")
            self.assertIsNotNone(gate)
            self.assertEqual(gate._value, 11)
        finally:
            w.close()

    def test_idle_engine_has_no_retry_queue_even_if_enabled(self):
        """空闲引擎不挂重投队列：启动时 `_last_batch_ctx` 为空，自动重投会污染 host 列。

        （G67-3 修过的同类缺陷：重投必须复用上一批的 host_by_key / fetch_depth / mirror。）
        """
        w = self._win_with(retry_enabled=True)
        try:
            self.assertIsNone(w.engine.retry_queue,
                              "启动期空闲引擎不得挂重试队列（会拿到空的 _last_batch_ctx）")
        finally:
            w.close()

    def test_batch_engine_does_have_retry_queue_when_enabled(self):
        """对照组：真正跑批次的那条路径（工厂默认 enable_retry_queue=True）必须有队列。"""
        import unittest.mock as mock
        from gcm.ui.engine_factory import build_sync_engine
        owner = mock.Mock()
        owner.settings = mock.Mock(
            concurrency=8, fetch_timeout=300, clone_timeout=600, time_budget_min=0,
            host_concurrency=4, retries=2, proxy="", token="", submodule=False,
            rate_limit_kbps=0, host_tokens={}, mirror_prefix={},
            mirror_use_presets=True, mirror_fallback=True, mirror_probe_timeout=8,
            host_auth_types={}, precheck_remote=False, force_ipv4=False,
            lfs_enabled=False, post_clone_hook="", ssh_key_path="",
            retry_enabled=True, retry_max=2, retry_backoff_base_sec=30,
            watchdog_enabled=False, watchdog_interval_min=5)
        owner.db = None
        owner.progress_path = None
        owner.data_dir = Path(tempfile.mkdtemp())
        e = build_sync_engine(owner, owner.data_dir / "clones",
                              single_branch=False, mirror=False)
        try:
            self.assertIsNotNone(e.retry_queue,
                                 "批次引擎必须挂上重试队列（否则自动重试功能形同虚设）")
        finally:
            e.shutdown()

    def test_startup_engine_restores_concurrency_memory(self):
        """C1-9 在启动路径上的端到端验证：上次的并发值会被恢复。"""
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow
        tmp = Path(tempfile.mkdtemp())
        (tmp / "concurrency_state.json").write_text('{"concurrency": 3}', encoding="utf-8")
        store = SettingsStore(tmp / "settings.json")
        s = store.load()
        s.first_run_done = True
        s.checklist_prompted = True
        s.animations = False
        s.minimize_to_tray = False
        s.concurrency = 8
        store.save(s)
        w = MainWindow(data_dir=tmp)
        try:
            self.assertEqual(w.engine._adapt_current, 3,
                             "启动时应恢复上次的并发（C1-9）；天花板仍是用户设的 8")
            self.assertEqual(w.engine._adapt_max, 8)
        finally:
            w.close()
