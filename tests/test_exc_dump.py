"""G06-3 异常兜底测试：sys.excepthook 捕获写日志、Qt 消息拦截、safe-mode（对应指南 G06-3）。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.exc_dump import install_excepthook, write_error_log


class TestExcDump(unittest.TestCase):
    def test_write_error_log(self):
        d = Path(tempfile.mkdtemp())
        path = d / "error.log"
        try:
            1 / 0
        except Exception as e:
            write_error_log(path, e, context={"extra": "x"})
        self.assertTrue(path.exists())
        text = path.read_text(encoding="utf-8")
        self.assertIn("ZeroDivisionError", text)
        self.assertIn("Traceback", text)

    def test_install_excepthook_captures(self):
        d = Path(tempfile.mkdtemp())
        log_path = d / "error.log"
        install_excepthook(log_path=log_path)
        # 触发一次未捕获异常
        try:
            1 / 0
        except ZeroDivisionError as e:
            sys.excepthook(type(e), e, e.__traceback__)
        self.assertTrue(log_path.exists())
        self.assertIn("ZeroDivisionError", log_path.read_text(encoding="utf-8"))

    def test_install_idempotent(self):
        """重复安装不得重复包装 sys.excepthook（否则异常会被记录多次）。

        `install_excepthook` 用模块级 `_installed["hook"]` 做一次性闸门，
        必须临时复位该闸门，否则本测试只会在「别的测试已装过」时才通过
        （顺序依赖，且第二次调用本就是 no-op，断言不到任何东西）。
        """
        from gcm.app import exc_dump
        d = Path(tempfile.mkdtemp())
        with mock.patch.dict(exc_dump._installed, {"hook": False}), \
             mock.patch.object(sys, "excepthook", sys.__excepthook__):
            install_excepthook(log_path=d / "error.log")
            first = sys.excepthook
            self.assertTrue(exc_dump._installed["hook"], "首次安装应置位闸门")
            # 二次安装：闸门已置位 → 必须原样返回，不得替换 hook
            install_excepthook(log_path=d / "error.log")
            self.assertIs(sys.excepthook, first, "重复安装不得再次包装")


class TestThreadExcepthook(unittest.TestCase):
    """线程内未捕获异常必须落盘（windowed 打包下 stderr 不可见 = 无声消失）。

    用子进程跑真实的 install_excepthook 路径：模块级幂等闸门在同进程里
    无法可靠复位，且子进程能验证「安装 → 线程抛出 → 文件生成」全链路。
    对照组（不装 hook）证明正例不是环境自带的 stderr 转存。
    """

    SCRIPT = (
        "import sys, threading, time\n"
        "sys.path.insert(0, r'{root}')\n"
        "from pathlib import Path\n"
        "if sys.argv[2] == 'install':\n"
        "    from gcm.app.exc_dump import install_excepthook\n"
        "    install_excepthook(Path(sys.argv[1]))\n"
        "t = threading.Thread(target=lambda: 1 / 0, name='boom-worker')\n"
        "t.start(); t.join(); time.sleep(0.1)\n"
    )

    def _run(self, log: Path, mode: str):
        import subprocess
        return subprocess.run(
            [sys.executable, "-c",
             self.SCRIPT.format(root=str(ROOT)), str(log), mode],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )

    def test_thread_exception_lands_in_log(self):
        d = Path(tempfile.mkdtemp())
        log = d / "error.log"
        proc = self._run(log, "install")
        self.assertEqual(proc.returncode, 0, f"子进程失败：{proc.stderr[-400:]}")
        self.assertTrue(log.exists(), "线程未捕获异常未落盘（threading.excepthook 未生效）")
        text = log.read_text(encoding="utf-8")
        self.assertIn("ZeroDivisionError", text)
        self.assertIn("boom-worker", text, "落盘记录必须带线程名")

    def test_without_hook_no_log(self):
        """阴性对照：不装 hook 时默认行为只打 stderr，不产生 error.log。"""
        d = Path(tempfile.mkdtemp())
        log = d / "error.log"
        proc = self._run(log, "nohook")
        self.assertEqual(proc.returncode, 0)
        self.assertFalse(log.exists(), "对照组竟然生成了日志——正例断言不可信")
        self.assertIn("ZeroDivisionError", proc.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
