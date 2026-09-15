"""run_git_ui 取消/超时后读线程退出保障（G33-4）定向测试。

覆盖三个用例：
- 用例 A：取消路径（cancelled 恒 True）→ 返回 rc==-1、读线程正常退出、
  不产生「未及时退出」警告
- 用例 B：正常路径回归（真实 git --version）→ rc==0、无警告
- 用例 C：超时路径 + 读线程卡死（mock 阻塞 stdout）→ 返回非 0、
  产生「未及时退出」警告，判定后读线程仍存活（join 超时被放弃等待）

子进程一律 mock（用例 B 用真实 git --version，秒级完成），不触网、不跑长命令；
毫秒级稳定，全用例 ≤ 10s。
"""
from __future__ import annotations

# 项目约定：QT_QPA_PLATFORM 置为离屏（见 tests/test_local_dialog.py 头部）
import os
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.git import service  # noqa: E402
from gcm.git.service import run_git_ui  # noqa: E402


class _YieldingStdout:
    """模拟 git 子进程 stdout：可迭代出若干行后自然结束。"""

    def __init__(self, lines: list[str]) -> None:
        self._it = iter(lines)

    def __iter__(self):
        return self

    def __next__(self) -> str:
        return next(self._it)

    def close(self) -> None:
        pass


class _BlockingStdout:
    """模拟卡死的 stdout：迭代永久阻塞（管道缓冲满/被杀进程未刷管道）。"""

    def __iter__(self):
        return self

    def __next__(self) -> str:
        time.sleep(3600)
        return "x\n"

    def close(self) -> None:
        pass


def _fake_proc(stdout, returncode: int = 0, wait_raises: bool = False) -> mock.Mock:
    """构造 fake subprocess.Popen 返回值。

    - returncode：wait/poll 正常返回时的退出码
    - wait_raises：wait() 抛超时异常（模拟主线程 proc.wait 超时兜底）
    """
    proc = mock.Mock()
    proc.stdout = stdout
    proc.returncode = returncode
    proc.pid = 12345
    if wait_raises:
        proc.wait.side_effect = TimeoutError
    else:
        proc.wait.return_value = returncode
    proc.poll.return_value = returncode
    return proc


class TestRunGitUiThreadExit(unittest.TestCase):
    def _collect(self):
        """构造 on_line 收集器，返回 (行列表, on_line 回调)。"""
        lines: list[str] = []

        def on_line(chunk) -> None:
            lines.append(chunk.text)

        return lines, on_line

    def _probe(self, seen: list):
        """构造 thread_probe：每次回调记录拿到的读线程引用。"""
        def probe(thread) -> None:
            seen.append(thread)

        return probe

    # ---------------- 用例 A：取消路径 ----------------
    def test_cancel_path_thread_exits_and_no_warning(self):
        lines, on_line = self._collect()
        seen: list = []
        proc = _fake_proc(_YieldingStdout(["hello\n"]), returncode=0)
        with mock.patch.object(service.subprocess, "Popen", return_value=proc), \
                mock.patch.object(service, "_kill_tree", return_value=None) as kill:
            rc, _ = run_git_ui(
                ["git", "status"], cwd=str(ROOT),
                on_line=on_line, cancelled=lambda: True,
                thread_probe=self._probe(seen),
            )
        # 取消统一按非 0 处理
        self.assertEqual(rc, -1)
        # 取消被泵线程捕获（_kill_tree 至少被调用一次）
        kill.assert_called()
        # 读线程应已退出：join 判定后不再存活
        self.assertTrue(seen, "thread_probe 应被回调")
        self.assertFalse(seen[-1].is_alive(), "取消后读线程应已退出")
        # 正常退出路径不应产生「未及时退出」警告
        self.assertFalse(any("未及时退出" in L for L in lines))

    # ---------------- 用例 B：正常路径回归 ----------------
    def test_normal_path_no_warning(self):
        lines, on_line = self._collect()
        seen: list = []
        rc, _ = run_git_ui(
            ["git", "--version"], cwd=str(ROOT),
            on_line=on_line, thread_probe=self._probe(seen),
        )
        self.assertEqual(rc, 0)
        self.assertTrue(any("git version" in L for L in lines))
        self.assertFalse(any("未及时退出" in L for L in lines))
        self.assertTrue(seen and not seen[-1].is_alive(),
                        "正常路径读线程应已退出")

    # ---------------- 用例 C：超时路径 + 读线程卡死 ----------------
    def test_timeout_path_warns_when_reader_stuck(self):
        lines, on_line = self._collect()
        seen: list = []
        proc = _fake_proc(_BlockingStdout(), returncode=None, wait_raises=True)
        with mock.patch.object(service.subprocess, "Popen", return_value=proc), \
                mock.patch.object(service, "_kill_tree", return_value=None):
            rc, _ = run_git_ui(
                ["git", "status"], cwd=str(ROOT),
                on_line=on_line, cancelled=lambda: False,
                timeout=0.001, retries=0, thread_probe=self._probe(seen),
            )
        # 超时应返回非 0 退出码
        self.assertNotEqual(rc, 0)
        # 读线程卡死 → join(5s) 超时 → 产生一次放弃等待警告
        self.assertTrue(any("未及时退出" in L for L in lines),
                        f"应输出读线程未及时退出警告，实际行：{lines}")
        # join 判定后读线程仍存活（卡在阻塞 stdout 上，符合「放弃等待」语义）
        self.assertTrue(seen and seen[-1].is_alive(),
                        "卡死读线程在 join 判定后应仍存活")


if __name__ == "__main__":
    unittest.main()
