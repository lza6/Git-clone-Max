"""MCP 异步作业机制（G84-13 起；G84-24 从 `mcp_server.py` 拆出）。

**为什么单独成模块**：`gcm/mcp_server.py` 因为加异步作业/资源/提示词越过了项目
800 行硬上限（守卫 `test_g79_repo_hygiene::TestNoNewOversizedModules` 用集合相等
断言，多出第 4 个超限文件直接红）。这与 v4 的 `queue_db.py`、v5 的 `repo_profile.py`、
C3 的 `txn.py`、G84 的 `backup.py` 是同一范式：**新能力落新模块，不往热文件里塞**。

**这层负责什么**（与 `mcp_server.py` 的边界）：
- 这里只关心「起一个后台子进程、读它的输出、能查状态、能取消」；
- **不关心 MCP 协议**：进度通知通过 `on_progress` 回调交给调用方去写帧
  （这样本模块不持有 stdout，也不掺进协议的锁）。

**两条实测过的坑（改代码前先读）**：
1. **必须并行读 stdout 与 stderr**：CLI 的最终 JSON 走 stdout，Windows 管道缓冲
   仅 4096 字节，单条 results ≈230 字节 —— 先读完 stderr 再读 stdout 会在
   20 个仓库以上**永久死锁**（子进程阻塞在写 stdout，stderr 永不 EOF）。
2. **取消/失败必须收尸**：只杀父进程会留下 `git`/`ssh` 孤儿继续写盘；POSIX 侧
   子进程需自成会话（`start_new_session`）才能整组终止，且**绝不能对自己的
   进程组开火**（同组 killpg 会把服务自己一起杀掉）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .util.redact import redact

#: 作业表上限（超出按插入顺序丢已完成的；全在跑时拒绝新作业）
MAX_JOBS = 20
#: 单作业整体超时（与同步路径 `subprocess timeout=3600` 对齐）
JOB_TIMEOUT_S = 3600

_JOBS: dict[str, dict[str, Any]] = {}
_JOBS_LOCK = threading.Lock()

#: 进度回调签名：(已读行数, 该行文本)。只在调用方提供了 progressToken 时才会被调用。
ProgressFn = Callable[[int, str], None]


def _log(text: str) -> None:
    """写 stderr（stdout 是 JSON-RPC 通道，绝不能污染）。"""
    try:
        sys.stderr.write(str(text)[:2000] + "\n")
        sys.stderr.flush()
    except Exception:      # noqa: BLE001 —— 日志失败不得影响作业
        return


def job_cmd(data_dir: Path) -> list[str]:
    """作业真正执行的命令（抽出来是为了可注入：死锁回归测试用假命令）。"""
    return [sys.executable, "-m", "gcm", "--cli", "--json", "--dir", str(data_dir)]


def kill_tree(proc: Any) -> None:
    """终止整棵子进程树。

    Windows 用 `taskkill /T /F`；POSIX 用 killpg（先 TERM 后 KILL）。
    **绝不对自己所在进程组开火**：子进程若没有自成会话，它的 pgid 就是我们的
    pgid —— 那个 killpg 会把本进程一起杀掉。
    """
    if proc is None:
        return
    try:
        if proc.poll() is not None:
            return
    except Exception as e:      # noqa: BLE001 —— 无 poll 的对象按"还活着"处理
        _log(f"proc.poll 不可用（按仍存活处理）：{e}")
    if os.name == "nt":
        try:
            r = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                               capture_output=True, timeout=15)
            if r.returncode == 0 and proc.poll() is not None:
                return
        except Exception as e:      # noqa: BLE001 —— 杀软拦截时退化为 kill
            _log(f"taskkill 失败（退化为 proc.kill）：{e}")
    else:
        try:
            import signal

            pgid = os.getpgid(proc.pid)
            if pgid and pgid != os.getpgid(0):
                os.killpg(pgid, signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except Exception:      # noqa: BLE001 —— 超时即强杀整组
                    try:
                        os.killpg(pgid, getattr(signal, "SIGKILL", signal.SIGTERM))
                    except Exception as e2:   # noqa: BLE001 —— 组已消失
                        _log(f"SIGKILL 进程组失败（可能已退出）：{e2}")
            else:
                _log("子进程与本进程同组，跳过 killpg（避免自杀），退化为 proc.kill")
        except Exception as e:      # noqa: BLE001 —— 无进程组权限时退化为 kill
            _log(f"killpg 失败（退化为 proc.kill）：{e}")
    try:
        proc.kill()
    except Exception as e:      # noqa: BLE001 —— 进程可能已自行退出
        _log(f"proc.kill 失败（进程可能已退出）：{e}")


def _pump(proc: Any, job: dict[str, Any],
          on_progress: ProgressFn | None) -> tuple[str, int]:
    """并行读 stdout/stderr 并等退出；返回 (stdout 全文, 退出码)。

    **必须并行**：CLI 的最终 JSON 走 stdout，Windows 匿名管道缓冲仅 4096 字节、
    单条 results ≈230 字节 —— 先读完 stderr 再读 stdout 会在 20 仓以上永久死锁
    （子进程阻塞在写 stdout，stderr 永不 EOF）。所以 stdout 交给独立线程排空。
    """
    out_chunks: list[str] = []

    def _drain_stdout() -> None:
        try:
            if proc.stdout is not None:
                out_chunks.append(proc.stdout.read())
        except Exception as e:      # noqa: BLE001 —— 读失败按空输出处理
            _log(f"读取克隆 stdout 失败：{e}")

    drain = threading.Thread(target=_drain_stdout, daemon=True)
    drain.start()

    deadline = time.time() + JOB_TIMEOUT_S
    lines = 0
    if proc.stderr is not None:
        for line in proc.stderr:
            if job.get("cancelled"):
                break
            lines += 1
            if on_progress is not None:
                on_progress(lines, line.strip()[:200])
            if time.time() > deadline:
                _log("作业读取阶段超过上限，终止子进程")
                job["timed_out"] = True
                kill_tree(proc)
                break
    try:
        code = proc.wait(timeout=60)
    except subprocess.TimeoutExpired:      # 取消/超时后仍不退出 → 强杀再等
        kill_tree(proc)
        try:
            code = proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            code = -1
    drain.join(timeout=10)
    return "".join(out_chunks), code


def _job_thread(job_id: str, data_dir: Path, body: str,
                on_progress: ProgressFn | None = None,
                cmd: list[str] | None = None) -> None:
    """作业线程：跑子进程，**并行**读 stdout/stderr，结束写结果。"""
    job = _JOBS[job_id]
    proc = None
    timer: threading.Timer | None = None
    try:
        if job.get("cancelled"):          # 取消早于 Popen：不要再启动
            job["status"] = "cancelled"
            return
        proc = subprocess.Popen(cmd or job_cmd(data_dir), stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                start_new_session=(os.name != "nt"))
        job["proc"] = proc
        if job.get("cancelled"):          # 竞态窗口：Popen 之后才置位
            kill_tree(proc)

        # 看门狗：**静默挂起**的子进程（既不写 stderr 也不退出）不会走到下面的
        # stderr 循环，只靠循环里的 deadline 检查永远超不了时 —— 审查发现。
        # 这里用独立 Timer 兜底，到点直接杀树并标 timed_out。
        def _timeout_kill() -> None:
            if job.get("finished"):
                return
            _log(f"作业 {job_id} 静默超过 {JOB_TIMEOUT_S}s，强制终止子进程")
            job["timed_out"] = True
            kill_tree(proc)

        timer = threading.Timer(JOB_TIMEOUT_S, _timeout_kill)
        timer.daemon = True
        timer.start()

        assert proc.stdin is not None
        try:
            proc.stdin.write(body)
            proc.stdin.close()
        except (BrokenPipeError, OSError) as e:
            # 取消竞态：子进程已被杀掉，管道写入会抛。这不是"失败"，是"已取消"。
            if job.get("cancelled"):
                _log(f"作业 {job_id} 写入请求体时已被取消（预期）：{e}")
            else:
                raise

        stdout, code = _pump(proc, job, on_progress)
        try:
            payload = json.loads(stdout or "{}")
        except ValueError:
            payload = {"error": {"code": -32603, "message": "克隆输出非 JSON",
                                 "redacted": redact(stdout)}}
        job["result"] = {"clone_result": payload, "cli_exit_code": code}
        if job.get("timed_out"):
            job["status"] = "timeout"
        elif job.get("cancelled"):
            job["status"] = "cancelled"
        else:
            job["status"] = "done"
    except Exception as e:      # noqa: BLE001 —— 作业失败要能读到原因
        job["result"] = {"error": {"code": -32603, "message": f"克隆调度失败：{e}"}}
        job["status"] = "failed"
        if proc is not None:      # 失败路径也必须收尸（否则 git/ssh 继续写盘）
            kill_tree(proc)
    finally:
        # `Timer.cancel()` 是线程安全的、不会抛 —— 不必再包 try（except 预算要省着花）
        if timer is not None:
            timer.cancel()
        job["finished"] = True


def prune_jobs_locked() -> None:
    """作业表治理（调用方必须已持 `_JOBS_LOCK`）。

    只保留最近 `MAX_JOBS` 个：按插入顺序丢**已完成**的；若全都还在跑则不再丢
    —— 运行中的作业不能被静默遗忘（否则用户拿不到结果、也取消不了）。
    """
    while len(_JOBS) >= MAX_JOBS:
        done = [k for k, v in _JOBS.items() if v.get("finished")]
        if not done:
            return
        _JOBS.pop(done[0], None)


def start_job(data_dir: Path, body: str, token: Any = None,
              on_progress: ProgressFn | None = None,
              cmd: list[str] | None = None) -> dict[str, Any]:
    """登记并启动作业；返回 `{job_id, status}`。作业表满时抛 ValueError。"""
    job_id = uuid.uuid4().hex[:12]
    with _JOBS_LOCK:
        prune_jobs_locked()
        if len(_JOBS) >= MAX_JOBS:
            # 全都在跑时表会无界增长（旧作业不能删）。宁可明确拒绝，
            # 也不要让服务"越用越吃内存、且旧作业再也取不到结果"。
            raise ValueError(
                f"作业数已达上限 {MAX_JOBS}（先用 job_status 收走结果或 job_cancel）")
        _JOBS[job_id] = {"status": "running", "finished": False, "cancelled": False,
                         "result": None, "proc": None, "token": token}
    t = threading.Thread(target=_job_thread,
                         args=(job_id, data_dir, body, on_progress, cmd), daemon=True)
    t.start()
    return {"job_id": job_id, "status": "running"}


def job_snapshot(job_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        return {"job_id": job_id, "status": job["status"],
                "finished": job["finished"], "result": job["result"]}


def cancel_job(job_id: str) -> dict[str, Any] | None:
    """取消作业。已完成/已失败的作业**不会被改写终态**（返回它的真实状态）。"""
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            return None
        if job.get("finished"):
            return {"job_id": job_id, "status": job["status"], "finished": True}
        job["cancelled"] = True
        job["status"] = "cancelling"
        proc = job.get("proc")
    if proc is not None:
        try:
            kill_tree(proc)
        except Exception as e:      # noqa: BLE001 —— 取消本身不能报错
            _log(f"kill_tree 失败，退化为 terminate：{e}")
            try:
                proc.terminate()
            except Exception as e2:   # noqa: BLE001 —— 进程可能已退出
                _log(f"terminate 也失败（进程可能已退出）：{e2}")
    return {"job_id": job_id, "status": "cancelling", "finished": False}
