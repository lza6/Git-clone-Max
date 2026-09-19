"""本地 git 仓库扫描：发现目标目录下所有已有 git 仓库，纳入统一管理。

解决『用户以前就有 git 仓库，也想用本软件统一更新』的场景：
任意根目录下凡是 git 仓库（含 .git 目录 / .git 文件 worktree / 裸仓库）都能被发现，
解析出远端 URL / owner / repo / HEAD，供 UI 批量入库后走既有增量更新链路。

性能说明（P-perf 重构）：remote URL 与 HEAD 优先**纯文件读取**（config / HEAD /
refs/heads/* / packed-refs），每仓库从 2 次 git 子进程降到 0~1 次（仅极少数回退）；
枚举用 os.scandir 栈式遍历并跳过 junction/ReparsePoint 防环；候选仓库用线程池并发采集。
"""
from __future__ import annotations

import configparser
import os
import re
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from ..models import RepoSpec

# 无需深入的无意义目录
_SKIP_DIRS = {"node_modules", ".venv", "venv", "dist", "build",
              "__pycache__", ".idea", ".vscode", "site-packages"}

_GIT_TIMEOUT = 5  # git 查询单次超时（秒，仅回退路径使用）

_HEX40 = re.compile(r"[0-9a-fA-F]{40}")


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
    """在 path 下跑 git 查询命令（回退路径用），失败返回空串。"""
    try:
        r = subprocess.run(
            ["git", "-C", str(path), *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=_GIT_TIMEOUT,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


# ---------------------------------------------------------------- 纯文件读取

def _resolve_git_dir(path: Path) -> Path | None:
    """返回仓库的 git 目录（实际存 git 数据的位置），不是仓库则返回 None。

    覆盖三种形态：
    - 常规仓库：<root>/.git/ 目录
    - worktree/submodule：<root>/.git 为文件，内容 "gitdir: <真实路径>"
    - 裸仓库：目录名以 .git 结尾且含 HEAD + config
    """
    d = path / ".git"
    if d.is_dir():
        return d
    if d.is_file():
        # worktree/submodule：gitdir 指针文件
        try:
            txt = d.read_text(encoding="utf-8", errors="replace").strip()
            if txt.startswith("gitdir:"):
                gd = Path(txt.split(":", 1)[1].strip())
                if not gd.is_absolute():
                    gd = d.parent / gd
                return gd.resolve()
        except Exception:
            return None
        return None
    # 裸仓库：目录名 *.git + HEAD/config 存在，且无 .git 子项
    try:
        if path.is_dir() and path.name.endswith(".git") and \
                (path / "HEAD").is_file() and (path / "config").is_file() and \
                not (path / ".git").exists():
            return path
    except Exception:
        pass
    return None


def is_git_repo_path(path: Path) -> bool:
    """是否为 git 仓库目录（常规/worktree/裸仓）。"""
    return _resolve_git_dir(path) is not None


def _worktree_base(git_dir: Path) -> Path | None:
    """worktree 的 gitdir（<主>/.git/worktrees/<wt>）无 config/packed-refs，
    它们位于 <主>/.git；返回该 base（否则 None）。"""
    try:
        if git_dir.parent.name == "worktrees" and git_dir.parent.parent.name == ".git":
            return git_dir.parent.parent
    except Exception:
        pass
    return None


def _remote_url_from_config(git_dir: Path) -> str:
    """从 <gitdir>/config 的 [remote "origin"] url 读远端（0 次子进程）。"""
    for base in (git_dir, _worktree_base(git_dir)):
        if base is None:
            continue
        cfg = base / "config"
        if not cfg.is_file():
            continue
        try:
            cp = configparser.ConfigParser(strict=False)
            cp.read(str(cfg), encoding="utf-8")
            if cp.has_section('remote "origin"'):
                return cp.get('remote "origin"', "url", fallback="").strip()
        except Exception:
            continue
    return ""


def _head_sha_from_files(git_dir: Path) -> str:
    """纯文件读 HEAD：HEAD → refs/heads/* → packed-refs；失败返回空（回退 git）。"""
    try:
        head = (git_dir / "HEAD").read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""
    if not head.startswith("ref: "):
        return head.strip() if _HEX40.fullmatch(head.strip()) else ""
    ref = head[5:].strip()  # 如 refs/heads/main
    base = _worktree_base(git_dir)
    for candidate in (git_dir, base):
        if candidate is None:
            continue
        p = candidate / ref
        if p.is_file():
            try:
                sha = p.read_text(encoding="utf-8", errors="replace").strip()
                return sha if _HEX40.fullmatch(sha) else ""
            except Exception:
                pass
    for candidate in (git_dir, base):
        if candidate is None:
            continue
        try:
            packed = (candidate / "packed-refs").read_text(
                encoding="utf-8", errors="replace")
            for line in packed.splitlines():
                line = line.strip()
                if line and not line.startswith("#") and line.endswith(" " + ref):
                    sha = line.split(" ", 1)[0]
                    if _HEX40.fullmatch(sha):
                        return sha
        except Exception:
            continue
    return ""


def _head_sha(path: Path) -> str:
    """当前 HEAD（纯文件优先，回退 git rev-parse）。"""
    gd = _resolve_git_dir(path)
    if gd is not None:
        sha = _head_sha_from_files(gd)
        if sha:
            return sha
    return _run_git(path, "rev-parse", "HEAD")


def _remote_url(path: Path) -> str:
    """origin 远端 URL（config 纯文件优先，回退 git remote get-url）。"""
    gd = _resolve_git_dir(path)
    if gd is not None:
        url = _remote_url_from_config(gd)
        if url:
            return url
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
    return re.match(r"^[A-Za-z]:[/\\]", url) is not None or url.startswith("/")


def parse_remote_url(url: str) -> tuple:
    """把远端 URL 解析成 (owner, repo, url_https, host)。

    支持 https://host/o/r(.git) / git@host:o/r(.git) / ssh://git@host/o/r.git。
    本地路径（file:// / C:/x / /x/y）返回空——纯本地，无远端可解析。
    无法解析时返回 ("", "", "", host 或 "")——调用方用目录名兜底。
    """
    url = (url or "").strip()
    if not url or remote_is_local(url):
        return ("", "", "", "")
    # 复用通用解析器（多平台 HTTPS/SSH/子组）；GitHub 走旧 parse_repo_url 保持 folder 语义
    from ..app.url_lib import host_of, parse_any_repo_url, parse_repo_url
    spec = parse_any_repo_url(url)
    if spec is not None:
        host = host_of(url) or "github.com"
        # 本地扫描的规范名用 host__owner__repo；github 用 owner__repo
        if host == "github.com":
            return (spec.owner, spec.repo, spec.url_https, "github.com")
        return (spec.owner, spec.repo, spec.url_https, host)
    spec = parse_repo_url(url)
    if spec is not None:
        return (spec.owner, spec.repo, spec.url_https, "github.com")
    # 兜底通用正则
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


def _iter_git_candidates(root: Path, max_depth: int):
    """栈式 os.scandir 遍历，跳过 junction/ReparsePoint 与依赖目录；产出疑似 git 仓库目录。

    语义与旧 os.walk 一致：仓库在 depth<=max_depth 可被找到；到达 max_depth 后不再下钻。
    返回 (path, depth) 元组。
    """
    stack = [(root, 0)]
    while stack:
        dirpath, depth = stack.pop()
        if _resolve_git_dir(dirpath) is not None:
            yield (dirpath, depth)
            continue  # 仓库内部不再下钻（含 .git / 裸仓）
        if depth >= max_depth:
            continue
        try:
            with os.scandir(dirpath) as it:
                entries = list(it)
        except OSError:
            continue
        for e in entries:
            try:
                if not e.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            # 跳过 junction / reparse point（Windows 目录联接，防环/防重复）
            try:
                if hasattr(e, "is_junction") and e.is_junction():
                    continue
                st = e.stat(follow_symlinks=False)
                if getattr(st, "st_reparse_tag", 0):
                    continue
            except OSError:
                continue
            name = e.name
            if name in _SKIP_DIRS or (name.startswith(".") and name != ".git"):
                continue
            stack.append((Path(e.path), depth + 1))


def scan_git_dirs(root: str | Path, max_depth: int = 2, *,
                  workers: int | None = None,
                  progress=None, cancelled=None) -> list[RepoInfo]:
    """扫描根目录下所有 git 仓库目录（常规/worktree/裸仓，下钻最多 max_depth 层）。

    - 不深入 .git / 依赖目录等内部；跳过 junction/reparse 防环
    - 候选仓库用线程池并发采集（remote/HEAD 为纯文件读，快）
    - progress(done, total)：每完成一个候选回调（线程安全）；cancelled() 返回 True 时提前停止
    - 返回按路径排序的 RepoInfo 列表；目录读取异常自动跳过，不中断整体扫描
    """
    root = Path(root)
    if not root.is_dir():
        return []
    candidates = list(_iter_git_candidates(root, max_depth))
    total = len(candidates)
    if progress is not None:
        try:
            progress(0, total)
        except Exception:
            pass
    if not candidates:
        return []

    n_workers = workers if workers and workers > 0 else min(16, (os.cpu_count() or 4) + 4)
    lock = threading.Lock()
    infos: list[RepoInfo] = []
    done = [0]

    def _work(item):
        # 取消检查：提前停止（不发 progress，返回 None）
        if cancelled is not None:
            try:
                if cancelled():
                    return
            except Exception:
                pass
        dirpath, depth = item
        try:
            info = _make_info(root, Path(dirpath), depth)
        except Exception:
            info = None
        with lock:
            if info is not None:
                infos.append(info)
            done[0] += 1
            if progress is not None:
                try:
                    progress(done[0], total)
                except Exception:
                    pass

    with ThreadPoolExecutor(max_workers=n_workers) as ex:
        list(ex.map(_work, candidates))

    infos.sort(key=lambda i: i.path)
    return infos


def filter_new(existing: list[RepoInfo], known_keys: set) -> list[RepoInfo]:
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
    # 只要存在任意远端（含本地路径远端），就不是纯本地 → 可增量更新。
    # 注意：url_https 不再回填 info.path —— 纯本地仓库无远端，url 应保持为空，
    # 否则入 DB 后 url 非空会让 _rows_to_specs 误判 is_local=False（审计 H1）。
    is_local = not (info.remote_url or info.url_https)
    spec = RepoSpec(owner=info.owner or info.folder_name,
                    repo=info.repo or info.folder_name,
                    url_https=url,
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
