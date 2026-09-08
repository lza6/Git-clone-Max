# -*- coding: utf-8 -*-
"""本地 git 仓库扫描：发现目标目录下所有已有 git 仓库，纳入统一管理。

解决『用户以前就有 git 仓库，也想用本软件统一更新』的场景：
任意根目录下凡是含 .git 的目录（不管命名、不管远端是哪个平台）都能被发现，
解析出远端 URL / owner / repo / HEAD，供 UI 批量入库后走既有增量更新链路。
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from ..models import RepoSpec

# 无需深入的无意义目录
_SKIP_DIRS = {"node_modules", ".venv", "venv", "dist", "build",
              "__pycache__", ".idea", ".vscode", "site-packages"}

_GIT_TIMEOUT = 5  # git 查询单次超时（秒）


@dataclass
class RepoInfo:
    """扫描发现的本地仓库。"""
    folder_name: str        # 实际目录名（可能不是 owner__repo 规范名）
    display: str            # 展示名：owner/repo（有远端时）或目录名
    path: str               # 仓库根目录绝对路径
    has_git: bool = True
    remote_url: str = ""    # origin 远端 URL（可能为空 = 纯本地仓库）
    owner: str = ""
    repo: str = ""
    url_https: str = ""     # 规范化可 clone 的 https URL（空则无法克隆）
    host: str = ""          # github.com / gitlab.com / 本地路径等
    head_sha: str = ""      # 当前 HEAD（空 = 无提交）
    depth: int = 0          # 距根目录的层数

    @property
    def key(self) -> str:
        """用于去重的键：优先 owner/repo（忽略 host，因本地扫描 host 未必准确），
        无远端/无法解析时用绝对路径。"""
        if self.owner and self.repo:
            return f"{self.owner}/{self.repo}"
        return f"path:{self.path}"


def _run_git(path: Path, *args: str) -> str:
    """在 path 下跑 git 查询命令，失败返回空串。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_GIT_TIMEOUT,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _head_sha(path: Path) -> str:
    return _run_git(path, "rev-parse", "HEAD")


def _remote_url(path: Path) -> str:
    return _run_git(path, "remote", "get-url", "origin")


def remote_is_local(url: str) -> bool:
    """判断远端 URL 是否为本地路径（file:// 或 C:/x、/x/y 等，无法直接 clone）。"""
    url = (url or "").strip()
    if not url:
        return True
    if url.startswith("file://"):
        return True
    if "://" in url:
        return False
    if "\\" in url:                     # Windows 反斜杠路径
        return True
    # 无协议：判断是否本地路径（含盘符 C:/ 或根路径 /x/y）
    if re.match(r"^[A-Za-z]:[/\\]", url):
        return True
    if url.startswith("/"):
        return True
    return False


def parse_remote_url(url: str) -> tuple:
    """把远端 URL 解析成 (owner, repo, url_https, host)。

    支持 https://host/o/r(.git) / git@host:o/r(.git) / ssh://git@host/o/r.git。
    本地路径（file:// / C:/x / /x/y）返回空——纯本地，无远端可解析。
    无法解析时返回 ("", "", "", host 或 "")——调用方用目录名兜底。
    """
    url = (url or "").strip()
    if not url or remote_is_local(url):
        return ("", "", "", "")
    # 从 url_lib 复用 github 解析；其余平台做通用正则
    from ..app.url_lib import parse_repo_url, host_of
    spec = parse_repo_url(url)
    if spec is not None:
        return (spec.owner, spec.repo, spec.url_https, "github.com")
    # 通用：https://host/owner/repo(.git) 或 ssh://git@host/owner/repo
    m = re.match(r"^(?:https?://|ssh://(?:git@)?|git@)([^/:]+)[:/]([^/]+)/([^/]+?)(?:\.git)?/?$",
                 url, re.IGNORECASE)
    if m:
        host = m.group(1).lower().replace("www.", "")
        owner, repo = m.group(2), m.group(3)
        url_https = f"https://{host}/{owner}/{repo}.git"
        return (owner, repo, url_https, host)
    return ("", "", "", host_of(url))


def _name_to_owner_repo(dirname: str) -> tuple:
    """目录名 → (owner, repo)。含 __ 视为 owner__repo 规范名，否则 repo=目录名。"""
    if "__" in dirname:
        owner, _, repo = dirname.partition("__")
        if owner and repo:
            return (owner, repo)
    return ("", dirname)


def _make_info(root: Path, dirpath: Path, depth: int) -> RepoInfo:
    name = dirpath.name
    remote = _remote_url(dirpath)
    owner, repo = _name_to_owner_repo(name)
    owner2, repo2, url_https, host = parse_remote_url(remote)
    # 远端信息优先覆盖目录名推测
    if owner2 and repo2:
        owner, repo = owner2, repo2
    if not url_https and remote and not remote_is_local(remote):
        url_https = remote
    if not host and url_https and not remote_is_local(url_https):
        from ..app.url_lib import host_of
        host = host_of(url_https)
    display = f"{owner}/{repo}" if owner else name
    return RepoInfo(
        folder_name=name, display=display, path=str(dirpath), has_git=True,
        remote_url=remote, owner=owner, repo=repo, url_https=url_https,
        host=host, head_sha=_head_sha(dirpath), depth=depth,
    )


def scan_git_dirs(root: str | Path, max_depth: int = 2) -> List[RepoInfo]:
    """扫描根目录下所有含 .git 的仓库目录（最多下钻 max_depth 层）。

    - 不深入 .git / 依赖目录等内部
    - 返回按路径排序的 RepoInfo 列表
    - 目录读取异常自动跳过，不中断整体扫描
    """
    root = Path(root)
    if not root.is_dir():
        return []
    results: List[RepoInfo] = []
    for dirpath, dirnames, _ in os.walk(root):
        depth = len(Path(dirpath).relative_to(root).parts)
        # .git 单独处理；其余隐藏目录（.venv/.idea 等）跳过
        has_git = ".git" in dirnames
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and (d == ".git" or not d.startswith("."))]
        if has_git:
            results.append(_make_info(root, Path(dirpath), depth))
            # .git 已从 dirnames 移除，os.walk 不会深入
            continue
        if depth >= max_depth:
            dirnames[:] = []  # 不再下钻
    results.sort(key=lambda i: i.path)
    return results


def filter_new(existing: List[RepoInfo], known_keys: set) -> List[RepoInfo]:
    """剔除已入库的仓库，只保留新的。known_keys 为已知 (host/owner/repo 或 path:xxx)。"""
    return [i for i in existing if i.key not in known_keys]


def to_spec(info: RepoInfo) -> RepoSpec:
    """把扫描到的仓库转成可复用更新链路的 RepoSpec。

    folder_name 保留实际目录名——GitService 按 folder_name 定位目录，
    从而对非规范命名的已有仓库也能正确增量更新。
    本地路径远端（file:// / C:/x）同样能 git fetch 增量更新——只有完全无远端
    URL 才是 is_local（纯本地，不 fetch/merge）。
    """
    url = info.url_https or info.remote_url
    is_local = not url
    spec = RepoSpec(owner=info.owner or info.folder_name,
                    repo=info.repo or info.folder_name,
                    url_https=url or info.path,
                    display=info.display,
                    folder_name=info.folder_name,
                    is_local=is_local,
                    local_path=info.path)
    return spec


def known_keys_from_db(rows: list) -> set:
    """从 db 行构造去重键集合（与 RepoInfo.key 对齐）。"""
    keys = set()
    for r in rows:
        if r.get("owner") and r.get("repo"):
            keys.add(f"{r['owner']}/{r['repo']}")
        keys.add(f"path:{r.get('local_path')}")
    return keys
