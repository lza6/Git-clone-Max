# -*- coding: utf-8 -*-
"""G49-3 CLI 批量克隆真实 E2E：本地裸仓 x3 → python -m gcm --cli 全部克隆成功、退出码 0。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_remote(tmp: Path, name: str) -> Path:
    """建一个带 main 分支与一次提交的本地裸仓。"""
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


class TestCliE2E(unittest.TestCase):
    def test_batch_clone_three_repos(self):
        tmp = Path(tempfile.mkdtemp(prefix="gcm_cli_"))
        remotes = [_init_remote(tmp, f"r{i}.git") for i in range(3)]
        urls_file = tmp / "urls.txt"
        urls_file.write_text("\n".join(str(r) for r in remotes) + "\n",
                            encoding="utf-8")
        old_cwd = os.getcwd()
        os.chdir(tmp)  # CLI 克隆根 = cwd/clones
        try:
            from gcm.cli import main as cli_main
            rc = cli_main(["--cli", str(urls_file)])
        finally:
            os.chdir(old_cwd)
        self.assertEqual(rc, 0, "CLI 应全部成功退出 0")
        clones = tmp / "clones"
        for r in remotes:
            folder = clones / r.stem
            self.assertTrue((folder / ".git").is_dir(), f"未克隆 {r.stem}")
            self.assertTrue((folder / "a.txt").is_file())
        # 空输入（仅注释）应退出码 2
        empty = tmp / "empty.txt"
        empty.write_text("# 注释\n\n", encoding="utf-8")
        rc_empty = None
        try:
            rc_empty = cli_main(["--cli", str(empty)])
        except SystemExit:
            pass
        self.assertEqual(rc_empty, 2, "空输入应退出 2")
