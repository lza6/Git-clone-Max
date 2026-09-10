# -*- coding: utf-8 -*-
"""G08-1 子模块支持 E2E：真实 git 子模块仓库克隆（settings.submodule 开关）。"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.git.service import GitService
from gcm.models import RepoSpec, SyncAction, SyncStatus


def _git(cwd, *args, check=True):
    r = subprocess.run(["git", "-C", str(cwd), *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r


class TestSubmoduleClone(unittest.TestCase):
    """真实 git E2E：构造含子模块的裸仓库远端 → submodule=True 克隆后子模块目录完整。"""

    def test_clone_with_submodules(self):
        base = Path(tempfile.mkdtemp())
        # file 协议放行环境（新 git 默认禁止 file 子模块）
        env = dict(os.environ)
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = "protocol.file.allow"
        env["GIT_CONFIG_VALUE_0"] = "always"
        # 1) 子仓库 sub.git（main 分支 + HEAD 指向）
        sub_bare = base / "sub.git"
        sub_work = base / "sub_work"
        subprocess.run(["git", "init", "--bare", "-q", str(sub_bare)], check=True)
        subprocess.run(["git", "-C", str(sub_bare), "symbolic-ref", "HEAD",
                        "refs/heads/main"], check=True, capture_output=True)
        sub_work.mkdir()
        _git(sub_work, "init", "-q")
        _git(sub_work, "config", "user.email", "t@t.com")
        _git(sub_work, "config", "user.name", "t")
        (sub_work / "s.txt").write_text("sub", encoding="utf-8")
        _git(sub_work, "add", ".")
        _git(sub_work, "commit", "-m", "sub v1")
        _git(sub_work, "branch", "-M", "main")
        _git(sub_work, "push", "-q", str(sub_bare), "HEAD:main")
        # 2) 主仓库 main.git 引用 sub
        main_bare = base / "main.git"
        main_work = base / "main_work"
        subprocess.run(["git", "init", "--bare", "-q", str(main_bare)], check=True)
        subprocess.run(["git", "-C", str(main_bare), "symbolic-ref", "HEAD",
                        "refs/heads/main"], check=True, capture_output=True)
        main_work.mkdir()
        _git(main_work, "init", "-q")
        _git(main_work, "config", "user.email", "t@t.com")
        _git(main_work, "config", "user.name", "t")
        (main_work / "m.txt").write_text("main", encoding="utf-8")
        _git(main_work, "add", ".")
        _git(main_work, "commit", "-m", "main v1")
        subprocess.run(["git", "-C", str(main_work), "submodule", "add",
                        str(sub_bare), "libs/sub"], check=True, capture_output=True, env=env)
        _git(main_work, "commit", "-m", "add submodule")
        _git(main_work, "push", "-q", str(main_bare), "HEAD:main")

        # 3) submodule=False：主仓库克隆成功但子模块目录为空
        root1 = base / "root_off"
        svc_off = GitService(root1)
        spec = RepoSpec("o", "main", str(main_bare), folder_name="o__main")
        r_off = svc_off.sync(spec)
        self.assertEqual(r_off.status.value, "success")
        sub_dir_off = root1 / "o__main" / "libs" / "sub"
        # 未开启子模块：目录空（无 s.txt）
        self.assertFalse((sub_dir_off / "s.txt").exists(),
                         "submodule=False 时子模块内容不应被拉取")

        # 4) submodule=True：子模块内容完整
        root2 = base / "root_on"
        # 克隆 service 子进程同样需要放行 file 协议（本地裸仓库远端）
        svc_on = GitService(root2, submodule=True)
        svc_on._env_orig = svc_on._env

        def _env_with_file_allow(extra=None):
            e = svc_on._env_orig(extra) or dict(os.environ)
            e["GIT_CONFIG_COUNT"] = "1"
            e["GIT_CONFIG_KEY_0"] = "protocol.file.allow"
            e["GIT_CONFIG_VALUE_0"] = "always"
            return e
        svc_on._env = _env_with_file_allow
        spec2 = RepoSpec("o2", "main", str(main_bare), folder_name="o2__main")
        r_on = svc_on.sync(spec2)
        self.assertEqual(r_on.status.value, "success", r_on.message)
        sub_file = root2 / "o2__main" / "libs" / "sub" / "s.txt"
        self.assertTrue(sub_file.exists(), f"submodule=True 应拉取子模块内容：{sub_file}")
        self.assertEqual(sub_file.read_text(encoding="utf-8"), "sub")


if __name__ == "__main__":
    unittest.main(verbosity=2)
