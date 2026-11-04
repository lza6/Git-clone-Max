"""G84-20/21/22：取消与收尾的三条硬约束。

1. **G84-20** 子模块更新必须吃单仓时间预算（否则一个卡住的子模块能拖垮整批）；
2. **G84-21** 取消要终止**整棵进程树**（POSIX 侧只杀父进程会留 `ssh`/凭据助手孤儿）；
   同时**绝不能对自己的进程组开火**（同组 killpg 会把本进程一起杀掉）；
3. **G84-22** 关窗收尾必须先给可见反馈、再阻塞，且总预算要有上限
   （原来 drain 4s + waitForDone 8s = 12s 冻结，用户以为没响应就去强杀）。
"""
from __future__ import annotations

import ast
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SERVICE = ROOT / "gcm" / "git" / "service.py"
MCP = ROOT / "gcm" / "mcp_server.py"
MW = ROOT / "gcm" / "ui" / "main_window.py"
ENGINE = ROOT / "gcm" / "app" / "engine.py"


def _src(p: pathlib.Path) -> str:
    return p.read_text(encoding="utf-8")


class TestSubmoduleBudget(unittest.TestCase):
    def test_submodule_update_passes_budget(self):
        """子模块那条 `run_git_ui([... 'submodule', 'update' ...])` 必须带 budget。

        用 AST 而不是 grep：`grep submodule` 会命中 `bool(self._submodule)`、
        `cmd.append('--recurse-submodules')` 这些无关位置（第一版就这么误报过）。
        精确定位到「第一个实参是含 'submodule' 的列表字面量、且被调函数是 run_git_ui」。
        """
        tree = ast.parse(_src(SERVICE))
        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not ast.unparse(node.func).endswith("run_git_ui"):
                continue
            first = node.args[0]
            if not isinstance(first, ast.List):
                continue
            literals = [ast.unparse(e) for e in first.elts]
            if not any("submodule" in s for s in literals):
                continue
            found.append(node)
        self.assertTrue(found, "没找到 submodule update 的 run_git_ui 调用（代码结构变了？）")
        for call in found:
            kws = {k.arg for k in call.keywords}
            self.assertIn("budget", kws,
                          "子模块更新没有传 budget —— 卡住的子模块会绕过单仓时间预算")
            self.assertIn("retries", kws)


class TestKillTree(unittest.TestCase):
    def test_posix_child_is_session_leader(self):
        """POSIX 上子进程必须自成会话，否则 killpg 打不到它那一组。"""
        text = _src(SERVICE)
        self.assertIn("start_new_session=", text,
                      "Popen 没有 start_new_session：POSIX 下取消只杀得掉父进程")

    def test_git_kill_tree_uses_process_group(self):
        text = _src(SERVICE)
        self.assertIn("os.killpg", text)
        self.assertIn("pgid != os.getpgid(0)", text,
                      "缺少「不杀自己所在进程组」的保护")

    def test_mcp_kill_tree_guards_self_group(self):
        """MCP 侧同样必须防自杀 —— 它的子进程若没自成会话，killpg 会连服务自己一起杀。

        G84-24 起作业机制拆到 `gcm/mcp_jobs.py`（mcp_server 越 800 行红线），
        所以这里查新模块；同时确认 mcp_server 仍从它导入（没退回本地复制）。
        """
        jobs = _src(ROOT / "gcm" / "mcp_jobs.py")
        self.assertIn("os.getpgid(0)", jobs)
        self.assertIn("start_new_session=", jobs)
        self.assertIn("SIGKILL", jobs, "缺少 SIGKILL 兜底：忽略 TERM 的孙进程杀不掉")
        server = _src(MCP)
        self.assertIn("from .mcp_jobs import", server,
                      "mcp_server 不再复用 mcp_jobs（会变成两份实现）")

    def test_kill_tree_is_not_a_noop_for_live_process(self):
        """真实调用一次：把自己起的 `sleep` 杀掉，函数必须返回且进程真的终止。"""
        import subprocess
        import time

        from gcm.git.service import _kill_tree

        cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
        kwargs = {}
        if sys.platform != "win32":
            kwargs["start_new_session"] = True
        proc = subprocess.Popen(cmd, **kwargs)
        try:
            time.sleep(0.2)
            _kill_tree(proc)
            proc.wait(timeout=10)
            self.assertIsNotNone(proc.poll())
        finally:
            if proc.poll() is None:      # pragma: no cover - 兜底
                proc.kill()


class TestClosingBudget(unittest.TestCase):
    def test_engine_shutdown_accepts_budget(self):
        import inspect

        from gcm.app.engine import SyncEngine
        sig = inspect.signature(SyncEngine.shutdown)
        self.assertIn("drain_ms", sig.parameters)
        self.assertIn("wait_ms", sig.parameters)
        # 默认值仍是保守的（供非 UI 场景直接调用）
        self.assertGreaterEqual(sig.parameters["wait_ms"].default, 2000)

    def test_close_event_uses_tightened_budget_and_feedback(self):
        text = _src(MW)
        self.assertIn("_closing_feedback()", text)
        self.assertIn("shutdown(drain_ms=", text)
        # 预算合计必须明显小于原来的 12s
        import re
        m = re.search(r"shutdown\(drain_ms=(\d+),\s*wait_ms=(\d+)\)", text)
        self.assertIsNotNone(m, "关窗没有传收紧后的预算")
        total = int(m.group(1)) + int(m.group(2))
        # 上限 6s：原来写死 12s（drain 4s + waitForDone 8s）。不敢压到 2~3s ——
        # 取消后的 worker 不收敛就析构 QThreadPool 会 0xC0000409（G69-21 实测），
        # 而反馈已经把窗口藏起来，用户不会再把它读成"卡死"。
        self.assertLessEqual(total, 6000, f"关窗阻塞预算仍有 {total}ms（应 ≤6s）")
        self.assertGreaterEqual(total, 2000, "预算太低会把 0xC0000409 风险引回来")

    def test_closing_feedback_hides_and_disables(self):
        """真跑一次 `_closing_feedback`：窗口要变灰、状态栏要有话、最后隐藏。"""
        import os
        import tempfile
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        from gcm.db.repo_db import Database
        from gcm.db.settings import SettingsStore
        from gcm.ui.main_window import MainWindow

        with tempfile.TemporaryDirectory() as td:
            d = pathlib.Path(td)
            db = Database(d / "repos.db")
            st = SettingsStore(d / "settings.json")
            w = MainWindow(data_dir=d, db=db, settings=st)
            w.show()
            app.processEvents()
            self.assertTrue(w.isVisible())
            w._closing_feedback()
            app.processEvents()
            self.assertFalse(w.isEnabled(), "关窗反馈没有置灰窗口")
            self.assertFalse(w.isVisible(), "关窗反馈没有隐藏窗口")
            self.assertIn("停止", w.statusBar().currentMessage())
            w.close()


if __name__ == "__main__":
    unittest.main()
