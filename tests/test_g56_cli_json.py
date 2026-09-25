# -*- coding: utf-8 -*-
"""G56-3 CLI --json 真实 E2E：本地裸仓 x2 → schema 断言 + 退出码 + 失败/无效输入路径。

硬性约束：不写网络真实请求（file:// 本地裸仓）；stdout 只输出 JSON（--json 时
文本进度走 stderr）；退出码 0=全成功 / 1=有失败 / 2=无有效输入。
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_remote(tmp: Path, name: str) -> Path:
    """建一个带 main 分支与一次提交的本地裸仓（file:// 无网络）。"""
    remote = tmp / name
    work = tmp / f"work_{name}"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    work.mkdir()
    _git(work, "init")
    _git(work, "config", "user.email", "t@t.com")
    _git(work, "config", "user.name", "t")
    (work / "a.txt").write_text("hello " + name, encoding="utf-8")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "branch", "-M", "main")
    _git(work, "remote", "add", "origin", str(remote))
    _git(work, "push", "-u", "origin", "main")
    subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"],
                   cwd=remote, check=True, capture_output=True)
    return remote


class TestCliJsonE2E(unittest.TestCase):
    def test_json_schema_all_success(self):
        """两仓全部成功：顶层 schema、counts、results 字段、stdout 仅 JSON。"""
        tmp = Path(tempfile.mkdtemp(prefix="gcm_g56_json_"))
        remotes = [_init_remote(tmp, f"r{i}.git") for i in range(2)]
        urls = tmp / "urls.txt"
        urls.write_text("\n".join(str(r) for r in remotes) + "\n", encoding="utf-8")
        out_dir = tmp / "out"
        stdout, stderr = io.StringIO(), io.StringIO()
        from gcm.cli import main as cli_main
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli_main(["--cli", "--json", "--dir", str(out_dir), str(urls)])
        self.assertEqual(rc, 0, "全部成功应退出 0")
        # stdout 只有一行 JSON（可解析）
        doc = json.loads(stdout.getvalue())
        self.assertEqual(sorted(doc.keys()),
                         ["counts", "invalid", "ok", "results", "version"])
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["version"], "8.0.2")
        self.assertEqual(doc["counts"], {"success": 2, "failed": 0, "other": 0, "total": 2})
        self.assertEqual(len(doc["results"]), 2)
        self.assertEqual(doc["invalid"], [])
        for r in doc["results"]:
            self.assertEqual(sorted(r.keys()),
                             ["action", "code", "message", "path", "status", "url"])
            self.assertEqual(r["status"], "success")
            self.assertEqual(r["code"], 0)
            self.assertIn(r["action"], {"cloned", "updated", "fetched", "empty"})
            self.assertTrue(Path(r["path"]).is_dir(), f"path 不存在：{r['path']}")
            self.assertEqual(Path(r["path"]).parent.resolve(), out_dir.resolve(),
                             "path 应位于 --dir 输出根目录下")
        # 文本进度移到 stderr；stdout 不能混入进度
        self.assertIn("开始并行克隆", stderr.getvalue())
        self.assertNotIn("[CLI]", stdout.getvalue())

    def test_json_failure_returns_1(self):
        """目标目录被占 → FAILED，退出码 1，status=failed / code=1。"""
        tmp = Path(tempfile.mkdtemp(prefix="gcm_g56_fail_"))
        remote = _init_remote(tmp, "r0.git")
        urls = tmp / "urls.txt"
        urls.write_text(str(remote) + "\n", encoding="utf-8")
        out_dir = tmp / "out"
        out_dir.mkdir()
        (out_dir / "r0").write_text("blocker", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        from gcm.cli import main as cli_main
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli_main(["--cli", "--json", "--dir", str(out_dir), str(urls)])
        self.assertEqual(rc, 1, "有失败应退出 1")
        doc = json.loads(stdout.getvalue())
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["counts"]["failed"], 1)
        self.assertEqual(doc["counts"]["total"], 1)
        r = doc["results"][0]
        self.assertEqual(r["status"], "failed")
        self.assertEqual(r["code"], 1)
        self.assertTrue(r["message"])

    def test_json_no_valid_input_returns_2(self):
        """仅注释/空文件 → 退出码 2；顶层 schema 仍完整。"""
        tmp = Path(tempfile.mkdtemp(prefix="gcm_g56_empty_"))
        urls = tmp / "empty.txt"
        urls.write_text("# 注释\n\n", encoding="utf-8")
        stdout, stderr = io.StringIO(), io.StringIO()
        from gcm.cli import main as cli_main
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli_main(["--cli", "--json", "--dir", str(tmp / "out"), str(urls)])
        self.assertEqual(rc, 2, "无有效输入应退出 2")
        doc = json.loads(stdout.getvalue())
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["counts"]["total"], 0)
        self.assertEqual(doc["results"], [])

    def test_json_invalid_lines_reported(self):
        """无效行（本地不存在的路径）进 invalid 数组，schema 为 {code,message,redacted}。"""
        tmp = Path(tempfile.mkdtemp(prefix="gcm_g56_inv_"))
        remote = _init_remote(tmp, "r0.git")
        urls = tmp / "urls.txt"
        urls.write_text(f"{remote}\n{tmp / 'nope_does_not_exist'}\n", encoding="utf-8")
        out_dir = tmp / "out"
        stdout, stderr = io.StringIO(), io.StringIO()
        from gcm.cli import main as cli_main
        with redirect_stdout(stdout), redirect_stderr(stderr):
            rc = cli_main(["--cli", "--json", "--dir", str(out_dir), str(urls)])
        self.assertEqual(rc, 0, "有效仓成功，无效行不改变退出码 0")
        doc = json.loads(stdout.getvalue())
        self.assertEqual(len(doc["invalid"]), 1)
        inv = doc["invalid"][0]
        self.assertEqual(sorted(inv.keys()), ["code", "message", "redacted"])
        self.assertEqual(inv["code"], 2)
        self.assertTrue(inv["message"])
        self.assertIn("nope_does_not_exist", inv["redacted"])

    def test_json_redacts_url_credentials(self):
        """URL 内嵌凭据在输出中被打码。"""
        from gcm.cli import _error, _result_dict
        err = _error(2, "https://ghp_secret@github.com/o/r.git 无法解析")
        self.assertNotIn("ghp_secret", err["redacted"])
        self.assertIn("***@", err["redacted"])
        from gcm.models import RepoSpec, SyncAction, SyncResult, SyncStatus
        spec = RepoSpec(owner="o", repo="r", url_https="https://ghp_secret@github.com/o/r.git",
                        folder_name="o__r")
        res = SyncResult(spec=spec, status=SyncStatus.SUCCESS, action=SyncAction.CLONED,
                         message="克隆完成", path=str(tmp if "tmp" in dir() else Path.cwd()))
        d = _result_dict(res)
        self.assertNotIn("ghp_secret", d["url"])
        self.assertIn("***@", d["url"])


if __name__ == "__main__":
    unittest.main()
