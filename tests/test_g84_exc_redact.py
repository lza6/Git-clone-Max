"""G84-19：`error.log` 必须打码（它曾是唯一没打码的持久化出口）。

为什么这是硬要求：未捕获异常的 traceback 天然带
- URL（可能含 `user:pass@`）、
- 子进程命令（`-c http.extraHeader=Authorization: Bearer …`）、
- 我们自己的 `context=`（可能含 token）。

用户报障时会把 error.log 贴进 issue —— 那一步就等于泄露凭据。
`applog` 侧早有 `RedactingFormatter`，但 excepthook 走的是 `exc_dump`，绕过了它。
"""
from __future__ import annotations

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.diag_bundle import build_diag_bundle  # noqa: E402
from gcm.app.exc_dump import write_error_log  # noqa: E402

# 假 token：仅用于断言打码，不是真凭据（前缀合法 + 长度够长才会被 redact 命中）
TOKEN = "ghp_FAKEFAKEFAKEFAKEFAKE1234"
URL_SECRET = "https://user:sup3rs3cret@github.com/o/r.git"


class TestErrorLogIsRedacted(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = pathlib.Path(self._td.name)
        self.addCleanup(self._td.cleanup)
        self.log = self.dir / "error.log"

    def _write(self, exc, context=None):
        write_error_log(self.log, exc, context)
        return self.log.read_text(encoding="utf-8")

    def test_token_in_exception_message_is_masked(self):
        text = self._write(RuntimeError(f"clone failed with token {TOKEN}"))
        self.assertNotIn(TOKEN, text, "error.log 里出现了明文 token")
        self.assertIn("***", text)

    def test_url_userinfo_in_message_is_masked(self):
        text = self._write(RuntimeError(f"fetch {URL_SECRET} failed"))
        self.assertNotIn("sup3rs3cret", text, "error.log 里出现了 URL 内嵌口令")

    def test_context_is_masked(self):
        text = self._write(RuntimeError("boom"), {"token": TOKEN, "url": URL_SECRET})
        self.assertNotIn(TOKEN, text)
        self.assertNotIn("sup3rs3cret", text)

    def test_traceback_frames_are_masked(self):
        """凭据常常出现在 traceback 的局部变量/源码行里，整段 traceback 也必须打码。"""
        try:
            raise ValueError(f"bad url {URL_SECRET}")
        except ValueError as e:
            text = self._write(e)
        self.assertNotIn("sup3rs3cret", text)
        self.assertIn("Traceback", text, "打码不能把整个 traceback 吃掉")

    def test_env_block_still_present(self):
        text = self._write(RuntimeError("x"))
        self.assertIn("app=", text)
        self.assertIn("python=", text)


class TestErrorLogInDiagBundle(unittest.TestCase):
    def test_bundle_contains_redacted_error_log(self):
        with tempfile.TemporaryDirectory() as td:
            data = pathlib.Path(td) / "data"
            data.mkdir()
            write_error_log(data / "error.log",
                            RuntimeError(f"leak {TOKEN} / {URL_SECRET}"))
            out = pathlib.Path(td) / "diag.zip"
            build_diag_bundle(out, data_dir=data, report_text="ok")
            import zipfile
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
                self.assertTrue(any(n.endswith("error.log") for n in names),
                                f"诊断包里没有 error.log：{names}")
                blob = "".join(zf.read(n).decode("utf-8", errors="replace")
                               for n in names)
        self.assertNotIn(TOKEN, blob, "诊断包内出现明文 token")
        self.assertNotIn("sup3rs3cret", blob)


if __name__ == "__main__":
    unittest.main()
