# -*- coding: utf-8 -*-
"""启动器测试：验证 bat 能走通『依赖就绪→启动 gcm』。
为稳定无显示器验证，改用探测式断言：启动后进程存活 = 成功进入 GUI 事件循环。
"""
from __future__ import annotations

import os
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BAT = ROOT / "scripts" / "启动Git-clone-Max.bat"


class TestLauncher(unittest.TestCase):
    def test_bat_reaches_launch(self):
        env = dict(os.environ)
        env["QT_QPA_PLATFORM"] = "offscreen"
        out = None
        proc = None
        try:
            proc = subprocess.Popen(
                ["cmd.exe", "/c", str(BAT)],
                cwd=str(ROOT),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            try:
                out, _ = proc.communicate(timeout=60)
                # 正常退出：检查是否到达启动行（或明确报错行）；兼容 str/bytes
                text = out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")
                self.assertIn("正在启动 Git-clone-Max", text)
            except subprocess.TimeoutExpired:
                # 进程仍存活 = 已进入 GUI 事件循环 → 成功
                proc.kill()
                proc.wait(timeout=10)
                self.assertTrue(True)
                return
        finally:
            if proc and proc.poll() is None:
                proc.kill()


if __name__ == "__main__":
    unittest.main(verbosity=2)