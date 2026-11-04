"""G84-13：MCP 长任务（预览 / 异步作业 / 取消 / 审计）的守卫。

历史问题（G84 实测）：`clone_batch` 在 stdio 循环里同步 `subprocess.run(timeout=3600)`
→ 克隆几百个仓库时整条 MCP 通道最长 1 小时不响应：没有 progress、不能取消、
连 `ping` 都不回；客户端超时断开，而子进程还在后台写盘，没人能停。

本文件守的是"契约只增不改"：
- 不传 `async` 时行为与老客户端完全一致（同步返回、同一份顶层契约）；
- `dry_run` / `async` / `job_status` / `job_cancel` 都是**加性**字段与工具；
- 两个新工具里 `job_cancel` 属写操作，未放行时**不存在于 tools/list**（与 clone_batch 同一门槛）。
"""
from __future__ import annotations

import pathlib
import sys
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm import mcp_jobs as J  # noqa: E402
from gcm import mcp_server as M  # noqa: E402

DATA = ROOT / "data"


def _tool_names(tools: list[dict]) -> list[str]:
    return [t["name"] for t in tools]


class TestToolSurface(unittest.TestCase):
    def test_readonly_surface_excludes_mutating_tools(self):
        names = _tool_names(M._readonly_tools())
        self.assertNotIn("clone_batch", names)
        self.assertNotIn("job_cancel", names)
        self.assertIn("job_status", names, "job_status 是只读的，应始终可见")

    def test_full_surface_has_async_tools(self):
        names = _tool_names(M._TOOLS)
        for n in ("clone_batch", "job_status", "job_cancel"):
            self.assertIn(n, names)

    def test_clone_batch_advertises_dry_run_and_async(self):
        tool = next(t for t in M._TOOLS if t["name"] == "clone_batch")
        props = tool["inputSchema"]["properties"]
        self.assertIn("dry_run", props)
        self.assertIn("async", props)
        self.assertIn("dry_run", tool["description"])

    def test_mutate_flag_marks_write_tools(self):
        for name in ("clone_batch", "job_cancel"):
            tool = next(t for t in M._TOOLS if t["name"] == name)
            self.assertTrue(tool.get("mutate"),
                            f"{name} 是写操作，必须带 mutate 标记（否则只读模式会暴露它）")


class TestHandlers(unittest.TestCase):
    def setUp(self):
        J._JOBS.clear()

    def test_dry_run_does_not_start_subprocess(self):
        resp = M._handle_call(1, {"name": "clone_batch", "arguments": {
            "urls": ["https://github.com/owner/alpha"], "dry_run": True}},
            DATA, allow_export=False, allow_mutate=True)
        self.assertIn("result", resp)
        self.assertTrue(resp["result"]["dry_run"])
        self.assertIn("counts", resp["result"]["plan"])
        self.assertEqual(J._JOBS, {}, "dry_run 不应创建作业")

    def test_dry_run_allowed_without_mutate(self):
        """预览不写盘 —— 但仍需 --allow-mutate 才暴露 clone_batch 本身（现状保持）。"""
        resp = M._handle_call(1, {"name": "clone_batch", "arguments": {
            "urls": ["https://github.com/owner/alpha"], "dry_run": True}},
            DATA, allow_export=False, allow_mutate=False)
        self.assertIn("error", resp)

    def test_job_status_unknown_id(self):
        resp = M._handle_call(2, {"name": "job_status",
                                  "arguments": {"job_id": "nope"}},
                              DATA, allow_export=False, allow_mutate=False)
        self.assertEqual(resp["error"]["code"], -32602)

    def test_job_cancel_requires_mutate(self):
        resp = M._handle_call(3, {"name": "job_cancel",
                                  "arguments": {"job_id": "nope"}},
                              DATA, allow_export=False, allow_mutate=False)
        self.assertEqual(resp["error"]["code"], -32000)

    def test_job_cancel_unknown_id_with_mutate(self):
        resp = M._handle_call(4, {"name": "job_cancel",
                                  "arguments": {"job_id": "nope"}},
                              DATA, allow_export=False, allow_mutate=True)
        self.assertEqual(resp["error"]["code"], -32602)

    def test_cancel_running_job_marks_cancelling(self):
        J._JOBS["abc123"] = {"status": "running", "finished": False,
                             "cancelled": False, "result": None, "proc": None,
                             "token": None}
        out = J.cancel_job("abc123")
        self.assertEqual(out["status"], "cancelling")
        self.assertFalse(out["finished"])
        self.assertEqual(J.job_snapshot("abc123")["status"], "cancelling")

    def test_cancel_finished_job_does_not_rewrite_terminal_state(self):
        """R2（审查发现）：已完成的作业不能被改成 `cancelling` —— 那是状态说谎。"""
        J._JOBS["done1"] = {"status": "done", "finished": True, "cancelled": False,
                            "result": {"clone_result": {"ok": True}}, "proc": None,
                            "token": None}
        out = J.cancel_job("done1")
        self.assertEqual(out["status"], "done")
        self.assertTrue(out["finished"])
        snap = J.job_snapshot("done1")
        self.assertEqual(snap["status"], "done", "终态被取消动作改写了")
        self.assertIsNotNone(snap["result"], "终态结果被抹掉了")

    def test_start_job_rejected_when_all_running(self):
        """R11：全都在跑时必须明确拒绝，而不是让作业表无界增长。"""
        for i in range(J.MAX_JOBS):
            J._JOBS[f"busy{i}"] = {"status": "running", "finished": False,
                                   "cancelled": False, "result": None, "proc": None,
                                   "token": None}
        with self.assertRaises(ValueError):
            J.start_job(DATA, "https://example.com/x\n", None)

    def test_job_registry_is_bounded(self):
        """作业表必须封顶 —— 但**不能靠真起一个克隆来测**。

        踩过的坑：早期这例调 `start_job(...)`，会拉起一个真去克隆 example.com 的
        子进程；在 CI 里它可能长时间持有管道句柄，把测试运行器拖住（隐藏挂起源）。
        所以改为直接测治理函数。
        """
        for i in range(J.MAX_JOBS + 30):
            J._JOBS[f"j{i}"] = {"status": "done", "finished": True, "cancelled": False,
                                "result": None, "proc": None, "token": None}
        J.prune_jobs_locked()
        self.assertLessEqual(len(J._JOBS), J.MAX_JOBS,
                             "作业表没有上限（长跑进程会内存泄漏）")

    def test_running_jobs_are_never_pruned(self):
        """全都在跑时不能丢任何一个（丢了用户既拿不到结果也取消不了）。"""
        J._JOBS.clear()
        for i in range(J.MAX_JOBS + 5):
            J._JOBS[f"r{i}"] = {"status": "running", "finished": False,
                                "cancelled": False, "result": None, "proc": None,
                                "token": None}
        J.prune_jobs_locked()
        self.assertEqual(len(J._JOBS), J.MAX_JOBS + 5)


class TestAsyncJobDoesNotDeadlock(unittest.TestCase):
    """C1（审查发现，实测过的真死锁）回归守卫。

    原实现先读 stderr 到 EOF、最后才读 stdout；CLI 的最终 JSON 走 stdout，
    Windows 管道缓冲仅 4096 字节 —— 20 个仓库以上子进程阻塞在写 stdout，
    父线程永久卡在 stderr 循环。这里用"大量无效地址"制造 >4KB 的 stdout
    （不需要网络），断言作业能在限期内结束。
    """

    def setUp(self):
        J._JOBS.clear()
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.data = pathlib.Path(self._td.name) / "data"
        self.data.mkdir()

    def test_large_stdout_job_finishes(self):
        """C1 回归：子进程**先写满 stderr、再写 20KB stdout** 时必须能跑完。

        这个顺序正是原实现的死穴（先读 stderr 到 EOF、最后才读 stdout）：
        用真命令而不是真克隆，复现成本低且不触网。
        """
        # 假 CLI 写成**临时文件**而不是 `-c`：`-c` 传的是已解码字符串，
        # 里面的转义换行会被子进程当成真换行 → SyntaxError（踩过）。
        fake = self.data / "fake_cli.py"
        fake.write_text(
            "import json, sys\n"
            "sys.stderr.write('progress line\\n' * 200)\n"
            "sys.stderr.flush()\n"
            "sys.stdout.write(json.dumps({'pad': 'x' * 20000, 'counts': {'total': 0}}))\n"
            "sys.stdout.flush()\n",
            encoding="utf-8")
        started = J.start_job(self.data, "urls\n", None,
                               cmd=[sys.executable, str(fake)])
        job_id = started["job_id"]
        deadline = time.time() + 60
        snap = None
        while time.time() < deadline:
            snap = J.job_snapshot(job_id)
            if snap and snap["finished"]:
                break
            time.sleep(0.1)
        self.assertIsNotNone(snap)
        self.assertTrue(snap["finished"],
                        "作业没在限期内结束 —— stdout 管道死锁回归了（C1）")
        result = snap["result"] or {}
        payload = result.get("clone_result") or {}
        self.assertEqual(len(payload.get("pad") or ""), 20000,
                         "stdout 没被完整读回（说明仍在读之前就结束了）")

    def test_stdio_writes_all_locked(self):
        """C3：主循环的每一处写出都必须走 `_write_message`（唯一带锁入口）。"""
        src = (ROOT / "gcm" / "mcp_server.py").read_text(encoding="utf-8")
        self.assertNotIn("out.write(string", src)
        self.assertEqual(src.count("out.write(json.dumps"), 0,
                         "主循环仍有绕过 _WRITE_LOCK 的裸写")
        self.assertGreaterEqual(src.count("_write_message("), 6)


class TestNoProgressWithoutToken(unittest.TestCase):
    def test_callback_only_fires_with_token(self):
        """没有 progressToken 时不能发进度通知（否则污染客户端读流）。

        逻辑分两层，必须**两层都在**：
        - `mcp_server` 的 `_on_progress` 先判 token；
        - `mcp_jobs` 只做回调、自己不碰 stdout。
        """
        server_src = (ROOT / "gcm" / "mcp_server.py").read_text(encoding="utf-8")
        self.assertIn("def _on_progress(", server_src)
        self.assertIn("if token is not None", server_src,
                      "进度回调没判 token —— 无 token 的客户端也会收到通知")
        jobs_src = (ROOT / "gcm" / "mcp_jobs.py").read_text(encoding="utf-8")
        self.assertNotIn("notifications/progress", jobs_src,
                         "作业层不该直接写 MCP 帧（协议归 mcp_server）")


if __name__ == "__main__":
    unittest.main()
