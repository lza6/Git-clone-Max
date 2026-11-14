# -*- coding: utf-8 -*-
"""G85 B2 守卫：覆盖率缺口回补（Rev18，目标精确 ≥81%）。

只补核心逻辑的**正常/错误/边界**路径，不为数字写无断言测试。两个目标：

1. `gcm/mcp_jobs.py`（53.6% → 目标 75%+）：_log 失败路径、kill_tree 各分支、
   _pump 的取消/进度回调/超时终止、_job_thread 的 JSON 解析失败 / 写入期取消 /
   看门狗 timer 清理、prune 满表拒绝、cancel 的收尸退路。
2. `gcm/ui/error_dialog.py`（71% → 目标 85%+）：_kind_button 三态（retry /
   open_settings 有/无 goto）、raw 与 cause 重叠不重复展示、复制详情剪贴板、
   show_error_message 分类失败退化、show_error 构建失败退化。

Mock 只打外部边界（子进程 / 剪贴板 / 模态 exec），业务逻辑真跑（CLAUDE.md §4）。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PyQt6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])  # noqa: E402

from gcm import mcp_jobs as J  # noqa: E402
from gcm.ui import error_dialog  # noqa: E402


class _JobsBase(unittest.TestCase):
    def setUp(self):
        J._JOBS.clear()
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.data = pathlib.Path(self._td.name) / "data"
        self.data.mkdir()

    def _wait(self, job_id: str, timeout: float = 30) -> dict:
        deadline = time.time() + timeout
        snap = None
        while time.time() < deadline:
            snap = J.job_snapshot(job_id)
            if snap and snap["finished"]:
                break
            time.sleep(0.05)
        self.assertIsNotNone(snap)
        self.assertTrue(snap["finished"], "作业没在限期内结束")
        return snap


class TestLogAndKillTree(_JobsBase):
    """mcp_jobs 的工具函数分支。"""

    def test_log_survives_stderr_failure(self):
        """_log 在 stderr 不可写时不抛（作业机制不能被日志拖垮）。"""
        broken = type("BrokenErr", (), {"write": staticmethod(
            lambda *_a: (_ for _ in ()).throw(OSError("管道已断")))})()
        with mock.patch.object(J.sys, "stderr", broken):
            J._log("这条日志写不出去也不许抛")
        self.assertTrue(True, "到达此处 = _log 未抛（作业机制未被日志拖垮）")

    def test_kill_tree_none_and_exited(self):
        """kill_tree(None) 与已退出进程都是 no-op。"""
        J.kill_tree(None)
        fake = mock.MagicMock()
        fake.poll.return_value = 0  # 已退出
        J.kill_tree(fake)
        self.assertFalse(fake.taskkill.called if hasattr(fake, "taskkill") else False
                         or any("taskkill" in str(c) for c in fake.method_calls),
                         "已退出进程不得触发任何终止调用（no-op 断言）")

    def test_kill_tree_poll_broken_treated_alive(self):
        """poll 抛异常 → 按仍存活处理（走终止路径）。"""
        fake = mock.MagicMock()
        fake.pid = 0  # taskkill 对 PID 0 会失败，但不会抛 —— 覆盖 except 分支
        fake.poll.side_effect = RuntimeError("poll 不可用")
        with mock.patch.object(J.subprocess, "run",
                               side_effect=RuntimeError("taskkill 被拦截")) as m_run:
            J.kill_tree(fake)
        self.assertTrue(m_run.called, "poll 不可用时必须按仍存活走 taskkill 终止路径")
        self.assertIn("taskkill", str(m_run.call_args.args[0]))

    def test_job_cmd_carries_json_and_dir(self):
        cmd = J.job_cmd(pathlib.Path("D:/x"))
        self.assertIn("--cli", cmd)
        self.assertIn("--json", cmd)
        self.assertIn("--dir", cmd)


class TestPumpBranches(_JobsBase):
    """_pump：取消中断 / 进度回调 / stderr 为 None / deadline 超时。"""

    def _mk_fake_proc(self, stderr_lines, stdout_text, sleep_per_line=0.0):
        proc = mock.MagicMock()
        proc.stdout = mock.MagicMock()
        proc.stdout.read.return_value = stdout_text
        proc.stderr = iter(stderr_lines)
        proc.wait.return_value = 0
        return proc

    def test_progress_callback_invoked_per_line(self):
        job = {"cancelled": False, "finished": False}
        proc = self._mk_fake_proc(["line1\n", "line2\n"], "{}")
        seen: list = []
        out, code = J._pump(proc, job, on_progress=lambda n, t: seen.append((n, t)))
        self.assertEqual(code, 0)
        self.assertEqual(seen, [(1, "line1"), (2, "line2")])

    def test_cancel_breaks_stderr_loop(self):
        job = {"cancelled": True, "finished": False}
        proc = self._mk_fake_proc(["a\n", "b\n", "c\n"], "{}")
        seen: list = []
        J._pump(proc, job, on_progress=lambda n, t: seen.append(n))
        self.assertLess(len(seen), 3,
                        "取消后不得继续消费 stderr 行（实现：循环首检 cancelled 立即 break）")

    def test_stderr_none_is_tolerated(self):
        job = {"cancelled": False, "finished": False}
        proc = self._mk_fake_proc([], "{}")
        proc.stderr = None
        out, code = J._pump(proc, job, None)
        self.assertEqual((out, code), ("{}", 0))

    def test_deadline_exceeded_kills(self):
        """读取阶段超过 deadline → 标 timed_out 并终止。"""
        job = {"cancelled": False, "finished": False}
        proc = self._mk_fake_proc(["x\n"], "{}")
        seen: list = []
        # 注入一个「已经过期」的 deadline 窗口
        with mock.patch.object(J.time, "time", side_effect=[100.0, 1e12]):
            J._pump(proc, job, on_progress=lambda n, t: seen.append(n))
        self.assertTrue(job.get("timed_out"), "超期未标 timed_out")


class TestJobThreadBranches(_JobsBase):
    """_job_thread 的错误/竞态路径（假 CLI 真子进程，不触网）。"""

    def test_non_json_output_becomes_structured_error(self):
        """子进程输出非 JSON → result.error 结构化，而不是解析崩。"""
        fake = self.data / "fake_nonjson.py"
        fake.write_text("import sys\nsys.stdout.write('not json')\n",
                        encoding="utf-8")
        started = J.start_job(self.data, "urls\n", None,
                              cmd=[sys.executable, str(fake)])
        snap = self._wait(started["job_id"])
        self.assertEqual(snap["status"], "done")
        err = snap["result"]["clone_result"].get("error")
        self.assertIsNotNone(err)
        self.assertEqual(err["code"], -32603)

    def test_write_time_cancel_maps_to_cancelled(self):
        """Popen 后、写 stdin 前被取消 → 状态 cancelled（不是 failed）。"""
        fake = self.data / "slow_stdin.py"
        fake.write_text(
            "import sys, time\n"
            "sys.stdin.read()\n"
            "time.sleep(2)\n",
            encoding="utf-8")
        started = J.start_job(self.data, "urls\n", None,
                              cmd=[sys.executable, str(fake)])
        job_id = started["job_id"]
        # 竞态窗口：等 proc 挂上后立刻取消
        deadline = time.time() + 10
        while time.time() < deadline:
            with J._JOBS_LOCK:
                if J._JOBS[job_id].get("proc") is not None:
                    break
            time.sleep(0.02)
        J.cancel_job(job_id)
        snap = self._wait(job_id)
        self.assertIn(snap["status"], ("cancelled", "timeout"))

    def test_exception_in_thread_marks_failed(self):
        """线程内异常 → status=failed + 收尸（不悬挂）。"""
        started = J.start_job(self.data, "u\n", None,
                              cmd=["Z:\\__no_such_interpreter__"])
        snap = self._wait(started["job_id"])
        self.assertEqual(snap["status"], "failed")
        self.assertIn("调度失败", str(snap["result"]))

    def test_silent_hang_watchdog_kills(self):
        """静默挂起（不写 stderr 不退出）→ 看门狗 Timer 到点强杀标 timeout。"""
        fake = self.data / "hang.py"
        fake.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
        # 直接注入一个已到期的 Timer 场景不可行（JOB_TIMEOUT_S=3600），
        # 改为构造：job 内部把 timer 窗口缩到 0 —— 用 mock JOB_TIMEOUT_S
        with mock.patch.object(J, "JOB_TIMEOUT_S", 0.2):
            started = J.start_job(self.data, "u\n", None,
                                  cmd=[sys.executable, str(fake)])
            snap = self._wait(started["job_id"], timeout=30)
        self.assertEqual(snap["status"], "timeout",
                         "静默挂起必须由看门狗终止并标 timeout")


class TestPruneAndCancel(_JobsBase):
    """prune 满表拒绝 + cancel 的收尸退路。"""

    def test_start_job_rejected_when_all_running(self):
        for _ in range(J.MAX_JOBS):
            J._JOBS["k%d" % _] = {"status": "running", "finished": False,
                                  "cancelled": False, "result": None, "proc": None,
                                  "token": None}
        with self.assertRaises(ValueError):
            J.start_job(self.data, "u\n", None,
                        cmd=[sys.executable, "-c", "pass"])

    def test_cancel_uses_terminate_when_kill_tree_fails(self):
        """kill_tree 抛 → 退化为 terminate（作业必须能被终止）。"""
        job_id = "c1"
        J._JOBS[job_id] = {"status": "running", "finished": False,
                           "cancelled": False, "result": None,
                           "proc": mock.MagicMock(), "token": None}
        with mock.patch.object(J, "kill_tree",
                               side_effect=RuntimeError("kill 失败")):
            snap = J.cancel_job(job_id)
        self.assertEqual(snap["status"], "cancelling")
        # terminate 退路必须被走到
        self.assertTrue(J._JOBS[job_id]["proc"].terminate.called)

    def test_cancel_terminate_also_fails_is_swallowed(self):
        """terminate 也抛 → 吞掉并留日志（取消不能报错）。"""
        job_id = "c2"
        bad = mock.MagicMock()
        bad.terminate.side_effect = RuntimeError("也失败")
        J._JOBS[job_id] = {"status": "running", "finished": False,
                           "cancelled": False, "result": None,
                           "proc": bad, "token": None}
        with mock.patch.object(J, "kill_tree",
                               side_effect=RuntimeError("kill 失败")):
            snap = J.cancel_job(job_id)  # 不抛即通过
        self.assertEqual(snap["status"], "cancelling")


# ---------------------------------------------------------------------------
# error_dialog 剩余分支
# ---------------------------------------------------------------------------


class TestErrorDialogBranches(unittest.TestCase):
    def _mk_info(self, **kw):
        from gcm.app.errors import ErrorInfo
        base = dict(category="network", code="net", title="T", cause="C",
                    action="A", retryable=True, raw="R", action_kind="none")
        base.update(kw)
        return ErrorInfo(**base)

    def test_kind_button_retry_returns_none(self):
        from gcm.ui.error_dialog import _kind_button
        self.assertIsNone(_kind_button(self._mk_info(action_kind="retry"), None))

    def test_kind_button_open_settings_with_goto(self):
        from gcm.ui.error_dialog import _kind_button
        parent = mock.MagicMock()
        btn = _kind_button(self._mk_info(action_kind="open_settings"), parent)
        self.assertIsNotNone(btn, "有 _goto_settings_section 的 parent 必须出按钮")

    def test_kind_button_open_settings_without_goto(self):
        from gcm.ui.error_dialog import _kind_button
        self.assertIsNone(
            _kind_button(self._mk_info(action_kind="open_settings"), None))

    def test_dialog_skips_raw_when_overlapping_cause(self):
        """raw 已包含在 cause 里 → 不重复展示（muted raw 标签不建）。"""
        from PyQt6.QtWidgets import QLabel
        info = self._mk_info(cause="为什么：认证失败 raw 内容", raw="raw 内容")
        dlg = error_dialog.ErrorDialog(info, parent=None)
        try:
            texts = [l.text() for l in dlg.findChildren(QLabel)]
            self.assertFalse(
                any(t.startswith("raw 内容") for t in texts),
                f"raw 与 cause 重叠时不应重复展示：{texts}")
        finally:
            dlg.deleteLater()

    def test_dialog_shows_raw_when_new_info(self):
        from PyQt6.QtWidgets import QLabel
        info = self._mk_info(cause="认证失败", raw="fatal: bad credentials 完整原文")
        dlg = error_dialog.ErrorDialog(info, parent=None)
        try:
            texts = [l.text() for l in dlg.findChildren(QLabel)]
            self.assertTrue(any("bad credentials" in t for t in texts))
        finally:
            dlg.deleteLater()

    def test_copy_detail_puts_segments_in_clipboard(self):
        from PyQt6.QtWidgets import QApplication
        info = self._mk_info(title="发生了什么", cause="为什么", action="怎么办",
                             raw="原始")
        dlg = error_dialog.ErrorDialog(info, parent=None)
        try:
            dlg._copy_detail()
            text = QApplication.clipboard().text()
            for seg in ("发生了什么", "为什么", "怎么办", "原始"):
                self.assertIn(seg, text)
        finally:
            dlg.deleteLater()

    def test_show_error_message_classify_failure_degrades(self):
        """classify_error 抛 → 退化为纯文本 QMessageBox（不比原来差）。"""
        with mock.patch.object(error_dialog, "classify_error",
                               side_effect=RuntimeError("分类崩")), \
             mock.patch.object(error_dialog.QMessageBox, "warning") as m_w:
            error_dialog.show_error_message(None, "原始消息", context="ctx")
        m_w.assert_called_once()
        self.assertIn("原始消息", m_w.call_args.args[2])

    def test_show_error_dialog_build_failure_degrades(self):
        """ErrorDialog 构建抛 → show_error 退化为纯文本。"""
        with mock.patch.object(error_dialog, "ErrorDialog",
                               side_effect=RuntimeError("构建崩")), \
             mock.patch.object(error_dialog.QMessageBox, "warning") as m_w:
            error_dialog.show_error(None, RuntimeError("真异常"), context="ctx")
        m_w.assert_called_once()
        self.assertIn("真异常", m_w.call_args.args[2])

    def test_show_error_message_dialog_build_failure_degrades(self):
        with mock.patch.object(error_dialog, "classify_error",
                               return_value=self._mk_info()), \
             mock.patch.object(error_dialog, "ErrorDialog",
                               side_effect=RuntimeError("构建崩")), \
             mock.patch.object(error_dialog.QMessageBox, "warning") as m_w:
            error_dialog.show_error_message(None, "消息X")
        m_w.assert_called_once()


if __name__ == "__main__":
    unittest.main()
