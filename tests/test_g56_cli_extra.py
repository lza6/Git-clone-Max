"""G56-3 CLI 补充分支测试：--diag（json/文本）/ --serve / 读取失败 / 无有效输入。"""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from gcm.cli import main as cli_main


class TestCliDiag(unittest.TestCase):
    def _run_diag(self, *args):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli_main(list(args))
        return rc, buf.getvalue()

    def test_diag_text(self):
        with mock.patch("gcm.app.diag.run_diagnostics") as rd, \
             mock.patch("gcm.app.diag.grade") as gr:
            rd.return_value = [{"status": "ok", "name": "git", "detail": "x"}]
            gr.return_value = "绿（1/1 通过）"
            rc, out = self._run_diag("--cli", "--diag")
            self.assertEqual(rc, 0)
            self.assertIn("总评", out)

    def test_diag_json(self):
        with mock.patch("gcm.app.diag.run_diagnostics") as rd, \
             mock.patch("gcm.app.diag.grade") as gr:
            rd.return_value = [{"status": "fail", "name": "net", "detail": "down"}]
            gr.return_value = "红（0/1 通过）"
            rc, out = self._run_diag("--cli", "--diag", "--json")
            self.assertEqual(rc, 1)
            doc = json.loads(out)
            self.assertIn("diag", doc)
            self.assertFalse(doc["ok"])

    def test_diag_exception(self):
        with mock.patch("gcm.app.diag.run_diagnostics", side_effect=RuntimeError("boom")):
            rc, _ = self._run_diag("--cli", "--diag")
            self.assertEqual(rc, 1)


class TestCliServe(unittest.TestCase):
    def test_serve_dispatches(self):
        with mock.patch("gcm.http_api.serve") as sv:
            sv.return_value = 0
            rc = cli_main(["--cli", "--serve", "127.0.0.1:9999"])
            sv.assert_called_once()
            self.assertEqual(rc, 0)


class TestCliErrors(unittest.TestCase):
    def test_read_failure_json(self):
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "nope.txt"
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["--cli", "--json", str(missing)])
            self.assertEqual(rc, 2)
            doc = json.loads(buf.getvalue())
            self.assertFalse(doc["ok"])

    def test_no_valid_input(self):
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "bad.txt"
            f.write_text("not a url\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["--cli", str(f)])
            self.assertEqual(rc, 2)

    def test_invalid_and_valid_mixed_json(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "src"
            src.mkdir()
            (src / "f.txt").write_text("v1", encoding="utf-8")
            for cmd in (["git", "init", "-q"], ["git", "config", "user.email", "t@e.com"],
                        ["git", "config", "user.name", "t"], ["git", "add", "."],
                        ["git", "commit", "-qm", "init"]):
                subprocess.run(cmd, cwd=src, check=True, capture_output=True)
            bare = td / "r0.git"
            subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)],
                           check=True, capture_output=True)
            f = td / "mix.txt"
            f.write_text(str(bare) + "\n\n", encoding="utf-8")
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli_main(["--cli", "--json", "--dir", str(td / "out"), str(f)])
            self.assertEqual(rc, 0)
            doc = json.loads(buf.getvalue())
            self.assertEqual(doc["counts"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
