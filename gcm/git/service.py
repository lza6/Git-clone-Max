# -*- coding: utf-8 -*-
"""git 核心服务：clone / fetch 增量更新 / 断点续传 / 冲突检测。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from ..models import RepoSpec, StreamChunk, SyncAction, SyncResult, SyncStatus

LineCallback = Callable[[StreamChunk], None]

# 常见默认分支，用于冲突时提示
_COMMON_BRANCHES = ("main", "master", "develop", "dev")

# Windows 下禁用子进程弹窗；独立进程组便于整树终止
_CREATE_FLAGS = 0
if os.name == "nt":
    _CREATE_FLAGS = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0)
        | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    )


@dataclass
class _Backoff:
    """重试退避调度：dirty 非零时导致立即成功，见 run_git_ui 说明。"""

    delays: Tuple[float, ...] = (1.0, 3.0, 8.0)
    max_attempts: int = 3


def _retry_delays(retries: int) -> Tuple[float, ...]:
    """按配置的重试次数生成退避间隔。retries=2 → (1.0, 3.0)。"""
    base = (1.0, 3.0, 8.0)
    return base[:max(0, retries)]


def _is_dirty(git_dir: str) -> bool:
    """是否存在未提交的本地改动（含未跟踪文件）。

    目录不存在 / git 失败（rc!=0）视为脏（True）——保守起见，宁可判冲突
    也不冒险覆盖用户可能尚未保存的工作。
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(git_dir), "status", "--porcelain"],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            return True
        return bool(r.stdout.strip())
    except Exception:
        return True


def _divergence_info(git_dir: str) -> Tuple[int, int]:
    """返回本地与上游的分叉信息 (ahead, behind)。

    ahead  = 本地领先上游的提交数（@{upstream}..HEAD）
    behind = 上游领先本地的提交数（HEAD..@{upstream}）
    任何一步失败均当作 (0, 0) 安全值，不阻塞后续流程。
    """
    def _count(spec: str) -> int:
        try:
            r = subprocess.run(
                ["git", "-C", git_dir, "rev-list", "--count", spec],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode == 0:
                return int(r.stdout.strip() or 0)
        except Exception:
            pass
        return 0

    return _count("@{upstream}..HEAD"), _count("HEAD..@{upstream}")


def _detect_conflict(git_dir: str) -> Tuple[bool, str]:
    """检测本地是否已有会阻止 fast-forward 的改动。

    语义：**仅检测本地工作树/已提交的「脏」状态**；本地领先远端（ahead>0）能否
    安全快进由调用方（_update 的 merge --ff-only 失败路径）处理，此处不判。
    - 无未提交改动（clean）→ 不冲突（即使 ahead>0，fast-forward 失败会走 rebase 流程）
    - 有未提交改动：本地领先上游时 pull 必然受阻 → 冲突
    """
    if not _is_dirty(git_dir):
        return False, ""
    # 有未提交改动：若本地领先上游，则 pull 必然受阻
    ahead, _ = _divergence_info(git_dir)
    if ahead > 0:
        return True, f"本地领先上游 {ahead} 个提交且存在未提交改动"
    return False, ""


def _classify_failure(tail: str) -> tuple:
    """对克隆/更新失败的最后几行做人类可读分类。

    返回 (message, detail)。用于平台限制（Windows 非法文件名 / 目录占用）、
    仓库类、网络类之外的明确提示；不改变原有 retry 逻辑（由 _is_networkish_error 把关）。
    """
    t = (tail or "").lower()
    if "invalid path" in t:
        return ("仓库含 Windows 不允许的文件名，无法检出",
                "该仓库存在 Windows 禁止的字符（如冒号 ':'）。git 无法在 Windows 上检出；"
                "可尝试在 WSL / Linux / GitHub Codespaces 中克隆。")
    if "file exists" in t:
        return ("目标目录已存在（重复提交或残留）",
                "同名仓库已被占用或上次克隆残留，已自动去重/清理后重新尝试。")
    if "unable to checkout" in t or "unable to create file" in t:
        return ("检出工作区失败（平台/文件系统限制）",
                "可能因长路径、文件系统限制或仓库内含非法文件名导致 checkout 失败。")
    return ("克隆失败", "git 操作失败，详见日志。")


def _progress_from_line(line: str) -> Optional[str]:
    """从 git 进度行提取百分比字符串，例如 45%。"""
    m = re.search(r"(\d{1,3})%", line)
    return m.group(1) if m else None


def _is_networkish_error(text: str) -> bool:
    """粗略区分『网络类』错误（值得重试）与『仓库类』错误（重试无效）。

    网络/远端临时故障：Connection / stream ended / RPC failed / 超时 / reset / 远端挂断 等。
    仓库本身问题：Repository not found（404）、Authentication failed（认证）等。
    """
    t = text.lower()
    net = (
        "connection", "timed out", "timeout", "rpc failed", "stream ended",
        "early eof", "read error", "reset by peer", "unable to resolve",
        "could not resolve", "network is unreachable", "getaddrinfo",
        "ssl", "tls", "fatal: unable to access", "remote end hung up",
    )
    repo = (
        "repository not found", "authentication failed",
        "invalid username or password", "could not read username",
        "access denied", "permission denied",
        "could not read from remote",      # clone 本地路径不存在 / 无权限 → 仓库类
        "does not appear to be a git repository",
        "please make sure you have the correct access rights",
    )
    # Windows 平台限制类：重试无效，必须跳过
    platform = (
        "invalid path",                    # 含 Windows 禁止字符（冒号等）的文件名
        "file exists",                     # 目标目录被占用（重复提交/残留）
        "unable to checkout",              # checkout 阶段失败（平台/文件系统限制）
        "unable to create file",           # 文件系统不允许
    )
    if any(k in t for k in platform):
        return False  # 平台限制不重试
    # 先命中『仓库类』再命中『网络类』；网络类优先判断避免被通用串覆盖
    if any(k in t for k in repo):
        return False
    return any(k in t for k in net)


def _kill_tree(proc: subprocess.Popen) -> None:
    """尽量终止 git 整棵进程树（Windows 用 taskkill /T /F）。"""
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt" and proc.pid:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True, timeout=15,
            )
            return
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def run_git_ui(cmd: List[str], cwd: str, on_line: LineCallback,
               env: Optional[Dict[str, str]] = None,
               label: str = "git", cancelled: Callable[[], bool] = lambda: False,
               timeout: float | None = None,
               retries: int = 0, backoff: Tuple[float, ...] = (1.0, 3.0, 8.0)) -> Tuple[int, str]:
    """运行 git 命令并实时转发输出，带超时与整树终止。

    返回 (returncode, 最后进度)。`retries` > 0 时，网络类失败自动重试。

    实现要点：
    - 读线程持续泵 stdout，主线程用 proc.wait(timeout) 兜底，静默挂死也能按时触发超时。
    - 成功（rc==0）立即 break，绝不重复启动子进程。
    """
    last_progress = ""
    attempts = 0
    max_attempts = max(1, retries + 1)
    rc = -1
    while attempts < max_attempts:
        proc = subprocess.Popen(
            cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            creationflags=_CREATE_FLAGS, env=env,
        )
        start = time.time()
        log: List[str] = []
        timed_out = False
        cancelled_flag = False

        def _pump():
            """读线程：持续读行、转发、记录；超时/取消时终止进程树。"""
            nonlocal timed_out, cancelled_flag, last_progress
            try:
                for raw in proc.stdout:
                    if cancelled():
                        cancelled_flag = True
                        _kill_tree(proc)
                        break
                    if timeout and time.time() - start > timeout:
                        timed_out = True
                        _kill_tree(proc)
                        break
                    line = raw.rstrip("\r\n")
                    if line:
                        on_line(StreamChunk(index=0, text=line, level="info"))
                        log.append(line)
                        p = _progress_from_line(line)
                        if p:
                            last_progress = p
                # 读线程正常结束（子进程退出）后，进程可能仍在运行
                # （pump join/timeout 场景），此时再补检一次取消标志，
                # 否则取消请求会被吞掉、状态误判为 SUCCESS。
                if not cancelled_flag and not timed_out and cancelled():
                    cancelled_flag = True
                    _kill_tree(proc)
            except Exception:
                pass
            finally:
                try:
                    proc.stdout.close()
                except Exception:
                    pass

        import threading as _threading
        pump = _threading.Thread(target=_pump, daemon=True)
        pump.start()
        # 主线程阻塞等待，超时则整树终止（读线程内也有超时检查，双保险）
        wait_limit = (timeout or 0) + 5 if timeout else None
        try:
            proc.wait(timeout=wait_limit)
        except Exception:
            timed_out = True
            _kill_tree(proc)
            # 终止后必须再次 wait 回收子进程对象，避免 ResourceWarning 与僵尸
            try:
                proc.wait(timeout=15)
            except Exception:
                pass
        try:
            pump.join(timeout=5)
        except Exception:
            pass
        # 确保读线程已退出（其 finally 已关闭 stdout），兜底再关一次防 ResourceWarning
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        rc = proc.returncode if proc.returncode is not None else -1

        # 成功：立即收尾，绝不重复启动
        if rc == 0 and not cancelled_flag and not timed_out:
            break

        if cancelled_flag:
            rc = -1  # 取消统一按非 0 处理
            break

        if timed_out:
            if attempts < max_attempts - 1 and not cancelled():
                on_line(StreamChunk(index=0, text="操作超时，自动重试…", level="warn"))
                time.sleep(backoff[attempts] if attempts < len(backoff) else backoff[-1])
                attempts += 1
                continue
            break

        if rc != 0 and not cancelled():
            tail = "\n".join(log[-8:]) if log else ""
            if _is_networkish_error(tail) and attempts < max_attempts - 1:
                delay = backoff[attempts] if attempts < len(backoff) else backoff[-1]
                on_line(StreamChunk(
                    index=0,
                    text=f"网络抖动，{delay:.0f}s 后自动重试（{attempts + 1}/{max_attempts - 1}）…",
                    level="warn",
                ))
                time.sleep(delay)
            else:
                break  # 仓库类错误 / 已达上限：直接返回
        attempts += 1
    return rc, last_progress


class GitService:
    """封装 git 操作，提供 clone / 增量同步 / 分支信息。"""

    def __init__(self, root_dir: str | Path, on_line: Optional[LineCallback] = None,
                 cancelled: Optional[Callable[[], bool]] = None,
                 fetch_timeout: float = 300, clone_timeout: float = 600,
                 retries: int = 0, backoff: Tuple[float, ...] = (1.0, 3.0, 8.0),
                 proxy: str = "", fetch_depth: int = 0, unshallow: bool = False):
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.on_line = on_line or (lambda c: None)
        self.cancelled = cancelled or (lambda: False)
        self.fetch_timeout = fetch_timeout
        self.clone_timeout = clone_timeout
        self.retries = max(0, retries)
        self._backoff = backoff
        self.proxy = proxy.strip()
        self.fetch_depth = max(0, int(fetch_depth))   # >0 时浅层仓库 fetch 带 --depth
        self.unshallow = bool(unshallow)              # True 时浅层仓库 fetch --unshallow 拉全量

    def _env(self, extra: Optional[Dict[str, str]] = None) -> Optional[Dict[str, str]]:
        """构造 subprocess 环境：未设置代理时注入 http_proxy/https_proxy。"""
        if not self.proxy:
            return extra
        env = dict(os.environ)
        if extra:
            env.update(extra)
        env.setdefault("http_proxy", self.proxy)
        env.setdefault("https_proxy", self.proxy)
        return env

    # ------------------------------------------------------------ 工具
    def _repo_dir(self, spec: RepoSpec) -> Path:
        # 导入仓库优先用其真实本地路径
        if spec.local_path:
            return Path(spec.local_path)
        return self.root / spec.folder_name

    def _is_shallow_repo(self, repo_dir: Path) -> bool:
        """检测仓库是否为浅克隆。

        .git/shallow 标记文件优先；worktree 场景 .git 为文件（gitdir: 指向），
        走 git rev-parse 兜底（对 worktree 同样有效）。
        """
        git_dir = repo_dir / ".git"
        if git_dir.is_dir() and (git_dir / "shallow").exists():
            return True
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", "--is-shallow-repository"],
                capture_output=True, text=True, timeout=20,
            )
            return r.returncode == 0 and r.stdout.strip() == "true"
        except Exception:
            return False

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
        res.started = time.time()
        t0 = time.time()

        try:
            if repo_dir.exists() and (repo_dir / ".git").exists():
                # 纯本地仓库（无远端）：不做 fetch/merge，仅记录已是最新
                if spec.is_local:
                    res.status = SyncStatus.SUCCESS
                    res.action = SyncAction.FETCHED
                    res.message = "本地仓库（无远端）"
                    res.head_sha = self._head_sha(repo_dir)
                    res.remote_sha = ""
                    return res
                return self._update(spec, res)
            # 半成品目录（断点残留）
            if repo_dir.exists():
                self._emit(f"[警告] 发现不完整目录 {repo_dir.name}，尝试清理后重新克隆", "warn")
                try:
                    shutil.rmtree(repo_dir)
                except Exception as e:
                    res.status = SyncStatus.FAILED
                    res.action = SyncAction.FAILED
                    res.message = "无法清理残留目录"
                    res.detail = str(e)
                    res.ended = time.time()
                    return res
            return self._clone(spec, res)
        finally:
            # 统一兜底：任何路径都必须有结束时间，否则 duration 会为 0
            if res.ended == 0.0:
                res.ended = time.time()
            res.duration_ms = int((res.ended - res.started) * 1000)

    # ------------------------------------------------------------ clone
    def _clone(self, spec: RepoSpec, res: SyncResult) -> SyncResult:
        repo_dir = self._repo_dir(spec)
        self._emit(f"开始克隆 {spec.display} …")
        # 记录最近输出行，供失败分类（平台限制识别）
        self._last_clone_tail: list = []
        prev_on_line = self.on_line

        def _collate(c: StreamChunk) -> None:
            self._last_clone_tail.append(c.text)
            if len(self._last_clone_tail) > 8:
                self._last_clone_tail = self._last_clone_tail[-8:]
            prev_on_line(c)

        cmd = ["git", "clone", "--progress", spec.url_https, str(repo_dir)]
        rc, prog = run_git_ui(cmd, str(self.root), _collate,
                              cancelled=self.cancelled, env=self._env(),
                              timeout=self.clone_timeout, retries=self.retries,
                              backoff=self._backoff)
        if self.cancelled():
            res.status = SyncStatus.CANCELLED
            res.action = SyncAction.CANCELLED
            res.message = "已取消"
            return res
        if rc != 0:
            # 克隆失败：分类给出人类可读提示（平台限制 / 目录占用 / 通用）
            tail = "\n".join(self._last_clone_tail[-8:])
            msg, detail = _classify_failure(tail)
            # Windows 非法文件名等平台限制：保留目录，让用户可见错误；其余清理残留
            if "Windows 不允许" in msg or "无法检出" in msg:
                self._emit(f"[失败] {spec.display}：{msg}", "warn")
            else:
                self._emit(f"克隆失败，清理残留目录 {repo_dir.name}", "warn")
                try:
                    shutil.rmtree(repo_dir, ignore_errors=True)
                except Exception:
                    pass
            res.status = SyncStatus.FAILED
            res.action = SyncAction.FAILED
            res.message = msg
            res.detail = detail or f"git clone 退出码 {rc}"
            return res
        head = self._head_sha(repo_dir)
        res.head_sha = head
        res.remote_sha = head
        res.status = SyncStatus.SUCCESS
        if not head:
            # 空仓库：无人提交，clone 成功但无 HEAD
            res.action = SyncAction.EMPTY
            res.message = "空仓库（暂无提交）"
            return res
        res.action = SyncAction.CLONED
        res.message = "克隆完成"
        res.commits = 0
        return res

    # ------------------------------------------------------------ update
    def _update(self, spec: RepoSpec, res: SyncResult) -> SyncResult:
        repo_dir = self._repo_dir(spec)
        before = self._head_sha(repo_dir)
        res.head_sha = before
        # 空仓库（无头）已存在：hit 导入的空仓库，跳过 fetch 直接标记 empty
        if not before:
            res.status = SyncStatus.SUCCESS
            res.action = SyncAction.EMPTY
            res.message = "空仓库（暂无提交）"
            return res

        # 1) fetch：浅克隆仓库需显式 depth 语义，全量仓库保持原样
        self._emit(f"检查 {spec.display} 远端更新 …")
        fetch_cmd = ["git", "fetch", "--progress", "--prune", "origin"]
        used_unshallow = False
        if self.unshallow and self._is_shallow_repo(repo_dir):
            fetch_cmd = ["git", "fetch", "--progress", "--prune", "--unshallow", "origin"]
            used_unshallow = True
            self._emit(f"{spec.display} 为浅克隆仓库，正在拉取全量历史（--unshallow）…")
        elif self.fetch_depth and self._is_shallow_repo(repo_dir):
            fetch_cmd = ["git", "fetch", "--progress", "--prune",
                         f"--depth={self.fetch_depth}", "origin"]
        rc, _ = run_git_ui(
            fetch_cmd,
            str(repo_dir), self.on_line, cancelled=self.cancelled, env=self._env(),
            timeout=self.fetch_timeout, retries=self.retries, backoff=self._backoff,
        )
        # unshallow/带深度 fetch 失败（远端行为异常）时降级回普通 fetch，避免把仓库标红
        if rc != 0 and (used_unshallow or "--depth=" in " ".join(fetch_cmd)) and not self.cancelled():
            self._emit(f"{spec.display} 浅层 fetch 失败，降级为普通 fetch …", "warn")
            fetch_cmd = ["git", "fetch", "--progress", "--prune", "origin"]
            rc, _ = run_git_ui(
                fetch_cmd,
                str(repo_dir), self.on_line, cancelled=self.cancelled, env=self._env(),
                timeout=self.fetch_timeout, retries=self.retries, backoff=self._backoff,
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

        # 3) 冲突检测（含 rebase 中止兜底：先中止可能残留的 rebase 状态）
        try:
            subprocess.run(["git", "-C", str(repo_dir), "rebase", "--abort"],
                           capture_output=True, timeout=15)
        except Exception:
            pass  # 无 rebase 进行中 → 非 0 忽略
        conflict, reason = _detect_conflict(str(repo_dir))
        if conflict:
            self._emit(f"[冲突] {spec.display}：{reason}。保留本地改动，跳过合并。", "warn")
            res.status = SyncStatus.CONFLICT
            res.action = SyncAction.CONFLICT
            res.message = "本地改动冲突，已跳过合并"
            res.detail = reason
            res.remote_sha = self._remote_head(repo_dir)
            res.commits = commits_new
            return res

        # 3.1) rebase 场景：本地领先远端（分叉但 clean）→ 有未提交改动会中断
        # 由 merge --ff-only 失败路径统一处理（rebase/回滚），此处不再重复检测。

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
