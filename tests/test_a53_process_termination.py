"""A5-3/R1 守卫：轮询取消（C-1）+ _kill_tree 返回码兜底（C-11）+ 时间预算（C-10）。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
import unittest
import unittest.mock
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.git.service import _kill_tree, run_git_ui


class TestPollingCancel(unittest.TestCase):
    def test_cancel_during_silent_process_takes_effect_fast(self):
        """C-1：git 静默（无输出行）时点取消必须在 ~1s 内终止。

        旧实现 proc.wait(timeout+5) 阻塞 + 只有读线程有输出行时才检查取消
        → 静默进程的取消请求被完全吞掉。轮询版每 100ms 查一次 cancelled()。
        """
        lines: list[str] = []
        start = time.time()

        def cancel_after_grace():
            # 启动 0.3s 后请求取消（子进程一直静默 sleep 30s）
            return time.time() - start > 0.3

        rc, _ = run_git_ui(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=tempfile.gettempdir(),
            on_line=lambda c: lines.append(c.text),
            cancelled=cancel_after_grace,
            timeout=25,  # 明确不是超时路径
        )
        elapsed = time.time() - start
        self.assertNotEqual(rc, 0, "取消后退出码应为非 0")
        self.assertLess(elapsed, 5.0,
                        f"取消应在秒级生效，实际耗时 {elapsed:.1f}s "
                        f"（若接近 25s 说明取消又被吞了）")


class TestKillTreeFallback(unittest.TestCase):
    def test_taskkill_failure_falls_back_to_kill(self):
        """C-11：taskkill 返回码非 0（杀软拦截/权限不足）时必须 proc.kill() 兜底。"""
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            with unittest.mock.patch(
                    "gcm.git.service.subprocess.run") as m_run:
                m_run.return_value = unittest.mock.Mock(returncode=1)  # 模拟被拦截
                _kill_tree(proc)
            deadline = time.time() + 5
            while proc.poll() is None and time.time() < deadline:
                time.sleep(0.05)
            self.assertIsNotNone(proc.poll(),
                                 "taskkill 失败后 kill() 兜底必须真实终止进程")
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)


class TestTimeBudget(unittest.TestCase):
    def test_budget_blocks_retry_loop(self):
        """C-10：预算耗尽时不再重试，如实报告已用尽时间预算。

        场景：timeout=0.2s 的命令 + retries=3 → 旧实现 4 次全试
        （0.2×4 + 退避），预算 0.5s 应在第 2 次循环前停止。
        """
        lines: list[str] = []
        start = time.time()
        rc, _ = run_git_ui(
            [sys.executable, "-c", "import time; time.sleep(5)"],
            cwd=tempfile.gettempdir(),
            on_line=lambda c: lines.append(c.text),
            timeout=0.2, retries=3, backoff=(0.05, 0.05, 0.05),
            budget=0.7,
        )
        elapsed = time.time() - start
        self.assertNotEqual(rc, 0)
        self.assertLess(elapsed, 3.0,
                        f"预算 0.7s 应在秒级收口，实际 {elapsed:.1f}s")
        self.assertTrue(any("预算" in t for t in lines),
                        "必须如实输出「已用尽时间预算」提示")

    def test_no_budget_keeps_old_behavior(self):
        """不传 budget 保持旧行为（重试循环不受影响）。"""
        work = Path(tempfile.mkdtemp())
        script = "import sys; sys.exit(1)\n"
        rc, _ = run_git_ui(
            [sys.executable, "-c", script], cwd=str(work),
            on_line=lambda c: None, retries=2, backoff=(0.01, 0.01),
        )
        self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
