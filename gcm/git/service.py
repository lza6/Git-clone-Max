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
            capture_output=True, text=True, timeout=30,
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
                capture_output=True, text=True, timeout=30,
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
                 mirror_prefix: Optional[dict[str, str]] = None,
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
        self.mirror_prefix: dict[str, str] = dict(mirror_prefix or {})  # G38-2
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
          `http.extraHeader=Authorization: Bearer <token>`（纯 git 支持、免临时脚本，
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
        if host in ("", "github.com"):
            tok = self._token_for_host(host)
            if tok:
                cfg.append(("http.extraHeader", f"Authorization: Bearer {tok}"))
        elif host in ("gitlab.com", "gitlab"):
            tok = self._token_for_host(host)
            if tok:
                cfg.append(("http.extraHeader", f"PRIVATE-TOKEN: {tok}"))
        else:
            # 白名单外 host：仅 host_tokens 显式登记时发送（映射值），否则不发
            mapped = (self.host_tokens.get(host) or "").strip()
            if mapped:
                cfgtok = mapped
                # 未知主机平台类型：gitlab 类头按其 feature 前缀？这里按通用 Authorization Bearer
                # 但为避免误发全局 token，未登记一律不发——已在上面过滤，此处 mapped 存在即登记。
                cfg.append(("http.extraHeader", f"Authorization: Bearer {cfgtok}"))
        # G04-4 下载限速：低于 lowSpeedLimit KiB/s 持续 lowSpeedTime 秒 → 中止
        if self.rate_limit_kbps > 0:
            cfg.append(("http.lowSpeedLimit", str(self.rate_limit_kbps)))
            cfg.append(("http.lowSpeedTime", "30"))
        # G38-6 强制 HTTP/1.1（IPv6 兼容问题常见修复）
        if self.force_ipv4:
            cfg.append(("http.version", "HTTP/1.1"))
        if cfg:
            env["GIT_CONFIG_COUNT"] = str(len(cfg))
            for i, (k, v) in enumerate(cfg):
                env[f"GIT_CONFIG_KEY_{i}"] = k
                env[f"GIT_CONFIG_VALUE_{i}"] = v
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
        """G38-2 按当前 host 拼接镜像前缀（仅 HTTPS；SSH/本地原样）。

        从 self.mirror_prefix[host] 取前缀；无前缀/SSH → 原样返回。
        """
        if not url or not self.mirror_prefix:
            return url
        if not url.lower().startswith("https://"):
            return url
        host = (getattr(self, "_current_host", "") or "").lower().replace("www.", "").rstrip("/")
        prefix = (self.mirror_prefix.get(host) or "").strip().rstrip("/")
        if not prefix:
            return url
        return f"{prefix}/{url}"

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
                capture_output=True, text=True, timeout=20,
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
                capture_output=True, text=True, timeout=20,
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
        except Exception:
            self._current_host = ""

        # G38-3 远端可达性预检（可选开启）：断网/坏 host 时 10s 内明确失败，
        # 不必等满 clone/fetch 超时。仅对含远端 URL 的仓库执行。
        if self.precheck_remote and not spec.is_local and spec.url_https:
            ok, err = self._precheck_reachable(spec.url_https)
            if not ok:
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
    def build_clone_cmd(self, spec: RepoSpec, repo_dir=None) -> list[str]:
        """G48-2/6：拼装 clone 命令（含 LFS -c 选项与 --mirror）。

        独立方法便于单测直接断言命令内容，无需跑真实 git。
        """
        repo_dir = repo_dir or self._repo_dir(spec)
        cmd = ["git"] + self._lfs_flag() + ["clone", "--progress"]
        if getattr(spec, "ref", ""):
            cmd += ["-b", spec.ref]
        if self.fetch_depth and self.single_branch:
            cmd += [f"--depth={self.fetch_depth}", "--single-branch"]
        if self._mirror:
            # G48-6 镜像克隆（--mirror 自带 --bare）
            cmd.append("--mirror")
        cmd += [self._mirror_url(spec.url_https), str(repo_dir)]
        if self._submodule:
            cmd.append("--recurse-submodules")
        return cmd

    def _run_post_clone_hook(self, repo_dir) -> None:
        """G48-5：成功克隆后运行用户钩子命令（%PATH% 替换为仓库路径；失败仅日志）。"""
        if not self._post_clone_hook:
            return
        import subprocess as _sp
        from pathlib import Path as _P
        cmd = self._post_clone_hook.replace("%PATH%", str(_P(repo_dir)))
        try:
            self._emit(f"执行克隆后钩子：{cmd}")
            _sp.run(cmd, shell=True, cwd=str(self.root),
                    capture_output=True, text=True, timeout=60)
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

        cmd = self.build_clone_cmd(spec, repo_dir)
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
                treeless_cmd = ["git", "clone", "--progress", "--filter=blob:none"]
                if getattr(spec, "ref", ""):
                    treeless_cmd += ["-b", spec.ref]
                treeless_cmd += [self._mirror_url(spec.url_https), str(repo_dir)]
                rc, prog = run_git_ui(treeless_cmd, str(self.root), _collate,
                                      cancelled=self.cancelled, env=self._env(),
                                      timeout=self.clone_timeout, retries=0,
                                      backoff=self._backoff,
                                      progress_detail=getattr(self, "send_progress_detail", None))
                # G38-5 降级链终档：treeless 仍失败 → depth1 单分支「保命模式」
                if rc != 0 and not self.cancelled():
                    self._emit(f"{spec.display} treeless 仍失败，尝试 depth1 单分支"
                               "保命模式（完整历史未拉取）…", "warn")
                    self._safe_rmtree_partial(repo_dir)
                    shallow_cmd = ["git", "clone", "--progress",
                                   "--depth=1", "--single-branch"]
                    if getattr(spec, "ref", ""):
                        shallow_cmd += ["-b", spec.ref]
                    shallow_cmd += [self._mirror_url(spec.url_https), str(repo_dir)]
                    rc, prog = run_git_ui(
                        shallow_cmd, str(self.root), _collate,
                        cancelled=self.cancelled, env=self._env(),
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
            # G48-6 镜像仓库（bare）：仅更新远端 refs，无工作树不 merge
            mr_rc, _ = run_git_ui(
                fetch_cmd, str(repo_dir), self.on_line, cancelled=self.cancelled,
                env=self._env(), timeout=self.fetch_timeout, retries=self.retries,
                backoff=self._backoff)
            if mr_rc == 0:
                res.status = SyncStatus.SUCCESS
                res.action = SyncAction.FETCHED
                res.message = "镜像已同步远端 refs"
            else:
                res.status = SyncStatus.FAILED
                res.action = SyncAction.FAILED
                res.message = "镜像 remote fetch 失败"
            return res
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
            return res
        if rc != 0:
            res.status = SyncStatus.FAILED
            res.action = SyncAction.FAILED
            res.message = "fetch 失败"
            res.detail = f"git fetch 退出码 {rc}"
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

        # 4) merge --ff-only（或 rebase）
        if commits_new > 0:
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
