"""任务级自动重试队列（G52-1 / G71-3 增强）。

失败任务持久化 data/retry_queue.json；引擎空闲时按退避基数自动重投。要点：
- 只重试**分类器判定可重试**的错误（网络类 / 限流 / 5xx）；仓库类 / 认证类 /
  平台限制 / 磁盘满一律不重试（`app/errors.classify_error` 是唯一判据）；
- 最多重试次数 0-3：attempts 超限即丢弃（记 dropped 供审计）；
- 退避基数 30/60/120s：第 n 次重试延迟 = base * n，上限 1 小时；
- 原子写（唯一 tmp + fsync + os.replace）+ 落盘 redact 打码；线程安全；
- G71-3 新增：每条记录带三段式错误信息（category/code/title/cause/action）、
  `retry_now`/`remove`/`snapshot`/`stats`，供 UI 的「重试队列」面板与 SSE 事件使用。
"""
from __future__ import annotations

import json
import random
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from ..models import RepoSpec
from ..util.atomic_io import write_json_atomic
from ..util.redact import redact
from .errors import classify_error

# 重试延迟上限（秒）：退避基数 * 次数 封顶，避免长时间挂起
_MAX_BACKOFF_SEC = 3600
_DEFAULT_BACKOFF_BASE_SEC = 30
_MAX_RETRIES_DEFAULT = 2


class RetryQueue:
    """失败重试队列：持久化记录 + 到期重投判定 + 成功清理。"""

    def __init__(self, path: Optional[str | Path] = None,
                 enabled: bool = True,
                 max_retries: int = _MAX_RETRIES_DEFAULT,
                 backoff_base_sec: int = _DEFAULT_BACKOFF_BASE_SEC,
                 now: Optional[Callable[[], float]] = None,
                 jitter: Optional[Callable[[float], float]] = None):
        """path 为空 → 仅内存模式（不落盘）；now/jitter 可注入（测试用）。

        G72：默认对退避做 **50%–100% full-ish jitter**，避免一批同时失败的仓库
        在同一秒再次齐发（尤其 429 / abuse detection），降低重试风暴。
        测试需要精确时间时传 `jitter=lambda d: d`。
        """
        self.path = Path(path) if path else None
        self.enabled = bool(enabled)
        # 设置页约束 0-3；越界值收敛到合法区间
        self.max_retries = max(0, min(3, int(max_retries)))
        self.backoff_base_sec = max(0, int(backoff_base_sec))
        self._now = now or time.time
        self._jitter = jitter or (lambda delay: random.uniform(delay * 0.5, delay))
        self._lock = threading.RLock()
        # G71-3：纯内存模式必须有真实存储 —— 原实现 `_load_locked` 每次都返回空骨架、
        # `_save` 直接 return，于是 `RetryQueue(path=None)` 记什么丢什么（旧测试只断言
        # count==0，把这个缺陷掩盖了）。内存态与落盘共用同一套结构。
        self._memory: dict[str, Any] = {"entries": {}, "dropped": []}
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    # ------------------------------------------------------------ 持久化
    def load(self) -> dict[str, Any]:
        """读取队列；缺失 / 损坏 → 空结构（不抛异常）。

        G69-15：**读也要持跨进程锁**。Windows 上 `os.replace` 与「正打开着目标文件的
        读者」冲突会直接 `PermissionError [WinError 5]` —— 实测 4 进程并发时出现
        「写方的整次保存失败」。只锁写方挡不住：读者随时可能正持着句柄。
        `progress_file.py` 的 `progress_guard` 早就同时包住 load/save，这里补齐一致。
        """
        # `path=None` 是纯内存模式（不落盘）—— 此时没有文件可锁，直接读内存态
        if self.path is None:
            return self._load_locked()
        from ..util.filelock import cross_process_lock
        with self._lock, cross_process_lock(self.path, label="retry_queue.json"):
            return self._load_locked()

    def _load_locked(self) -> dict[str, Any]:
        with self._lock:
            if self.path is None:
                # 深拷贝，避免调用方就地修改内存态而不经过 _save
                return json.loads(json.dumps(self._memory))
            raw: dict[str, Any] = {"entries": {}, "dropped": []}
            if self.path is not None and self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        raw = data
                except Exception:
                    raw = {"entries": {}, "dropped": []}
            entries = raw.get("entries")
            if not isinstance(entries, dict):
                entries = {}
            dropped = raw.get("dropped")
            if not isinstance(dropped, list):
                dropped = []
            return {"entries": entries, "dropped": dropped}

    def _save(self, data: dict[str, Any]) -> None:
        """原子写 + redact 打码（URL 内嵌凭据 / 认证头）；纯内存模式写内存。"""
        if self.path is None:
            with self._lock:
                self._memory = json.loads(json.dumps(data))
            return
        with self._lock:
            payload = json.loads(json.dumps(data, ensure_ascii=False))
            entries = payload.get("entries") or {}
            for rec in entries.values():
                if isinstance(rec, dict):
                    for k in ("url_https", "display", "message", "detail"):
                        if isinstance(rec.get(k), str):
                            rec[k] = redact(rec[k])
            # G69-6：唯一 tmp 名 + 跨进程锁（原为固定 .json.tmp 且无锁 —— 实测
            # 同进程双实例写 300 条最终只剩 16 条、87 次 PermissionError 被吞掉）
            write_json_atomic(self.path, payload, label="retry_queue.json")

    # ------------------------------------------------------------ 入队 / 判定
    def record_failure(self, spec: RepoSpec, message: str, detail: str = "") -> bool:
        """失败结果入队。仅可重试错误且未超限才入队；返回是否真正入队。

        兼容旧调用方；需要知道「为什么没入队」时用 `record_failure_ex`。
        """
        return bool(self.record_failure_ex(spec, message, detail).get("queued"))

    def record_failure_ex(self, spec: RepoSpec, message: str,
                          detail: str = "") -> dict[str, Any]:
        """同 `record_failure`，但返回结构化结果（供 SSE 事件与 UI 使用）。

        返回键：`queued` / `reason` / `key` / `attempts` / `next_retry_at` /
        `category` / `code` / `title` / `retryable`。
        """
        info = classify_error(message, detail)
        key = f"{spec.owner}/{spec.repo}"
        base = {
            "queued": False,
            "key": key,
            "category": info.category,
            "code": info.code,
            "title": info.title,
            "retryable": info.retryable,
            "attempts": 0,
            "next_retry_at": 0.0,
        }
        if not self.enabled:
            return {**base, "reason": "disabled"}
        if not info.retryable:
            # G71-11（审查 R1）：重投时若错误已变成永久失败（404/401/403/磁盘满/
            # 平台限制），必须**摘除旧条目**。否则 `due_tasks()` 仍把上次网络错误
            # 留下的条目判为到期，引擎每 15s 重投一次 → 无限循环 + 日志刷屏。
            with self._lock:
                data = self.load()
                removed = key in data.get("entries", {})
                if removed:
                    data["entries"].pop(key, None)
                    data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    self._save(data)
            return {**base, "reason": "non_retryable", "removed": removed}
        with self._lock:
            data = self.load()
            rec = data["entries"].get(key)
            attempts = int((rec or {}).get("attempts", 0)) + 1
            if attempts > self.max_retries:
                # 超限：从队列摘除并记 dropped（不再重投）
                data["entries"].pop(key, None)
                data["dropped"].append({
                    "key": key,
                    "attempts": attempts,
                    "message": redact(str(message)),
                    "detail": redact(str(detail)),
                    "category": info.category,
                    "code": info.code,
                    "title": info.title,
                    "retryable": info.retryable,
                    # G73：DLQ 重新入队需要完整 spec 字段（只存 key 会丢 folder/@tag）
                    "url_https": spec.url_https,
                    "display": spec.display or f"{spec.owner}/{spec.repo}",
                    "folder_name": spec.folder_name or f"{spec.owner}__{spec.repo}",
                    "local_path": spec.local_path,
                    "ref": spec.ref,
                    "is_local": bool(spec.is_local),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                if len(data["dropped"]) > 200:
                    del data["dropped"][: len(data["dropped"]) - 200]
                self._save(data)
                return {**base, "reason": "exhausted", "attempts": attempts}
            next_at = self._compute_next_retry_at(attempts)
            data["entries"][key] = {
                "key": key,
                "status": "queued",
                "state": "queued",
                "attempts": attempts,
                "url_https": spec.url_https,
                "display": spec.display or f"{spec.owner}/{spec.repo}",
                "folder_name": spec.folder_name or f"{spec.owner}__{spec.repo}",
                "local_path": spec.local_path,
                "ref": spec.ref,
                "is_local": bool(spec.is_local),
                "message": redact(str(message)),
                "detail": redact(str(detail)),
                "category": info.category,
                "code": info.code,
                "title": info.title,
                "cause": info.cause,
                "action": info.action,
                "retryable": info.retryable,
                "next_retry_at": next_at,
                "next_retry_at_iso": time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(next_at)),
                "last_attempt_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "queued_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save(data)
            return {**base, "queued": True, "reason": "queued",
                    "attempts": attempts, "next_retry_at": next_at}

    def _compute_next_retry_at(self, attempts: int) -> float:
        """退避：delay = min(base * n, 1h)，再施加可注入 jitter。"""
        delay = min(self.backoff_base_sec * max(1, attempts), _MAX_BACKOFF_SEC)
        try:
            delay = max(0.0, float(self._jitter(delay)))
        except Exception:
            pass
        return self._now() + delay

    def due_tasks(self, now: Optional[float] = None,
                  limit: Optional[int] = None) -> list[dict[str, Any]]:
        """到期（next_retry_at <= now）的重试任务列表，按到期时间升序。

        `limit` 用于限制单次 tick 重投批量（G72：防止几百个任务同时重投
        造成瞬时尖峰）；剩余任务留给下一次 tick。
        """
        ts = self._now() if now is None else float(now)
        with self._lock:
            data = self.load()
            due = [rec for rec in data["entries"].values()
                   if float(rec.get("next_retry_at", 0)) <= ts]
        due.sort(key=lambda r: float(r.get("next_retry_at", 0)))
        if limit is not None:
            due = due[:max(0, int(limit))]
        return due

    def to_spec(self, rec: dict[str, Any]) -> RepoSpec:
        """把队列记录还原为 RepoSpec（url 已 redact，不含凭据）。"""
        key = str(rec.get("key") or "")
        owner, _, repo = key.partition("/")
        if not owner or not repo:
            raise ValueError(f"队列记录缺少 owner/repo：{key}")
        return RepoSpec(
            owner=owner,
            repo=repo,
            url_https=str(rec.get("url_https") or ""),
            display=str(rec.get("display") or key),
            folder_name=str(rec.get("folder_name") or f"{owner}__{repo}"),
            local_path=str(rec.get("local_path") or ""),
            is_local=bool(rec.get("is_local", False)),
            ref=str(rec.get("ref") or ""),
        )

    def mark_success(self, key: str) -> None:
        """同步成功 → 从队列摘除（幂等）。"""
        with self._lock:
            data = self.load()
            if key in data.get("entries", {}):
                del data["entries"][key]
                data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._save(data)

    def remove(self, key: str) -> bool:
        """手动移除一条重试记录（用户放弃）。返回是否真的移除。"""
        with self._lock:
            data = self.load()
            if key not in data.get("entries", {}):
                return False
            del data["entries"][key]
            data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save(data)
            return True

    def retry_now(self, key: str) -> bool:
        """把某条记录的下次重试时间提前到「现在」（用户点「立即重试」）。"""
        with self._lock:
            data = self.load()
            rec = data.get("entries", {}).get(key)
            if not isinstance(rec, dict):
                return False
            rec["next_retry_at"] = 0.0
            rec["next_retry_at_iso"] = time.strftime("%Y-%m-%d %H:%M:%S")
            rec["state"] = "due"
            data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save(data)
            return True

    def retry_all_now(self) -> int:
        """把所有记录提前到「现在」；返回改动的条数。"""
        with self._lock:
            data = self.load()
            entries = data.get("entries", {})
            n = 0
            for rec in entries.values():
                if isinstance(rec, dict):
                    rec["next_retry_at"] = 0.0
                    rec["next_retry_at_iso"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    rec["state"] = "due"
                    n += 1
            if n:
                data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self._save(data)
            return n

    def clear(self) -> None:
        """清空队列（保留 dropped 审计）。"""
        with self._lock:
            self._save({"entries": {}, "dropped": self.load().get("dropped", [])})

    # ------------------------------------------------------------ 查询
    def count(self) -> int:
        return len(self.load().get("entries", {}))

    def list_entries(self) -> list[dict[str, Any]]:
        return list(self.load().get("entries", {}).values())

    def snapshot(self) -> list[dict[str, Any]]:
        """按「下次重试时间」升序返回队列快照（UI/SSE 用）。"""
        entries = self.list_entries()
        entries.sort(key=lambda r: float(r.get("next_retry_at", 0) or 0))
        return entries

    def dropped(self, limit: int = 50) -> list[dict[str, Any]]:
        """最近的丢弃审计（新→旧）。"""
        rows = list(self.load().get("dropped") or [])
        return list(reversed(rows))[: max(0, int(limit))]

    def requeue_dropped(self, key: str) -> bool:
        """G73：把一条已放弃任务重新入队（重置尝试次数，立即到期）。

        与云队列的 DLQ redrive 对齐：用户修正配置/网络后，把永久失败的任务放回
        主队列，而不是手工重新输入仓库地址。`attempts` 重置为 0，避免一入队就
        再次超限；下一次失败从 attempts=1 开始。
        """
        if not key:
            return False
        with self._lock:
            data = self.load()
            rows = data.get("dropped") or []
            idx = None
            for i in range(len(rows) - 1, -1, -1):
                if isinstance(rows[i], dict) and rows[i].get("key") == key:
                    idx = i
                    break
            if idx is None:
                return False
            rec = rows.pop(idx)
            entry = {
                "key": key,
                "status": "queued",
                "state": "queued",
                "attempts": 0,
                "url_https": str(rec.get("url_https") or ""),
                "display": str(rec.get("display") or key),
                "folder_name": str(rec.get("folder_name") or key.replace("/", "__")),
                "local_path": str(rec.get("local_path") or ""),
                "ref": str(rec.get("ref") or ""),
                "is_local": bool(rec.get("is_local", False)),
                "message": str(rec.get("message") or ""),
                "detail": str(rec.get("detail") or ""),
                "category": str(rec.get("category") or "other"),
                "code": str(rec.get("code") or "unknown"),
                "title": str(rec.get("title") or ""),
                "cause": "",
                "action": "",
                "retryable": bool(rec.get("retryable", True)),
                "next_retry_at": 0.0,
                "next_retry_at_iso": time.strftime("%Y-%m-%d %H:%M:%S"),
                "last_attempt_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "queued_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            data.setdefault("entries", {})[key] = entry
            data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save(data)
            return True

    def stats(self) -> dict[str, Any]:
        """队列健康度：总数 / 到期数 / 各类别计数。"""
        entries = self.list_entries()
        now = self._now()
        by_cat: dict[str, int] = {}
        for rec in entries:
            cat = str(rec.get("category") or "other")
            by_cat[cat] = by_cat.get(cat, 0) + 1
        return {
            "count": len(entries),
            "due": sum(1 for r in entries
                       if float(r.get("next_retry_at", 0) or 0) <= now),
            "by_category": by_cat,
            "dropped": len(self.load().get("dropped") or []),
            "enabled": self.enabled,
            "max_retries": self.max_retries,
        }

    def attempts_for(self, key: str) -> int:
        rec = self.load().get("entries", {}).get(key)
        return int((rec or {}).get("attempts", 0))

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
