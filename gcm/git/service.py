# -*- coding: utf-8 -*-
"""git 核心服务：clone / fetch 增量更新 / 断点续传 / 冲突检测。"""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from ..models import RepoSpec, StreamChunk, SyncAction, SyncResult, SyncStatus

LineCallback = Callable[[StreamChunk], None]

# 常见默认分支，用于冲突时提示
_COMMON_BRANCHES = ("main", "master", "develop", "dev")


def _git_exec(env: Optional[Dict[str, str]] = None) -> List[str]:
    return ["git", "-c", "core.quotepath=false"]


def _merge_base(git_dir: str) -> Optional[str]:
    """返回 HEAD 与远端跟踪分支的 merge-base；无则 None。"""
    try:
        r = subprocess.run(
            ["git", "-C", git_dir, "merge-base", "HEAD", "@{upstream}"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode == 0:
            return r.stdout.strip() or None
    except Exception:
        pass
    return None


def _is_dirty(git_dir: str) -> bool:
    """是否存在未提交的本地改动（含未跟踪文件）。"""
    try:
        r = subprocess.run(
            ["git", "-C", git_dir, "status", "--porcelain"],
            capture_output=True, text=True, timeout=30,
        )
        return bool(r.stdout.strip())
    except Exception:
        return True


def _branch_ahead(git_dir: str) -> int:
    """HEAD 领先 upstream 的提交数。"""
    try:
        r = subprocess.run(
            ["git", "-C", git_dir, "rev-list", "--count", "HEAD..@{upstream}"],
            capture_output=True, text=True, timeout=30,
        )
        return 0
    except Exception:
        return 0


def _detect_conflict(git_dir: str) -> Tuple[bool, str]:
    """检测本地是否有会阻止 fast-forward 的改动。返回 (is_conflict, reason)。"""
    if not _is_dirty(git_dir):
        return False, ""
    # 有未提交改动：若本地领先上游或与上游分叉，则 pull 可能冲突
    ahead = 0
    try:
        r = subprocess.run(
            ["git", "-C", git_dir, "rev-list", "--count", "@{upstream}..HEAD"],
            capture_output=True, text=True, timeout=30,
        )
        ahead = int(r.stdout.strip()) if r.returncode == 0 else 0
    except Exception:
        ahead = 0
    if ahead > 0:
        return True, f"本地领先上游 {ahead} 个提交且存在未提交改动"
    return False, ""


def _progress_from_line(line: str) -> Optional[str]:
    """从 git 进度行提取百分比字符串，例如 45%。"""
    m = re.search(r"(\d{1,3})%", line)
    return m.group(1) if m else None


def run_git_ui(cmd: List[str], cwd: str, on_line: LineCallback,
               env: Optional[Dict[str, str]] = None,
               label: str = "git", cancelled: Callable[[], bool] = lambda: False,
               timeout: float | None = None) -> Tuple[int, str]:
    """运行 git 命令并实时转发输出。返回 (returncode, 最后进度)。"""
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        creationflags=creationflags, env=env,
    )
    last_progress = ""
    start = time.time()
    try:
        for raw in proc.stdout:
            if cancelled():
                proc.terminate()
                break
            line = raw.rstrip("\r\n")
            if line:
                on_line(StreamChunk(index=0, text=line, level="info"))
                p = _progress_from_line(line)
                if p:
                    last_progress = p
            if timeout and time.time() - start > timeout:
                proc.terminate()
                break
    except Exception:
        pass
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
    try:
        proc.wait(timeout=15)
    except Exception:
        proc.kill()
        try:
            proc.wait()
        except Exception:
            pass
    return (proc.returncode if proc.returncode is not None else -1), last_progress


class GitService:
    """封装 git 操作，提供 clone / 增量同步 / 分支信息。"""

    def __init__(self, root_dir: str | Path, on_line: Optional[LineCallback] = None,
                 cancelled: Optional[Callable[[], bool]] = None):
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.on_line = on_line or (lambda c: None)
        self.cancelled = cancelled or (lambda: False)

    # ------------------------------------------------------------ 工具
    def _repo_dir(self, spec: RepoSpec) -> Path:
        return self.root / spec.folder_name

    def _emit(self, text: str, level: str = "info"):
        self.on_line(StreamChunk(index=0, text=text, level=level))

    def _local_branch(self, repo_dir: Path) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_dir), "symbolic-ref", "--short", "HEAD"],
                capture_output=True, text=True, timeout=20,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass
        return "main"

    # ------------------------------------------------------------ 主入口
    def sync(self, spec: RepoSpec) -> SyncResult:
        """统一入口：存在则增量更新，不存在则克隆。"""
        res = SyncResult(spec=spec, status=SyncStatus.RUNNING)
        repo_dir = self._repo_dir(spec)
        res.path = str(repo_dir)
        t0 = time.time()

        if repo_dir.exists() and (repo_dir / ".git").exists():
            return self._update(spec, res)
        # 半成品目录（断点残留）
        if repo_dir.exists():
            self._emit(f"[警告] 发现不完整目录 {repo_dir.name}，尝试清理后重新克隆", "warn")
            try:
                import shutil
                shutil.rmtree(repo_dir)
            except Exception as e:
                res.status = SyncStatus.FAILED
                res.action = SyncAction.FAILED
                res.message = "无法清理残留目录"
                res.detail = str(e)
                res.ended = time.time()
                return res
        return self._clone(spec, res)

    # ------------------------------------------------------------ clone
    def _clone(self, spec: RepoSpec, res: SyncResult) -> SyncResult:
        repo_dir = self._repo_dir(spec)
        self._emit(f"开始克隆 {spec.display} …")
        cmd = ["git", "clone", "--progress", spec.url_https, str(repo_dir)]
        rc, prog = run_git_ui(cmd, str(self.root), self.on_line,
                              cancelled=self.cancelled)
        res.ended = time.time()
        res.duration_ms = int((res.ended - res.started) * 1000)
        if self.cancelled():
            res.status = SyncStatus.CANCELLED
            res.action = SyncAction.CANCELLED
            res.message = "已取消"
            return res
        if rc != 0:
            res.status = SyncStatus.FAILED
            res.action = SyncAction.FAILED
            res.message = "克隆失败"
            res.detail = f"git clone 退出码 {rc}"
            return res
        head = self._head_sha(repo_dir)
        res.head_sha = head
        res.remote_sha = head
        res.status = SyncStatus.SUCCESS
        res.action = SyncAction.CLONED
        res.message = "克隆完成"
        res.commits = 0
        return res

    # ------------------------------------------------------------ update
    def _update(self, spec: RepoSpec, res: SyncResult) -> SyncResult:
        repo_dir = self._repo_dir(spec)
        res.started = time.time()
        before = self._head_sha(repo_dir)
        res.head_sha = before

        # 1) fetch
        self._emit(f"检查 {spec.display} 远端更新 …")
        rc, _ = run_git_ui(
            ["git", "fetch", "--progress", "--prune", "origin"],
            str(repo_dir), self.on_line, cancelled=self.cancelled, timeout=180,
        )
        if self.cancelled():
            res.status = SyncStatus.CANCELLED
            res.action = SyncAction.CANCELLED
            res.message = "已取消"
            return res
        if rc != 0:
            res.status = SyncStatus.FAILED
            res.action = SyncAction.FAILED
            res.message = "fetch 失败"
            res.detail = f"git fetch 退出码 {rc}"
            return res

        # 2) 计算可以快进的提交数
        remote_ref = "origin/" + self._local_branch(repo_dir)
        commits_new = self._count_new_commits(repo_dir, remote_ref)

        # 3) 冲突检测
        conflict, reason = _detect_conflict(str(repo_dir))
        if conflict:
            self._emit(f"[冲突] {spec.display}：{reason}。保留本地改动，跳过合并。", "warn")
            res.status = SyncStatus.CONFLICT
            res.action = SyncAction.CONFLICT
            res.message = "本地改动冲突，已跳过合并"
            res.detail = reason
            res.remote_sha = self._remote_head(repo_dir)
            res.commits = commits_new
            res.ended = time.time()
            return res

        # 4) merge --ff-only（或 rebase）
        if commits_new > 0:
            self._emit(f"{spec.display} 有 {commits_new} 个新提交，执行快进合并 …")
            rc, _ = run_git_ui(
                ["git", "merge", "--ff-only", "@{upstream}"],
                str(repo_dir), self.on_line, cancelled=self.cancelled, timeout=120,
            )
            if rc != 0:
                # 无法快进（分叉），尝试 rebase
                self._emit("快进合并失败，尝试 rebase …", "warn")
                rc, _ = run_git_ui(
                    ["git", "rebase", "@{upstream}"],
                    str(repo_dir), self.on_line, cancelled=self.cancelled, timeout=120,
                )
                if rc != 0:
                    # 中止 rebase，保留原状态
                    try:
                        subprocess.run(["git", "-C", str(repo_dir), "rebase", "--abort"],
                                       capture_output=True, timeout=30)
                    except Exception:
                        pass
                    res.status = SyncStatus.CONFLICT
                    res.action = SyncAction.CONFLICT
                    res.message = "合并/rebase 冲突，已中止"
                    res.detail = "本地与远端分叉且存在冲突，已回滚，保留本地改动"
                    res.commits = commits_new
                    res.remote_sha = self._remote_head(repo_dir)
                    res.ended = time.time()
                    return res
        else:
            self._emit(f"{spec.display} 已是最新。")

        after = self._head_sha(repo_dir)
        res.head_sha = after
        res.remote_sha = self._remote_head(repo_dir)
        res.commits = commits_new
        res.status = SyncStatus.SUCCESS
        res.action = SyncAction.UPDATED if commits_new > 0 else SyncAction.FETCHED
        res.message = f"更新完成（+{commits_new} 提交）" if commits_new > 0 else "已是最新"
        res.ended = time.time()
        res.duration_ms = int((res.ended - res.started) * 1000)
        return res

    # ------------------------------------------------------------ helpers
    def _head_sha(self, repo_dir: Path) -> str:
        try:
            r = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                               capture_output=True, text=True, timeout=20)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _remote_head(self, repo_dir: Path) -> str:
        branch = self._local_branch(repo_dir)
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", f"origin/{branch}"],
                capture_output=True, text=True, timeout=20,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _count_new_commits(self, repo_dir: Path, remote_ref: str) -> int:
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-list", "--count", f"HEAD..{remote_ref}"],
                capture_output=True, text=True, timeout=30,
            )
            return int(r.stdout.strip()) if r.returncode == 0 else 0
        except Exception:
            return 0

    def current_branch(self, repo_dir: Path) -> str:
        return self._local_branch(repo_dir)
