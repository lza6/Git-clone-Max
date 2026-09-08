# -*- coding: utf-8 -*-
"""应用启动时间优化：从系统 git 凭证管理器读取 GitHub token 以备私有仓库克隆。"""
from __future__ import annotations

import os
import subprocess


def git_token() -> str:
    """读取 GitHub 凭证 token（git credential fill）。失败返回空串。"""
    try:
        r = subprocess.run(
            ["git", "credential", "fill"],
            input="protocol=https\nhost=github.com\n\n",
            capture_output=True, text=True, timeout=10,
        )
        for line in (r.stdout or "").splitlines():
            if line.startswith("password="):
                return line[len("password="):].strip()
    except Exception:
        pass
    return ""


def token_env(extra=None) -> dict:
    """把 token 注入 git 环境（GIT_ASKPASS 无需，走 credential helper 即可）。"""
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env
