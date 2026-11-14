# -*- coding: utf-8 -*-
"""G85 B3 守卫：--diag 单独使用必须告警（known-issues #13 收口）。

`python -m gcm --diag`（不带 --cli）会静默走 GUI 单实例路径，打印
「已在运行」exit 0 —— 用户以为跑了诊断，实际什么都没发生。修法：

1. `gcm/__main__.py` GUI 路径检测到 `--diag` 且无 `--cli` → 打 WARN 日志
   （applog + stderr 各一条），说明 --diag 需配 --cli 才有诊断输出。
2. `cli.py` 的 help 早已写明（G49-3 起就有，见 :368-369）—— 守卫钉住
   该文案不被漂移删除。
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class TestDiagAloneWarns(unittest.TestCase):
    """单独 --diag（GUI 路径）必须产生可看见的告警。"""

    def test_gui_path_diag_alone_logs_warning(self):
        from gcm.__main__ import _parse_flags, _warn_diag_without_cli

        flags = _parse_flags(["--diag"])
        self.assertTrue(flags["diag"])
        self.assertFalse(flags["cli"])

        warnings: list[str] = []
        buf = io.StringIO()
        with mock.patch("gcm.app.applog.warn", side_effect=lambda m, *a, **k: warnings.append(m)), \
             redirect_stderr(buf):
            _warn_diag_without_cli(flags)
        self.assertEqual(len(warnings), 1, "applog 必须收到一条 WARN")
        self.assertIn("--cli", warnings[0])
        self.assertIn("--diag", warnings[0])
        # stderr 同步有一条（无日志系统时也可见）
        err_text = buf.getvalue()
        self.assertIn("--diag", err_text)

    def test_cli_path_does_not_warn(self):
        """--cli --diag 是合法组合，不应告警。"""
        from gcm.__main__ import _parse_flags, _warn_diag_without_cli

        flags = _parse_flags(["--cli", "--diag"])
        buf = io.StringIO()
        with mock.patch("gcm.app.applog.warn") as m_warn, redirect_stderr(buf):
            _warn_diag_without_cli(flags)
        m_warn.assert_not_called()
        self.assertEqual(buf.getvalue(), "")

    def test_help_text_mentions_cli_requirement(self):
        """cli.py help 必须写明 --diag 需配 --cli（防文案漂移）。"""
        from gcm.cli import _print_help

        buf = io.StringIO()
        with redirect_stdout_compat(buf):
            _print_help()
        text = buf.getvalue()
        self.assertIn("--diag", text)
        self.assertIn("--cli", text)

    def test_main_gui_branch_invokes_diag_warn(self):
        """main() 的 GUI 分支真实调用 _warn_diag_without_cli（接线守卫）。"""
        import inspect
        import gcm.__main__ as gm

        src = inspect.getsource(gm.main)
        self.assertIn("_warn_diag_without_cli", src,
                      "main() GUI 分支没有接线 _warn_diag_without_cli —— B3-1 虚伪实现")


def redirect_stdout_compat(buf):
    from contextlib import redirect_stdout
    return redirect_stdout(buf)


if __name__ == "__main__":
    unittest.main()
