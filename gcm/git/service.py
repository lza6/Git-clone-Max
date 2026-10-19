"""git 核心服务：clone / fetch 增量更新 / 断点续传 / 冲突检测。"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from ..app import applog
from ..app.mirror_probe import (
    global_auth_header_configured,
    is_transient_probe_error,
    mark_probe_health,
    preferred_prefix,
    probe_git_url,
    probe_health,
    probe_state,
)
from ..app.mirrors import (
    build_mirror_urls,
    mirror_prefixes_for,
    normalize_mirror_prefix,
    requires_credentials,
)
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

    delays: tuple[float, ...] = (1.0, 3.0, 8.0)
    max_attempts: int = 3


def _retry_delays(retries: int) -> tuple[float, ...]:
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
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if r.returncode != 0:
            return True
        return bool(r.stdout.strip())
    except Exception:
        return True


def _divergence_info(git_dir: str) -> tuple[int, int]:
    """返回本地与上游的分叉信息 (ahead, behind)。

    ahead  = 本地领先上游的提交数（@{upstream}..HEAD）
    behind = 上游领先本地的提交数（HEAD..@{upstream}）
    任何一步失败均当作 (0, 0) 安全值，不阻塞后续流程。
    """
    def _count(spec: str) -> int:
        try:
            r = subprocess.run(
                ["git", "-C", git_dir, "rev-list", "--count", spec],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
            if r.returncode == 0:
                return int(r.stdout.strip() or 0)
        except Exception:
            pass
        return 0

    return _count("@{upstream}..HEAD"), _count("HEAD..@{upstream}")


def _detect_conflict(git_dir: str) -> tuple[bool, str]:
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
    if "file exists" in t or "already exists" in t:
        return ("目标目录已存在（重复提交或残留）",
                "同名仓库已被占用或上次克隆残留，已自动去重/清理后重新尝试。")
    if "unable to checkout" in t or "unable to create file" in t:
        return ("检出工作区失败（平台/文件系统限制）",
                "可能因长路径、文件系统限制或仓库内含非法文件名导致 checkout 失败。")
    # G71-3：网络类失败给出可解释文案（并让重试队列能识别为可重试）。
    # `_is_networkish_error` 定义在本函数之后，但调用发生在运行时，可正常解析。
    if _is_networkish_error(tail or ""):
        return ("网络连接失败（可自动重试）",
                "远端不可达、连接被重置或超时；可稍后重试，或在设置页开启"
                "「失败自动重试」。")
    return ("克隆失败", "git 操作失败，详见日志。")


def _progress_from_line(line: str) -> Optional[str]:
    """从 git 进度行提取百分比字符串，例如 45%。"""
    m = re.search(r"(\d{1,3})%", line)
    return m.group(1) if m else None


_RATE_RE = re.compile(r"\b([\d.]+)\s*(KiB|MiB|MB|KB)/s\b")
_TOTAL_RE = re.compile(r"(\d+)/(\d+)")


def parse_progress_meta(line: str) -> tuple:
    """从 git 进度行提取 (percent, rate, files) 元数据。

    - percent: 百分比字符串，如 "45"；无则 None
    - rate:    实时速率原始串，如 "732.00 KiB/s"、"7.03 MiB/s"；无则 None
    - files:   对象/文件总数（x/y 中的 y），如 "7124"；无则 None
    任何异常都返回 (None, None, None)，绝不向上抛出。
    """
    try:
        percent = _progress_from_line(line)
        rate = None
        files = None
        m = _RATE_RE.search(line)
        if m:
            rate = f"{m.group(1)} {m.group(2)}/s"
        m2 = _TOTAL_RE.search(line)
        if m2:
            files = m2.group(2)
        if rate is None and files is None:
            return (percent, None, None)
        return (percent, rate, files)
    except Exception:
        return (None, None, None)


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
        "already exists",                  # 新版 git 文案：destination path ... already exists
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


def run_git_ui(cmd: Iterable[str], cwd: str, on_line: LineCallback,
               env: Optional[dict[str, str]] = None,
               label: str = "git", cancelled: Callable[[], bool] = lambda: False,
               timeout: float | None = None,
               retries: int = 0, backoff: tuple[float, ...] = (1.0, 3.0, 8.0),
               progress_detail: Optional[Callable[[str], None]] = None,
               thread_probe: Optional[Callable[[threading.Thread], None]] = None,
               ) -> tuple[int, str]:
    """运行 git 命令并实时转发输出，带超时与整树终止。

    返回 (returncode, 最后进度)。`retries` > 0 时，网络类失败自动重试。
    `progress_detail`：可选回调，收到速率/对象数等附加进度文本（如 "7.03 MiB/s 7124"）。
    `thread_probe`：测试钩子（默认 None），join 判定前后各回调一次读线程引用；
    生产调用方不传，仅用于单测观察读线程生命周期，属向后兼容扩展。

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
        log: list[str] = []
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
                        # 内存护栏：只保留最近 200 行（含原始进度行），
                        # 防止超大仓库克隆日志无限累积导致 OOM（32 并发时尤其明显）
                        if len(log) >= 200:
                            log[:] = log[-200:]
                        log.append(line)
                        p = _progress_from_line(line)
                        if p:
                            last_progress = p
                        # 速率/对象数等附加进度信息：经注入点透传给 UI（无注入点则忽略）
                        meta = parse_progress_meta(line)
                        if meta and (
                            meta[1] is not None or meta[2] is not None
                        ):
                            send = progress_detail or getattr(
                                on_line, "send_progress_detail", None)
                            if send:
                                send(f"{meta[1] or ''} {meta[2] or ''}".strip())
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
        if thread_probe is not None:
            try:
                thread_probe(pump)
            except Exception:
                pass
        try:
            pump.join(timeout=5)
        except Exception:
            pass
        if thread_probe is not None:
            try:
                thread_probe(pump)
            except Exception:
                pass
        if pump.is_alive():
            # G33-4 读线程卡死（极端：管道缓冲满/被杀进程未刷管道）：
            # daemon 已保证进程不挂，这里只输出一次性警告，不再继续等待
            on_line(StreamChunk(
                index=0,
                text=f"git 输出读线程未及时退出，已放弃等待（{label}）",
                level="warn"))
        # G69-2 修复（永久挂死，实测可达）：只有在读线程**确实已退出**时才关 stdout。
        # 原实现无条件 `proc.stdout.close()`：读线程若仍阻塞在 `for raw in proc.stdout`，
        # close() 会与它争同一把 BufferedReader 锁 → **永久阻塞**（实测 `close()` 40s
        # 不返回）。而这段代码的上一句刚刚才说「已放弃等待读线程」—— 转身又掉回那次等待。
        # 后果：该 worker 的 `finished` 永不发出 → `engine._pending` 不归零 → UI 永久
        # 「同步中…」、取消无效、`drain()` 只能超时。
        # 读线程是 daemon，留它自行消亡即可（进程退出不阻塞）。
        if not pump.is_alive():
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
                delay = backoff[attempts] if attempts < len(backoff) else backoff[-1]
                # G22-7：重试文案含剩余等待秒数与原因（用户可感知重试节奏）
                on_line(StreamChunk(
                    index=0,
                    text=f"操作超时，{delay:.0f}s 后自动重试（{attempts + 1}/{max_attempts - 1}）…",
                    level="warn"))
                time.sleep(delay)
                attempts += 1
                continue
            break

        if rc != 0 and not cancelled():
            tail = "\n".join(log[-8:]) if log else ""
            if _is_networkish_error(tail) and attempts < max_attempts - 1:
                delay = backoff[attempts] if attempts < len(backoff) else backoff[-1]
                on_line(StreamChunk(
                    index=0,
                    text=f"网络抖动（原因：{tail.splitlines()[-1].strip() if tail else '未知'}），"
                         f"{delay:.0f}s 后自动重试（{attempts + 1}/{max_attempts - 1}）…",
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
                 retries: int = 0, backoff: tuple[float, ...] = (1.0, 3.0, 8.0),
                 proxy: str = "", fetch_depth: int = 0, unshallow: bool = False,
                 token: str = "", submodule: bool = False, rate_limit_kbps: int = 0,
                 host_tokens: Optional[dict[str, str]] = None,
                 mirror_prefix: Optional[dict[str, object]] = None,
                 mirror_use_presets: bool = True,
                 mirror_fallback: bool = True,
                 mirror_probe_timeout: float = 8.0,
                 host_auth_types: Optional[dict[str, str]] = None,
                 precheck_remote: bool = False,
                 single_branch: bool = False,
                 force_ipv4: bool = False,
                 lfs_enabled: bool = False,
                 mirror: bool = False,
                 post_clone_hook: str = "",
                 ssh_key: str = ""):
        self.root = Path(root_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.on_line = on_line or (lambda c: None)
        self.cancelled = cancelled or (lambda: False)
        self.fetch_timeout = fetch_timeout
        self.clone_timeout = clone_timeout
        self.retries = max(0, retries)
        self._backoff = backoff
        self.proxy = proxy.strip()
        self.token = (token or "").strip()
        # G38-1 按 host 映射凭据（每 host 一个 token；键为 host，值原样传递）
        self.host_tokens: dict[str, str] = dict(host_tokens or {})
        self.mirror_prefix: dict[str, tuple[str, ...]] = normalize_mirror_prefix(
            mirror_prefix)  # G58-2
        self.mirror_use_presets = bool(mirror_use_presets)  # G58-2 内置国内镜像
        self.mirror_fallback = bool(mirror_fallback)        # G58-2 失败回退源站
        self.mirror_probe_timeout = max(1.0, float(mirror_probe_timeout or 8.0))
        self.host_auth_types: dict[str, str] = dict(host_auth_types or {})  # G58-5 认证头类型
        self.precheck_remote = bool(precheck_remote)  # G38-3 远端可达性预检（默认关）
        self.single_branch = bool(single_branch)      # G38-4 单分支浅克隆
        self.force_ipv4 = bool(force_ipv4)            # G38-6 强制 HTTP/1.1（IPv6 兼容修复）
        self.fetch_depth = max(0, int(fetch_depth))   # >0 时浅层仓库 fetch 带 --depth
        self.unshallow = bool(unshallow)              # True 时浅层仓库 fetch --unshallow 拉全量
        self._submodule = bool(submodule)             # G08-1 True 时 clone 带子模块
        self.rate_limit_kbps = max(0, int(rate_limit_kbps or 0))  # G04-4 限速
        # G48 深化
        self._lfs_enabled = bool(lfs_enabled)          # G48-2 Git LFS
        self._mirror = bool(mirror)                    # G48-6 镜像克隆(--mirror)
        self._post_clone_hook = (post_clone_hook or "").strip()  # G48-5
        self._ssh_key = (ssh_key or "").strip()        # G48-3 指定 SSH key
        self._current_url = ""                         # G69-21 认证头作用域用（含端口）

    @staticmethod
    def slow_remote_hint(duration_s: float, threshold: float = 3.0) -> str:
        """G48-9：ls-remote 预检耗时超过阈值 → 建议切换浅克隆/镜像。"""
        if duration_s >= threshold:
            return (f"远端响应偏慢（{duration_s:.1f}s ≥ {threshold:g}s），"
                    "建议本次使用「浅克隆」或「镜像克隆」以提速")
        return ""

    def _lfs_flag(self) -> list[str]:
        """G48-2：LFS 开关开启时 clone/fetch 追加 -c filter.lfs.required=false。"""
        if self._lfs_enabled:
            return ["-c", "filter.lfs.required=false"]
        return []

    @staticmethod
    def resolve_host_token(host_tokens: dict | None, host: str,
                           account: str | None = None) -> str:
        """G48-4：host_tokens[host] 值支持 `user:token` 多账号（每行一个）→ 按账号取。

        无账号 / 值无 `:` → 返回原值（兼容单 token）。
        """
        raw = ((host_tokens or {}).get(host or "", "") or "").strip()
        if not raw:
            return ""
        if raw and ":" in raw and account:
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith(account + ":"):
                    return line.split(":", 1)[1].strip()
        return raw

    def _host_token(self) -> str:
        """G38-1 取当前 host 对应的凭据：host_tokens 映射优先，其次全局 token。

        host 判断：_current_host 为 None/空 → 视为 github（向后兼容本工具 github 主战场）。
        """
        host = (getattr(self, "_current_host", "") or "").lower().replace("www.", "")
        if not host:
            return self.token  # 未知/空 host 默认用全局 token（历史行为仅 github）
        return (self.host_tokens.get(host) or "").strip() or self.token

    def _env(self, extra: Optional[dict[str, str]] = None) -> Optional[dict[str, str]]:
        """构造 subprocess 环境。

        - 未设代理且无 token 时返回 None（调用方透传 None 即继承当前进程环境）。
        - 有代理时注入 http_proxy/https_proxy；有 token 时经 GIT_CONFIG_* 注入
          URL 作用域的 `http.<scheme://netloc/>.extraHeader`（G69-21）（纯 git 支持、免临时脚本，
          仅对本次 git 子进程生效，不污染全局配置）。
        """
        if (not self.proxy and not self._any_token()
                and not self.rate_limit_kbps and not self.force_ipv4
                and not self._ssh_key):
            return extra
        env = dict(os.environ)
        if extra:
            env.update(extra)
        if self._ssh_key:
            # G48-3 指定 SSH key：多账号/公司内网
            env["GIT_SSH_COMMAND"] = f'ssh -i "{self._ssh_key}"'
        if self.proxy:
            env.setdefault("http_proxy", self.proxy)
            env.setdefault("https_proxy", self.proxy)
        # 认证头注入（G38-1 按 host 映射凭据；host 白名单安全语义保持）：
        # - github.com / 空 host（本工具 github 主战场）→ Authorization: Bearer（全局或映射值）
        # - gitlab.com → PRIVATE-TOKEN
        # - 其余 host：仅当 host_tokens 显式登记该 host 时才注入其映射值；
        #   未登记 host 一律不发凭据（F1 安全加固：凭据不外泄到未登记域名）。
        host = (getattr(self, "_current_host", "") or "").lower().replace("www.", "")
        host = host.rstrip("/")
        cfg = []
        # G69-21（安全）：认证头改为 **URL 作用域**，不再用全局 http.extraHeader。
        # 全局头会对该次 git 命令的所有 HTTP 请求生效 —— 只要仓库 origin 被换成
        # 第三方镜像，token 就会被发给镜像。URL 作用域后，只有匹配源站前缀的
        # 请求才带凭据，镜像/重定向一律不带。
        # G69-21：作用域必须保留端口（自建 GitLab 常见 `:8443`）。
        # 用源 URL 的 netloc；没有 URL 时退回 host/github.com。
        _scope = ""
        try:
            from urllib.parse import urlparse
            _parsed = urlparse(getattr(self, "_current_url", "") or "")
            if _parsed.scheme in ("http", "https") and _parsed.hostname:
                _hostport = _parsed.hostname.lower()
                if _parsed.port:
                    _hostport += f":{_parsed.port}"
                _scope = f"{_parsed.scheme}://{_hostport}/"
        except Exception:
            _scope = ""
        if not _scope:
            _scope = f"https://{host}/" if host else "https://github.com/"
        _auth_key = f"http.{_scope}.extraHeader"
        if host in ("", "github.com"):
            tok = self._token_for_host(host)
            if tok:
                cfg.append((_auth_key, f"Authorization: Bearer {tok}"))
        elif host in ("gitlab.com", "gitlab"):
            tok = self._token_for_host(host)
            if tok:
                cfg.append((_auth_key, f"PRIVATE-TOKEN: {tok}"))
        else:
            # 白名单外 host：仅 host_tokens 显式登记时发送（映射值），否则不发
            mapped = (self.host_tokens.get(host) or "").strip()
            if mapped:
                cfgtok = mapped
                # G58-5：按 host_auth_types 映射发对应头（bearer/private_token/basic）；
                # 未配置该 host 时回退 "*"（设置页只提供全局项），再默认 Bearer。
                # G66-9 修复：原实现只查 _hats.get(host)，而 UI 写入的是 _hats["*"]
                # → 设置页选的 Basic/PRIVATE-TOKEN 永不生效（实测仍发 Bearer）。
                _hats = getattr(self, "host_auth_types", None) or {}
                htype = str(_hats.get(host) or _hats.get("*") or "").lower().strip()
                if htype == "private_token":
                    cfg.append((_auth_key, f"PRIVATE-TOKEN: {cfgtok}"))
                elif htype == "basic":
                    import base64 as _b64
                    b = _b64.b64encode(cfgtok.encode("utf-8")).decode("ascii")
                    cfg.append((_auth_key, f"Authorization: Basic {b}"))
                else:
                    cfg.append((_auth_key, f"Authorization: Bearer {cfgtok}"))
        # G04-4 下载限速：低于 lowSpeedLimit KiB/s 持续 lowSpeedTime 秒 → 中止
        if self.rate_limit_kbps > 0:
            cfg.append(("http.lowSpeedLimit", str(self.rate_limit_kbps)))
            cfg.append(("http.lowSpeedTime", "30"))
        # G38-6 强制 HTTP/1.1（IPv6 兼容问题常见修复）
        if self.force_ipv4:
            cfg.append(("http.version", "HTTP/1.1"))
        if cfg:
            # G69-18 修复：**追加**到调用方已有的 GIT_CONFIG_* 之后，而不是从 0 覆盖。
            # 原实现把 index 写死从 0 开始、并把 COUNT 重置为 len(cfg)，
            # 于是当进程环境里本来就带 GIT_CONFIG_*（CI 用
            # `credential.interactive=false` 就是这么设的，Git for Windows
            # 与不少包装脚本也会设）时，会把调用方自己的配置项整条覆盖掉，
            # 且 COUNT 被改小 → 多余项被 git 忽略。表现为「凭据注入悄悄吃掉别人的
            # git 配置」，很难与「token 没生效」区分。
            try:
                base = int(os.environ.get("GIT_CONFIG_COUNT", "0") or 0)
            except ValueError:
                base = -1           # 非数字：走下方「整组不可信」分支
            # 校验调用方那批 **自身是否完整**：git 要求 KEY_i/VALUE_i (i<base)
            # 全部存在。若 COUNT=3 却只有 KEY_0/1，我们直接追加会写出 KEY_3，
            # 留下 index=2 的空洞 —— git 会报
            # `error: missing config key GIT_CONFIG_KEY_2` / `unable to parse command-line config`。
            # 既然这批数据不可信，就整组丢弃（与非数字分支同一处置）。
            if base > 0 and any(
                    f"GIT_CONFIG_{kind}_{i}" not in env
                    for i in range(base) for kind in ("KEY", "VALUE")):
                base = -1
            if base < 0:
                # COUNT 坏了但 KEY_n/VALUE_n 还在的话，环境自相矛盾（git 读 COUNT
                # 决定读几项）。既然无法信任这批数据，整组清掉再从 0 起。
                for k in [k for k in env
                          if k.startswith(("GIT_CONFIG_KEY_",
                                           "GIT_CONFIG_VALUE_",
                                           "GIT_CONFIG_COUNT"))]:
                    env.pop(k, None)
                base = 0
                try:
                    applog.warn("调用方 GIT_CONFIG_* 不完整或非数字，已忽略既有值")
                except Exception:
                    pass
            base = max(base, 0)
            for i, (k, v) in enumerate(cfg):
                env[f"GIT_CONFIG_KEY_{base + i}"] = k
                env[f"GIT_CONFIG_VALUE_{base + i}"] = v
            env["GIT_CONFIG_COUNT"] = str(base + len(cfg))
        return env

    def _any_token(self) -> bool:
        """是否存在任一可用凭据（host_tokens 或全局 token）。"""
        return any(v.strip() for v in self.host_tokens.values()) or bool(self.token)

    def _token_for_host(self, host: str) -> str:
        """G38-1 取指定 host 的凭据：host_tokens 精确命中优先，其次全局 token。

        安全语义（F1）：全局 token 默认只用于 github/空 host（本工具 github 主战场）；
        其它 host 必须显式登记在 host_tokens 才发送，否则返回空（不发任何凭据）。
        """
        host = (host or "").lower().replace("www.", "").rstrip("/")
        mapped = (self.host_tokens.get(host) or "").strip()
        if mapped:
            return mapped
        if host in ("", "github.com"):
            return self.token
        return ""

    def _mirror_url(self, url: str) -> str:
        """G38-2/G58-2 取**第一个用户配置**的镜像前缀拼 URL（保持旧语义）。

        完整的多备援链路见 `_resolve_clone_url()`；这里刻意不含内置预设，
        以免旧调用（`build_clone_cmd` 无 override 时）意外走第三方。
        """
        if not url or not url.lower().startswith("https://"):
            return url
        host = (getattr(self, "_current_host", "") or "").lower().replace("www.", "").rstrip("/")
        prefixes = mirror_prefixes_for(host, self.mirror_prefix, use_presets=False)
        urls = build_mirror_urls(url, prefixes)
        return urls[0] if urls else url

    # ------------------------------------------------------------ G58-2 多备援
    def _mirror_prefixes(self) -> tuple[str, ...]:
        host = (getattr(self, "_current_host", "") or "").lower().replace("www.", "").rstrip("/")
        return mirror_prefixes_for(host, self.mirror_prefix,
                                   use_presets=self.mirror_use_presets)

    def _mirror_candidates(self, url: str) -> list[str]:
        return build_mirror_urls(url, self._mirror_prefixes())

    @staticmethod
    def _prefix_of(candidate: str, source_url: str) -> str:
        tail = "/" + (source_url or "")
        return candidate[: -len(tail)] if tail != "/" and candidate.endswith(tail) else candidate

    @staticmethod
    def _mark_probe_result(prefix: str, ok: bool, err: str) -> None:
        """成功或网络类失败才更新前缀健康；仓库 404 不影响前缀。"""
        if ok or is_transient_probe_error(err):
            mark_probe_health(prefix, ok)

    def _mirror_env(self) -> dict[str, str]:
        """镜像相关子进程的环境：只带网络设置与代理，禁用交互式凭据提示。

        镜像只在「无应用内凭据」时启用；这里再关掉终端提示，避免某些环境下
        git 因 401 卡在等待输入。
        """
        env = dict(os.environ)
        if self.proxy:
            env.setdefault("http_proxy", self.proxy)
            env.setdefault("https_proxy", self.proxy)
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_ASKPASS"] = "echo"
        # 清掉父进程继承的 GIT_CONFIG_KEY_n/VALUE_n（可能带认证头）。
        # 保留 GIT_CONFIG_GLOBAL / GIT_CONFIG_SYSTEM / GIT_CONFIG_NOSYSTEM ——
        # 它们可能承载企业代理与 CA 配置，误删会让镜像在代理环境里全挂。
        import re as _re
        for key in [k for k in env
                    if k == "GIT_CONFIG_COUNT"
                    or _re.fullmatch(r"GIT_CONFIG_(KEY|VALUE)_\d+", k)]:
            env.pop(key, None)
        env["GIT_CONFIG_COUNT"] = "0"
        return env

    def _global_git_auth_configured(self) -> bool:
        """全局/系统 git 配置是否带认证头（实现见 `mirror_probe`，测速路径共用）。"""
        return global_auth_header_configured()

    def _mirror_allowed(self, url: str) -> bool:
        """镜像是否可用于本次克隆（HTTPS + 有候选 + 无凭据）。

        **有凭据绝不走镜像是硬约束**：第三方镜像能看到请求头，token 会直接泄漏。
        应用内凭据（token/host_tokens）与全局 git 配置里的 `http.extraHeader`
        都算凭据。
        """
        if not (url or "").lower().startswith("https://"):
            return False
        if not self._mirror_candidates(url):
            return False
        if requires_credentials(
                getattr(self, "_current_host", ""), self.token, self.host_tokens):
            return False
        return not self._global_git_auth_configured()

    def _remember_mirror(self, repo_dir, prefix: str) -> None:
        """记录「这个仓库是用哪个镜像克隆的」，供后续增量更新优先使用。"""
        try:
            subprocess.run(
                ["git", "-C", str(repo_dir), "config", "gcm.mirror", prefix],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
        except Exception:
            pass

    def _get_mirror_hint(self, repo_dir) -> str:
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_dir), "config", "--get", "gcm.mirror"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace",
            )
            return (proc.stdout or "").strip() if proc.returncode == 0 else ""
        except Exception:
            return ""

    def _canonicalize_mirror_origin(self, repo_dir, source_url: str) -> None:
        """把历史/旧版本留下的「origin=镜像地址」恢复成源站。

        `origin` 必须是**源站**：否则本地扫描导入会把镜像 host 当成仓库来源，
        用户在本工具之外 `git pull` 也会走第三方。镜像只作为取数通道，
        用 `gcm.mirror` 配置记住。
        """
        try:
            current = self._get_origin_url(repo_dir)
        except Exception:
            return
        if not current or not source_url or current == source_url:
            return
        tail = "/" + source_url
        if not (current == source_url or current.endswith(tail)):
            return
        prefix = current[: -len(tail)] if current.endswith(tail) else ""
        if not prefix or not prefix.lower().startswith(("http://", "https://")):
            return
        # 即使该前缀已从配置里删掉（例如关了内置镜像开关），也要把 origin 恢复源站。
        if self._set_origin_url(repo_dir, source_url):
            self._remember_mirror(repo_dir, prefix)
            self._emit(f"已将 origin 规范化为源站（镜像记录为 {prefix}）")
        else:
            self._emit("[警告] 无法把 origin 从镜像恢复为源站，请检查仓库权限", "warn")

    def _mirror_fetch_url(self, source_url: str, repo_dir) -> str:
        """返回本次增量更新可优先使用的镜像 URL（无 hint / 不允许时为空）。"""
        if self.single_branch:
            # 单分支语义下固定 refspec 会把所有分支拉回来，直接走普通 fetch。
            return ""
        hint = self._get_mirror_hint(repo_dir)
        if not hint or not self._mirror_allowed(source_url):
            return ""
        prefix = hint.strip().rstrip("/")
        if not prefix.lower().startswith(("http://", "https://")):
            return ""
        # 用户可能已经关掉内置镜像/删掉该前缀：hint 必须仍在**当前**候选集合里。
        allowed = {self._prefix_of(c, source_url)
                   for c in self._mirror_candidates(source_url)}
        if prefix not in allowed:
            return ""
        return f"{prefix}/{source_url}"

    def _resolve_clone_url(self, source_url: str) -> str:
        """按优先级探测镜像，返回**实际用于克隆**的 URL（源站兜底）。

        探测用短超时 `git ls-remote`（见 `mirror_probe`）。这一步同时解决两件事：
        ① 镜像没这个仓库时立刻跳到下一个，不会卡到 clone 超时（默认 600s）；
        ② 顺便得到延迟，优先用上一个成功的镜像。
        """
        if not self._mirror_allowed(source_url):
            return source_url
        if self.cancelled():
            return source_url
        candidates = self._mirror_candidates(source_url)
        preferred = getattr(self, "_preferred_mirror", "") or preferred_prefix()
        if preferred:
            pref_url = f"{preferred}/{source_url}"
            if pref_url in candidates:
                candidates = [pref_url] + [c for c in candidates if c != pref_url]
        env = self._mirror_env()
        healthy: list[str] = []
        skipped: list[str] = []
        for cand in candidates:
            prefix = self._prefix_of(cand, source_url)
            if not probe_health(prefix):
                skipped.append(prefix)
                continue
            healthy.append(cand)
        if not healthy:
            if skipped:
                self._emit("[警告] 已知镜像均不可用，直接使用源站", "warn")
            return source_url
        for cand in healthy:
            prefix = self._prefix_of(cand, source_url)
            ok, ms, err = probe_git_url(cand, self.mirror_probe_timeout,
                                        cancelled=self.cancelled, env=env)
            self._mark_probe_result(prefix, ok, err)
            if self.cancelled():
                return source_url
            if ok:
                self._preferred_mirror = prefix
                self._emit(f"使用镜像（{ms}ms）：{prefix}")
                return cand
            self._emit(f"[警告] 镜像不可用，尝试下一个：{cand}（{err}）", "warn")
        self._emit("[警告] 国内镜像均不可用，回退源站（可能较慢）", "warn")
        return source_url

    def _set_origin_url(self, repo_dir, url: str) -> bool:
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_dir), "remote", "set-url", "origin", url],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            return proc.returncode == 0
        except Exception:
            return False

    def _get_origin_url(self, repo_dir) -> str:
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=15,
                encoding="utf-8", errors="replace",
            )
            return (proc.stdout or "").strip() if proc.returncode == 0 else ""
        except Exception:
            return ""

    @staticmethod
    def _origin_same_repo(origin: str, source_url: str) -> bool:
        """origin 是否与目标仓库同源（源站本身，或镜像前缀包着的源站）。

        只比 host 不够：同一 host 下的**另一个仓库**也会被误判为同源，
        回退时会把 origin 改成那个仓库的镜像地址。
        """
        o = (origin or "").strip()
        s = (source_url or "").strip()
        if not o or not s:
            return False
        if o == s:
            return True
        # 镜像地址形如 `<prefix>/<source_url>`：剥掉前缀后应按源站比较
        if o.endswith(s):
            return True
        try:
            from urllib.parse import urlparse
            po, ps = urlparse(o), urlparse(s)
            if (po.hostname or "").lower() != (ps.hostname or "").lower():
                return False

            def _norm(path: str) -> str:
                p = (path or "").strip().rstrip("/")
                return p[:-4] if p.endswith(".git") else p

            return _norm(po.path) == _norm(ps.path)
        except Exception:
            return False

    def _update_failover_candidates(self, source_url: str) -> list[str]:
        """更新失败时的候选：镜像（含预设）在前，源站最后。"""
        if not self.mirror_fallback:
            return []
        if not (source_url or "").lower().startswith("https://"):
            return []
        if requires_credentials(
                getattr(self, "_current_host", ""), self.token, self.host_tokens):
            return []
        if self._global_git_auth_configured():
            return []
        out: list[str] = []
        seen: set[str] = set()
        for url in self._mirror_candidates(source_url) + [source_url]:
            if url and url not in seen:
                seen.add(url)
                out.append(url)
        return out

    def _precheck_reachable(self, url: str) -> tuple[bool, str]:
        """G38-3 远端可达性预检：git ls-remote --exit-code <url> HEAD（10s 超时）。

        返回 (可达, 错误文本)。任何异常/非 0 退出码 → 不可达。
        """
        import subprocess as _sp
        try:
            proc = _sp.run(
                ["git", "ls-remote", "--exit-code", url, "HEAD"],
                capture_output=True, text=True, timeout=10,
                encoding="utf-8", errors="replace", env=self._env(),
            )
            if proc.returncode == 0:
                return True, ""
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()
            return False, (tail[-1] if tail else f"git ls-remote 退出码 {proc.returncode}")
        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------ 工具
    def _safe_rmtree_partial(self, repo_dir: Path) -> None:
        """只清理「本工具产生的半成品克隆」目录（含 .git 或为空）。

        绝不删非空且无 .git 的目录——那可能是用户资料或他人正在写入的成果。
        """
        try:
            if not repo_dir.exists():
                return
            is_partial_git = (repo_dir / ".git").exists()
            is_empty = not any(repo_dir.iterdir())
            if is_partial_git or is_empty:
                shutil.rmtree(repo_dir, ignore_errors=True)
            else:
                # 非空且非 git 目录：保守起见不动，仅记录
                self._emit(f"[警告] 目录非空且非 git 仓库，保留不清理：{repo_dir.name}", "warn")
        except Exception:
            pass

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
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=20,
            )
            return r.returncode == 0 and r.stdout.strip() == "true"
        except Exception:
            return False

    def _emit(self, text: str, level: str = "info"):
        # G28-1：出口统一打码，token/授权头不会进入 UI 或日志
        from ..util.redact import redact
        self.on_line(StreamChunk(index=0, text=redact(text), level=level))

    def _local_branch(self, repo_dir: Path) -> str:
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_dir), "symbolic-ref", "--short", "HEAD"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=20,
            )
            if r.returncode == 0:
                return r.stdout.strip()
        except Exception:
            pass
        # detached HEAD（如 tag 检出）：仓库有效但无分支 → 返回空串。
        # 目录不存在/git 失败 → 回退默认分支名（保持既有兼容语义）。
        try:
            if (repo_dir / ".git").exists() or repo_dir.exists():
                return ""
        except Exception:
            pass
        return "main"

    def _is_detached(self, repo_dir: Path) -> bool:
        """仓库是否处于 detached HEAD（tag 检出的典型状态）。"""
        return self._local_branch(repo_dir) == ""

    # ------------------------------------------------------------ 主入口
    def sync(self, spec: RepoSpec) -> SyncResult:
        """统一入口：存在则增量更新，不存在则克隆。"""
        res = SyncResult(spec=spec, status=SyncStatus.RUNNING)
        repo_dir = self._repo_dir(spec)
        res.path = str(repo_dir)
        res.started = time.time()
        # F1 安全加固：按 spec 的 host 记录，供 _env 决定是否注入 token
        try:
            from ..app.url_lib import host_of
            self._current_host = host_of(spec.url_https) or ""
            self._current_url = spec.url_https or ""
        except Exception:
            self._current_host = ""
            self._current_url = ""

        self._precheck_failed = False
        # G38-3 远端可达性预检（可选开启）：断网/坏 host 时 10s 内明确失败，
        # 不必等满 clone/fetch 超时。仅对含远端 URL 的仓库执行。
        if self.precheck_remote and not spec.is_local and spec.url_https:
            ok, err = self._precheck_reachable(spec.url_https)
            if not ok:
                # G69-21：源站不可达正是镜像的用武之地。此前这里直接失败，
                # 导致「开了预检就永远用不上镜像」—— 两个功能互斥。
                if self._mirror_allowed(spec.url_https):
                    self._precheck_failed = True
                    self._emit("[警告] 源站预检不可达，改用国内镜像尝试 …", "warn")
                else:
                    res.status = SyncStatus.FAILED
                    res.action = SyncAction.FAILED
                    res.message = "远端不可达（10s 预检），检查网络/代理"
                    res.detail = err
                    res.ended = time.time()
                    res.duration_ms = int((res.ended - res.started) * 1000)
                    return res

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
                # G69-1 修复（数据销毁，实测可达）：原为**无条件**裸 `shutil.rmtree(repo_dir)`。
                #
                # 危险面在于**路径来源**：`_repo_dir(spec)` 在 `spec.local_path` 非空时
                # 直接返回它，而 `local_path` 可由**导入的元数据 JSON** 提供
                # （`repo_db.import_metadata_json`，url 非空即视为非 local）。于是
                # 「导入别人分享的元数据 → 点一键更新」能删除任意目录
                # —— 审计实测：local_path 指向含资料的目录，同步后整棵消失。
                #
                # 但**不能改成「一律不删」**：`tests/test_core.py::test_broken_dir_recloned`
                # 钉住了「克隆目标位上的坏目录要清掉重克隆」这一正式契约。
                # 两者的区别正是路径来源，所以按来源分治：
                #   - local_path 为空 → 路径是我们自己的 `root/folder_name`，维持原行为
                #   - local_path 非空 → 是用户/外部数据，**绝不删除**；只清「含 .git 或为空」
                #     的半成品，其余保留并如实报失败
                if not spec.local_path:
                    try:
                        shutil.rmtree(repo_dir)
                    except Exception as e:
                        res.status = SyncStatus.FAILED
                        res.action = SyncAction.FAILED
                        res.message = "无法清理残留目录"
                        res.detail = str(e)
                        res.ended = time.time()
                        return res
                else:
                    self._safe_rmtree_partial(repo_dir)
                    if repo_dir.exists() and not (repo_dir / ".git").exists():
                        res.status = SyncStatus.FAILED
                        res.action = SyncAction.FAILED
                        res.message = "目录已存在且非本工具克隆（已保留不清理）"
                        res.detail = (f"{repo_dir} 非空且不是 git 仓库，"
                                      "已跳过删除以免误删用户数据")
                        res.ended = time.time()
                        return res
            return self._clone(spec, res)
        finally:
            # 统一兜底：任何路径都必须有结束时间，否则 duration 会为 0
            if res.ended == 0.0:
                res.ended = time.time()
            res.duration_ms = int((res.ended - res.started) * 1000)

    # ------------------------------------------------------------ clone
    def build_clone_cmd(self, spec: RepoSpec, repo_dir=None,
                        url_override: Optional[str] = None) -> list[str]:
        """G48-2/6：拼装 clone 命令（含 LFS -c 选项与 --mirror）。

        独立方法便于单测直接断言命令内容，无需跑真实 git。
        `url_override` 供 G58-2 镜像备援传入实际选中的 URL。
        """
        repo_dir = repo_dir or self._repo_dir(spec)
        cmd = ["git"] + self._lfs_flag() + ["clone", "--progress"]
        if getattr(spec, "ref", ""):
            cmd += ["-b", spec.ref]
        if self.fetch_depth:
            # G66-10 修复：--depth 原先只在 single_branch 同时为真时才追加，
            # 导致 UI 选「浅克隆」但设置页未勾 single_branch 时退化为全量克隆
            # （实测 depth=1, single_branch=False → 命令里没有 --depth）。
            # 语义修正：depth 独立生效，single_branch 只决定是否附加 --single-branch。
            cmd.append(f"--depth={self.fetch_depth}")
            if self.single_branch:
                cmd.append("--single-branch")
        if self._mirror:
            # G48-6 镜像克隆（--mirror 自带 --bare）
            cmd.append("--mirror")
        cmd += [url_override or self._mirror_url(spec.url_https), str(repo_dir)]
        if self._submodule:
            cmd.append("--recurse-submodules")
        return cmd

    def _build_post_clone_hook_cmd(self, repo_dir) -> str | None:
        """把钩子模板里的 `%PATH%` 替换为**已加引号**的仓库路径。

        返回 None 表示无法安全拼装，调用方应跳过执行（并提示原因）。

        独立方法便于单测直接断言命令内容，无需真的跑 hooks。

        G67-1 / G67-3 安全要点（两次踩坑，都很值得记）：

        1) **为什么必须加引号**：钩子文本是用户写的（可信），但替换进去的路径来自
           URL（不可信）—— `url_lib.sanitize_name` 只过滤 `\\/:*?"<>|` 与控制字符，
           **不过滤 `& ; $ ( ) ^ ! %`**。实测目录名 `x&mkdir PWNED&y` 未加引号时
           真的执行了 `mkdir`。cmd.exe 在双引号内不再解释 `& | > < ^`，所以加引号可挡。

        2) **为什么归一化要用正则而不是精确字符串替换**（G67-3 修复的绕过）：
           只归一化完整的 `"%PATH%"` 会漏掉**占位符带后缀**这一自然写法
           `"%PATH%\\marker.txt"` → 生成 `""<path>"\\marker.txt"`，两个相邻引号使
           引号状态翻转，路径落到引号外，`&` 重新被当成命令分隔符
           （实测该模板下注入 `x&copy NUL PWNED_Q.txt&y` **成功**）。
           正则 `["\\']?%PATH%["\\']?` 会把占位符两侧的引号一起剥掉，再由我们统一加引号。

        3) **路径本身还必须不含 `"` `%` `!`**：`"` 无法转义；`%VAR%` 在引号内
           **仍会展开**（cmd 先展开再解析），`!` 在启用延迟展开时同理 ——
           两者都可能让展开值里的 `"` 突破引号。命中即拒绝执行钩子，不冒险拼接。

        4) **按引号上下文决定是否加引号**（而不是一律加、也不是一律剥）：
           逐个出现位置统计「它前面有几个未闭合的 `"`」——
           奇数说明模板自己已经把它包在引号里，此时**原样替换**（让模板的引号生效，
           且 `"%PATH%\\marker.txt"` 会自然得到 `"<path>\\marker.txt"`，正是用户意图）；
           偶数说明它在引号外，此时**由我们加引号**。
           前两版都错在这里：一律加引号会产生 `""<path>"\\marker.txt"`（引号状态翻转，
           可被注入）或 `"<path>"\\marker.txt"`（引号不均衡，钩子实际失效）。
        """
        from pathlib import Path as _P

        raw = self._post_clone_hook
        path = str(_P(repo_dir))
        if any(ch in path for ch in ('"', "%", "!")):
            return None
        token = "%PATH%"
        out: list[str] = []
        cursor = 0
        while True:
            hit = raw.find(token, cursor)
            if hit < 0:
                out.append(raw[cursor:])
                break
            out.append(raw[cursor:hit])
            inside_quotes = (raw[:hit].count('"') % 2) == 1
            out.append(path if inside_quotes else f'"{path}"')
            cursor = hit + len(token)
        return "".join(out)

    def _run_post_clone_hook(self, repo_dir) -> None:
        """G48-5：成功克隆后运行用户钩子命令（%PATH% 替换为仓库路径；失败仅日志）。

        G67-1 安全修复（命令注入）：
        钩子命令文本是**用户自己写的**（可信），但替换进去的**仓库路径来自 URL**
        （不可信）—— `url_lib.sanitize_name` 只过滤 `\\/:*?"<>|` 与控制字符，
        **不过滤 `& ; $ ( ) ^ ! %` 等 cmd.exe 元字符**。而本工具最典型的用法正是
        「粘贴别人的仓库清单批量克隆」。

        实测注入（修复前）：目录名 `x&echo INJECTED-CODE-EXECUTED&y` 会让 cmd.exe 把
        `echo INJECTED-CODE-EXECUTED` 当成独立命令执行（输出里实际出现了该串）；
        修复后同一输入被双引号完整保护（输出为空，未执行）。

        保留 `shell=True` 是**有意的**：用户的钩子本就可能是重定向/管道（如
        `echo done > log.txt`），改成 argv 数组会破坏既有配置。
        """
        if not self._post_clone_hook:
            return
        import subprocess as _sp

        cmd = self._build_post_clone_hook_cmd(repo_dir)
        if cmd is None:
            # 路径含 `"` / `%` / `!` —— 无法安全转义（见 _build_post_clone_hook_cmd
            # 的说明），拒绝执行而不是冒险拼接。sanitize_name 已过滤 `"`，
            # 所以正常路径不会走到这里。
            self._emit(
                "克隆后钩子已跳过：仓库路径含无法安全转义的特殊字符"
                "（\" / % / !），跳过执行以避免命令注入", "warn")
            return
        try:
            self._emit(f"执行克隆后钩子：{cmd}")
            _sp.run(cmd, shell=True, cwd=str(self.root), capture_output=True,
                    text=True, encoding="utf-8", errors="replace", timeout=60)
        except Exception as e:
            self._emit(f"克隆后钩子执行失败（仅记录）：{e}", "warn")

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

        source_url = spec.url_https
        clone_url = self._resolve_clone_url(source_url)
        if getattr(self, "_precheck_failed", False) and clone_url == source_url:
            res.status = SyncStatus.FAILED
            res.action = SyncAction.FAILED
            res.message = "源站不可达（10s 预检）且镜像不可用"
            res.detail = "已跳过长时间 clone 超时；请检查网络/代理或稍后重试"
            return res
        cmd = self.build_clone_cmd(spec, repo_dir, url_override=clone_url)
        if clone_url != source_url:
            # G69-21：镜像命令显式清空 http.extraHeader，防止仓库级/全局认证头外泄
            cmd = [cmd[0], "-c", "http.extraHeader="] + cmd[1:]
        rc, prog = run_git_ui(cmd, str(self.root), _collate,
                              cancelled=self.cancelled, env=self._env(),
                              timeout=self.clone_timeout,
                              # G69-21：镜像首跳**不重试**。第三方镜像在传 pack 时
                              # 卡住很常见，若沿用 retries=2×600s，最坏 30 分钟才回源。
                              retries=(0 if clone_url != source_url else self.retries),
                              backoff=self._backoff,
                              progress_detail=getattr(
                                  self, "send_progress_detail", None))
        # G58-2：镜像克隆失败 → 回退源站再试一次（探测通过也可能在 clone 阶段失败）
        if (rc != 0 and not self.cancelled() and self.mirror_fallback
                and clone_url != source_url):
            self._emit(f"{spec.display} 镜像克隆失败，回退源站重试 …", "warn")
            self._safe_rmtree_partial(repo_dir)
            self._last_clone_tail = []
            clone_url = source_url
            cmd = self.build_clone_cmd(spec, repo_dir, url_override=source_url)
            rc, prog = run_git_ui(cmd, str(self.root), _collate,
                                  cancelled=self.cancelled, env=self._env(),
                                  timeout=self.clone_timeout, retries=self.retries,
                                  backoff=self._backoff,
                                  progress_detail=getattr(
                                      self, "send_progress_detail", None))
        if self.cancelled():
            res.status = SyncStatus.CANCELLED
            res.action = SyncAction.CANCELLED
            res.message = "已取消"
            return res
        # G69-21：镜像克隆成功后立即把 origin 恢复为源站（必须在子模块更新之前，
        # 否则相对子模块 URL 会按镜像 origin 解析）。
        if rc == 0 and clone_url != source_url:
            prefix = self._prefix_of(clone_url, source_url)
            if self._set_origin_url(repo_dir, source_url):
                self._remember_mirror(repo_dir, prefix)
            else:
                self._emit("[警告] 无法把 origin 恢复为源站，后续更新可能仍走镜像", "warn")
        if self._submodule and rc == 0:
            # G08-1 拉取子模块（clone --recurse-submodules 之外的补充 update --init）
            self._emit(f"拉取子模块 {spec.display} …")
            sm_rc, _ = run_git_ui(
                ["git", "-C", str(repo_dir), "submodule", "update", "--init", "--recursive"],
                str(self.root), self.on_line, cancelled=self.cancelled, env=self._env(),
                timeout=self.clone_timeout, retries=self.retries, backoff=self._backoff,
            )
            if self.cancelled():
                res.status = SyncStatus.CANCELLED
                res.action = SyncAction.CANCELLED
                res.message = "已取消"
                return res
            if sm_rc != 0:
                self._emit(f"[警告] 子模块拉取失败（不影响主仓库）：{spec.display}", "warn")
        if rc != 0 and not self.cancelled():
            # G04-1 弱网降级：网络类失败 → 尝试 treeless 部分克隆（--filter=blob:none）
            # 显著减少传输量提升弱网成功率；成功则继续，失败则按原逻辑报错。
            tail0 = "\n".join(self._last_clone_tail[-8:])
            if _is_networkish_error(tail0):
                self._emit(f"{spec.display} 常规克隆失败，尝试 treeless 部分克隆"
                           "（弱网降级）…", "warn")
                self._safe_rmtree_partial(repo_dir)
                _is_mirror = clone_url != source_url
                treeless_cmd = ["git"]
                if _is_mirror:
                    treeless_cmd += ["-c", "http.extraHeader="]
                treeless_cmd += ["clone", "--progress", "--filter=blob:none"]
                if getattr(spec, "ref", ""):
                    treeless_cmd += ["-b", spec.ref]
                treeless_cmd += [clone_url, str(repo_dir)]
                rc, prog = run_git_ui(
                    treeless_cmd, str(self.root), _collate,
                    cancelled=self.cancelled,
                    env=(self._mirror_env() if _is_mirror else self._env()),
                                      timeout=self.clone_timeout, retries=0,
                                      backoff=self._backoff,
                                      progress_detail=getattr(self, "send_progress_detail", None))
                # G38-5 降级链终档：treeless 仍失败 → depth1 单分支「保命模式」
                if rc != 0 and not self.cancelled():
                    self._emit(f"{spec.display} treeless 仍失败，尝试 depth1 单分支"
                               "保命模式（完整历史未拉取）…", "warn")
                    self._safe_rmtree_partial(repo_dir)
                    shallow_cmd = ["git"]
                    if clone_url != source_url:
                        shallow_cmd += ["-c", "http.extraHeader="]
                    shallow_cmd += ["clone", "--progress",
                                    "--depth=1", "--single-branch"]
                    if getattr(spec, "ref", ""):
                        shallow_cmd += ["-b", spec.ref]
                    shallow_cmd += [clone_url, str(repo_dir)]
                    rc, prog = run_git_ui(
                        shallow_cmd, str(self.root), _collate,
                        cancelled=self.cancelled,
                        env=(self._mirror_env() if clone_url != source_url else self._env()),
                        timeout=self.clone_timeout, retries=0,
                        backoff=self._backoff,
                        progress_detail=getattr(self, "send_progress_detail", None))
                    if rc == 0:
                        self._emit("[提示] 已用 depth1 单分支克隆，完整历史未拉取；"
                                   "可用设置→浅克隆更新（unshallow）补全。", "warn")
        if rc != 0:
            # 克隆失败：分类给出人类可读提示（平台限制 / 目录占用 / 通用）
            tail = "\n".join(self._last_clone_tail[-8:])
            msg, detail = _classify_failure(tail)
            # 平台限制 / 目录占用类：保留目录（可能是他人成果或用户资料），不清理
            if ("Windows 不允许" in msg or "无法检出" in msg
                    or "目录已存在" in msg):
                self._emit(f"[失败] {spec.display}：{msg}", "warn")
            else:
                self._emit(f"克隆失败，清理残留目录 {repo_dir.name}", "warn")
                self._safe_rmtree_partial(repo_dir)
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
        # G48-5 克隆后钩子
        try:
            self._run_post_clone_hook(repo_dir)
        except Exception:
            pass
        res.action = SyncAction.CLONED
        res.message = "克隆完成"
        res.commits = 0
        return res

    # ------------------------------------------------------------ update
    def _update(self, spec: RepoSpec, res: SyncResult) -> SyncResult:
        repo_dir = self._repo_dir(spec)
        # G69-21：旧版本/旧会话可能把 origin 留在了镜像地址上。更新前先规范化回源站，
        # 否则本地扫描导入会把镜像 host 当成来源，且全局配置里的认证头可能被发给镜像。
        self._canonicalize_mirror_origin(repo_dir, spec.url_https)
        before = self._head_sha(repo_dir)
        res.head_sha = before
        # 空仓库（无头）已存在：hit 导入的空仓库，跳过 fetch 直接标记 empty
        if not before:
            res.status = SyncStatus.SUCCESS
            res.action = SyncAction.EMPTY
            res.message = "空仓库（暂无提交）"
            return res

        # 1) fetch：浅克隆仓库需显式 depth 语义，全量仓库保持原样（G48-2 LFS 追加 -c）
        self._emit(f"检查 {spec.display} 远端更新 …")
        fetch_cmd = ["git"] + self._lfs_flag() + ["fetch", "--progress", "--prune", "origin"]
        if self._mirror:
            return self._update_bare_mirror(spec, repo_dir, fetch_cmd, res)
        used_unshallow = False
        if self.unshallow and self._is_shallow_repo(repo_dir):
            fetch_cmd = ["git", "fetch", "--progress", "--prune", "--unshallow", "origin"]
            used_unshallow = True
            self._emit(f"{spec.display} 为浅克隆仓库，正在拉取全量历史（--unshallow）…")
        elif self.fetch_depth and self._is_shallow_repo(repo_dir):
            # G67-3 修复（重要回归）：**不要在浅仓上重复 `fetch --depth=N`**。
            #
            # 原实现在浅仓上发 `fetch --depth=N origin`，这会让新取到的 tip 成为
            # 「无父节点」的新 shallow 边界 → `git merge-base HEAD origin/main` 无解 →
            # `fatal: refusing to merge unrelated histories` → 被下方失败分类误判成
            # 「合并/rebase 冲突」，于是仓库**从第二次同步起永久无法更新且 HEAD 永不前进**，
            # 而用户看到的是「冲突」假象（本地其实毫无改动）。
            #
            # 实测规律：只有 `fetch_depth ≥ 1 + 远端推进数` 才侥幸成功，所以 UI 默认的
            # `depth=1` **必然每次失败** —— 属结构性缺陷而非特例。实测对照（本地裸仓）：
            #   depth=1, 远端+1 → conflict, HEAD 不前进
            #   depth=1, 远端+2 → conflict, HEAD 不前进
            #   depth=2, 远端+2 → success（但那时仓库已不是浅仓）
            # 改为 plain fetch：git 会尊重既有 shallow 边界、只取新增提交，
            # 仓库保持浅的，且 ff 正常。（`--deepen=N` / `--update-shallow` 实测也可行，
            # 但 plain fetch 语义最简、不会随时间无界加深。）
            fetch_cmd = ["git"] + self._lfs_flag() + [
                "fetch", "--progress", "--prune", "origin"]
            self._emit(f"{spec.display} 为浅克隆仓库：按浅边界增量 fetch（不带 --depth）")
        # G58-2/G69-21：fetch + 镜像回退（G71-7 抽成独立方法，行为零变化）
        if not self._fetch_remote(spec, repo_dir, fetch_cmd, used_unshallow, res):
            return res

        # 2) 计算可以快进的提交数
        branch = self._local_branch(repo_dir)
        # F6：detached HEAD（如指定 tag 检出）→ 不参与 ff/rebase，仅 fetch 保持新鲜
        if not branch:
            res.status = SyncStatus.SUCCESS
            res.action = SyncAction.FETCHED
            res.message = "已按标签/提交固定检出（detached HEAD），仅 fetch 检查"
            res.head_sha = self._head_sha(repo_dir)
            res.remote_sha = self._remote_head(repo_dir)
            res.commits = 0
            return res
        remote_ref = "origin/" + branch
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

        # 4) merge --ff-only（或 rebase）—— G71-7 抽成独立方法
        early = self._merge_remote(spec, repo_dir, commits_new, res)
        if early is not None:
            return early

        after = self._head_sha(repo_dir)
        res.head_sha = after
        res.remote_sha = self._remote_head(repo_dir)
        res.commits = max(0, commits_new)
        res.status = SyncStatus.SUCCESS
        if commits_new > 0:
            res.action = SyncAction.UPDATED
            res.message = f"更新完成（+{commits_new} 提交）"
        elif commits_new == 0:
            res.action = SyncAction.FETCHED
            res.message = "已是最新"
        else:
            # G67-11：无法判定时不能宣称「已是最新」——用 FETCHED + 明确措辞。
            advanced = after != before
            res.action = SyncAction.UPDATED if advanced else SyncAction.FETCHED
            res.message = ("已同步（提交数无法判定，HEAD 已推进）" if advanced
                           else "已获取远端（提交数无法判定，请复核远端分支）")
        return res

    def _update_bare_mirror(self, spec: RepoSpec, repo_dir: Path,
                            fetch_cmd: list[str], res: SyncResult) -> SyncResult:
        """G48-6 镜像仓库（bare）更新：仅 fetch refs，无工作树不 merge。

        G69-21：bare 仓库同样要有镜像回退（此前这个分支直接 return，
        README 的「更新失败自动切换」对 --mirror 仓库不成立）。
        """
        mr_rc, _ = run_git_ui(
            fetch_cmd, str(repo_dir), self.on_line, cancelled=self.cancelled,
            env=self._env(), timeout=self.fetch_timeout, retries=self.retries,
            backoff=self._backoff)
        if mr_rc != 0 and self.mirror_fallback and not self.cancelled():
            original = self._get_origin_url(repo_dir)
            if original and not self._origin_same_repo(original, spec.url_https):
                # 与普通分支一致：origin 指向别的仓库时不做猜测性改写
                self._emit("[警告] origin 与目标仓库不一致，跳过镜像回退", "warn")
            else:
                switched = False
                for alt in self._update_failover_candidates(spec.url_https):
                    prefix = self._prefix_of(alt, spec.url_https)
                    if alt != spec.url_https:
                        if not probe_health(prefix):
                            continue
                        ok, _ms, err = probe_git_url(
                            alt, self.mirror_probe_timeout,
                            cancelled=self.cancelled, env=self._mirror_env())
                        self._mark_probe_result(prefix, ok, err)
                        if not ok:
                            self._emit(f"[警告] 备用地址不可用，跳过：{alt}（{err}）", "warn")
                            continue
                    if not self._set_origin_url(repo_dir, alt):
                        continue
                    self._emit(f"{spec.display} 镜像 fetch 失败，切换到 {alt} 重试 …", "warn")
                    alt_cmd = fetch_cmd
                    if alt != spec.url_https:
                        alt_cmd = [fetch_cmd[0], "-c", "http.extraHeader="] + fetch_cmd[1:]
                    mr_rc, _ = run_git_ui(
                        alt_cmd, str(repo_dir), self.on_line,
                        cancelled=self.cancelled,
                        env=(self._mirror_env() if alt != spec.url_https else self._env()),
                        timeout=self.fetch_timeout, retries=0,
                        backoff=self._backoff)
                    if mr_rc == 0:
                        if alt != spec.url_https:
                            self._remember_mirror(repo_dir, prefix)
                        switched = True
                        break
                # 只有真的切换过才把 origin 恢复为源站；否则原样保留
                if switched:
                    self._set_origin_url(repo_dir, spec.url_https)
                elif original:
                    self._set_origin_url(repo_dir, original)
        if mr_rc == 0:
            res.status = SyncStatus.SUCCESS
            res.action = SyncAction.FETCHED
            res.message = "镜像已同步远端 refs"
        else:
            res.status = SyncStatus.FAILED
            res.action = SyncAction.FAILED
            res.message = "镜像 remote fetch 失败"
        return res

    def _fetch_remote(self, spec: RepoSpec, repo_dir: Path,
                      fetch_cmd: list[str], used_unshallow: bool,
                      res: SyncResult) -> bool:
        """执行 fetch（镜像优先 + 源站回退）；返回 False 表示 res 已是终态。

        G58-2/G69-21 收尾要求（逐条保留）：
        - 有「上次可用镜像」时用显式 URL + refspec fetch，不改动 origin；
        - hint 命中也要先看健康状态；未知则短超时探测，避免挂掉的镜像白等 180s；
        - 普通 fetch 失败 → 依次切换镜像/源站重试；
        - 候选尝试 retries=0；成功后把 origin 恢复为**源站**，镜像只记在 gcm.mirror；
        - unshallow 意图在回退成功后重新执行；
        - 恢复失败/无原 origin 时明确告警，绝不留下失败镜像。
        """
        rc = 1
        mirror_url = self._mirror_fetch_url(spec.url_https, repo_dir)
        if mirror_url:
            prefix = self._prefix_of(mirror_url, spec.url_https)
            state = probe_state(prefix)
            if state is False:
                mirror_url = ""
            elif state is None:
                ok, _ms, err = probe_git_url(
                    mirror_url, self.mirror_probe_timeout,
                    cancelled=self.cancelled, env=self._mirror_env())
                self._mark_probe_result(prefix, ok, err)
                if not ok:
                    self._emit(f"[警告] 镜像探测失败，回退源站：{err}", "warn")
                    mirror_url = ""
        if mirror_url:
            mirror_cmd = ["git", "-c", "http.extraHeader="] + [
                c for c in fetch_cmd if c != "origin" and c != "git"] + [
                mirror_url, "+refs/heads/*:refs/remotes/origin/*"]
            rc, _ = run_git_ui(
                mirror_cmd, str(repo_dir), self.on_line, cancelled=self.cancelled,
                env=self._mirror_env(), timeout=min(self.fetch_timeout, 180.0),
                retries=0, backoff=self._backoff,
                progress_detail=getattr(self, "send_progress_detail", None),
            )
            if rc == 0:
                self._emit(f"{spec.display} 通过镜像增量同步")
            else:
                self._emit("[警告] 镜像 fetch 失败，回退源站重试 …", "warn")
        if not mirror_url or rc != 0:
            rc, _ = run_git_ui(
                fetch_cmd,
                str(repo_dir), self.on_line, cancelled=self.cancelled, env=self._env(),
                timeout=self.fetch_timeout, retries=self.retries, backoff=self._backoff,
                progress_detail=getattr(self, "send_progress_detail", None),
            )
        # unshallow/带深度 fetch 失败（远端行为异常）时降级回普通 fetch，避免把仓库标红
        if (rc != 0 and (used_unshallow or "--depth=" in " ".join(fetch_cmd))
                and not self.cancelled()):
            self._emit(f"{spec.display} 浅层 fetch 失败，降级为普通 fetch …", "warn")
            fetch_cmd = ["git", "fetch", "--progress", "--prune", "origin"]
            rc, _ = run_git_ui(
                fetch_cmd,
                str(repo_dir), self.on_line, cancelled=self.cancelled, env=self._env(),
                timeout=self.fetch_timeout, retries=self.retries, backoff=self._backoff,
                progress_detail=getattr(self, "send_progress_detail", None),
            )
        if self.cancelled():
            res.status = SyncStatus.CANCELLED
            res.action = SyncAction.CANCELLED
            res.message = "已取消"
            return False
        if rc != 0 and self.mirror_fallback:
            original = self._get_origin_url(repo_dir)
            if original and not self._origin_same_repo(original, spec.url_https):
                self._emit("[警告] origin 与目标仓库不一致，跳过镜像回退", "warn")
            else:
                candidates = self._update_failover_candidates(spec.url_https)
                succeeded = False
                used_alt = ""
                for alt in candidates:
                    if self.cancelled():
                        break
                    if original and alt == original:
                        continue
                    prefix = self._prefix_of(alt, spec.url_https)
                    if alt != spec.url_https:
                        if not probe_health(prefix):
                            continue
                        ok, _ms, err = probe_git_url(
                            alt, self.mirror_probe_timeout,
                            cancelled=self.cancelled, env=self._mirror_env())
                        self._mark_probe_result(prefix, ok, err)
                        if not ok:
                            self._emit(f"[警告] 备用地址不可用，跳过：{alt}（{err}）", "warn")
                            continue
                    if not self._set_origin_url(repo_dir, alt):
                        continue
                    self._emit(f"{spec.display} fetch 失败，切换到 {alt} 重试 …", "warn")
                    alt_env = self._mirror_env() if alt != spec.url_https else self._env()
                    alt_cmd = fetch_cmd
                    if alt != spec.url_https:
                        alt_cmd = [fetch_cmd[0], "-c", "http.extraHeader="] + fetch_cmd[1:]
                    rc, _ = run_git_ui(
                        alt_cmd,
                        str(repo_dir), self.on_line, cancelled=self.cancelled,
                        env=alt_env, timeout=self.fetch_timeout,
                        retries=0, backoff=self._backoff,
                        progress_detail=getattr(self, "send_progress_detail", None),
                    )
                    if rc == 0:
                        succeeded = True
                        used_alt = alt
                        self._emit(f"{spec.display} 已切换并同步成功：{alt}")
                        break
                # 无论成功失败，origin 都恢复为源站（成功时记下镜像，供下次优先使用）
                restored = self._set_origin_url(repo_dir, spec.url_https)
                if not restored:
                    self._emit("[警告] 无法把 origin 恢复为源站，请检查仓库配置", "warn")
                if succeeded:
                    if used_alt != spec.url_https:
                        self._remember_mirror(
                            repo_dir, self._prefix_of(used_alt, spec.url_https))
                        # unshallow 意图：回退 fetch 用的是同一个 fetch_cmd，
                        # 但若中途降级过普通 fetch，这里补一次真正的 --unshallow。
                        if used_unshallow and self._is_shallow_repo(repo_dir):
                            urc, _ = run_git_ui(
                                ["git", "fetch", "--unshallow", "origin"],
                                str(repo_dir), self.on_line,
                                cancelled=self.cancelled, env=self._env(),
                                timeout=self.fetch_timeout, retries=0,
                                backoff=self._backoff,
                            )
                            if urc != 0:
                                self._emit("[警告] 回退后拉全量历史失败，仓库仍为浅克隆", "warn")
                    else:
                        self._emit(f"{spec.display} 已回退源站并同步成功")
                elif not restored:
                    self._emit("[警告] 镜像回退全部失败且 origin 未能恢复", "warn")
        if self.cancelled():
            res.status = SyncStatus.CANCELLED
            res.action = SyncAction.CANCELLED
            res.message = "已取消"
            return False
        if rc != 0:
            res.status = SyncStatus.FAILED
            res.action = SyncAction.FAILED
            res.message = "fetch 失败"
            res.detail = f"git fetch 退出码 {rc}"
            return False
        return True

    def _merge_remote(self, spec: RepoSpec, repo_dir: Path, commits_new: int,
                      res: SyncResult) -> Optional[SyncResult]:
        """按 commits_new 执行 ff-merge / rebase；冲突时返回終態 res，否则 None。"""
        remote_ref = "origin/" + self._local_branch(repo_dir)
        if commits_new < 0:
            # G67-11：无法判定落后数（远端 ref 不可解析等）→ **不得宣称「已是最新」**。
            # 仍尝试一次 ff 合并（若确实落后则推进；若真是最新则无副作用），
            # 但如实告知状态待复核，避免「长期落后却永远绿灯」。
            self._emit(
                f"{spec.display} 无法判定远端新提交数（{remote_ref} 不可解析），"
                "已尝试同步，请复核远端分支是否被重命名/删除", "warn")
            run_git_ui(["git", "merge", "--ff-only", "@{upstream}"],
                       str(repo_dir), self.on_line, cancelled=self.cancelled, timeout=120,
                       progress_detail=getattr(self, "send_progress_detail", None))
        elif commits_new > 0:
            self._emit(f"{spec.display} 有 {commits_new} 个新提交，执行快进合并 …")
            rc, _ = run_git_ui(
                ["git", "merge", "--ff-only", "@{upstream}"],
                str(repo_dir), self.on_line, cancelled=self.cancelled, timeout=120,
                progress_detail=getattr(self, "send_progress_detail", None),
            )
            if rc != 0:
                # 无法快进（分叉），尝试 rebase
                self._emit("快进合并失败，尝试 rebase …", "warn")
                rc, _ = run_git_ui(
                    ["git", "rebase", "@{upstream}"],
                    str(repo_dir), self.on_line, cancelled=self.cancelled, timeout=120,
                    progress_detail=getattr(self, "send_progress_detail", None),
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
        return None

    # ------------------------------------------------------------ helpers
    def _head_sha(self, repo_dir: Path) -> str:
        try:
            r = subprocess.run(["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=20)
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _remote_head(self, repo_dir: Path) -> str:
        branch = self._local_branch(repo_dir)
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-parse", f"origin/{branch}"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=20,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _count_new_commits(self, repo_dir: Path, remote_ref: str) -> int:
        """HEAD 落后 remote_ref 的提交数。

        G67-11 修复：**失败返回 -1（无法判定）而不是 0**。
        原实现把「rev-list 失败」与「确实是最新」混为一谈 —— 远端分支被重命名/删除、
        `origin/<branch>` 不存在、git 报错等情况下都返回 0，于是调用方走
        「已是最新」SUCCESS 分支，**仓库长期落后却永远显示绿色**。
        返回 -1 让调用方能把「未知」与「零」区分开，不再假报成功。
        """
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_dir), "rev-list", "--count", f"HEAD..{remote_ref}"],
                capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
            if r.returncode == 0:
                txt = (r.stdout or "").strip()
                if txt.isdigit():
                    return int(txt)
            return -1
        except Exception:
            return -1

    def current_branch(self, repo_dir: Path) -> str:
        return self._local_branch(repo_dir)
