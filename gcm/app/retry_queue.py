"""任务级自动重试队列（G52-1）。

失败任务（网络类错误）持久化 data/retry_queue.json；引擎空闲时按退避基数
自动重投。要点：
- 只重试网络类错误：复用 gcm/git/service.py 的 _is_networkish_error（仓库类 /
  认证类 / 平台限制类一律不重试）；
- 最多重试次数 0-3：attempts 超限即丢弃（记 dropped 供审计）；
- 退避基数 30/60/120s：第 n 次重试延迟 = base * n，上限 1 小时；
- 原子写（tmp + os.replace）+ 落盘 redact 打码；线程安全。
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Optional

from ..git.service import _is_networkish_error
from ..models import RepoSpec
from ..util.redact import redact

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
                 now: Optional[Callable[[], float]] = None):
        """path 为空 → 仅内存模式（不落盘）；now 注入时钟（测试用）。"""
        self.path = Path(path) if path else None
        self.enabled = bool(enabled)
        # 设置页约束 0-3；越界值收敛到合法区间
        self.max_retries = max(0, min(3, int(max_retries)))
        self.backoff_base_sec = max(0, int(backoff_base_sec))
        self._now = now or time.time
        self._lock = threading.RLock()
        if self.path is not None:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass

    # ------------------------------------------------------------ 持久化
    def load(self) -> dict[str, Any]:
        """读取队列；缺失 / 损坏 → 空结构（不抛异常）。"""
        with self._lock:
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
        """原子写 + redact 打码（URL 内嵌凭据 / 认证头）。"""
        if self.path is None:
            return
        with self._lock:
            payload = json.loads(json.dumps(data, ensure_ascii=False))
            entries = payload.get("entries") or {}
            for rec in entries.values():
                if isinstance(rec, dict):
                    for k in ("url_https", "display", "message", "detail"):
                        if isinstance(rec.get(k), str):
                            rec[k] = redact(rec[k])
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)

    # ------------------------------------------------------------ 入队 / 判定
    def record_failure(self, spec: RepoSpec, message: str, detail: str = "") -> bool:
        """失败结果入队。仅网络类错误且未超限才入队；返回是否真正入队。

        成功返回 True；关闭开关 / 非网络错误 / 已超最多重试 → False。
        """
        if not self.enabled:
            return False
        text = f"{message or ''}\n{detail or ''}"
        if not _is_networkish_error(text):
            return False
        with self._lock:
            data = self.load()
            key = f"{spec.owner}/{spec.repo}"
            rec = data["entries"].get(key)
            attempts = int((rec or {}).get("attempts", 0)) + 1
            if attempts > self.max_retries:
                # 超限：从队列摘除并记 dropped（不再重投）
                data["entries"].pop(key, None)
                data["dropped"].append({
                    "key": key,
                    "attempts": attempts,
                    "message": redact(str(message)),
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                })
                if len(data["dropped"]) > 200:
                    del data["dropped"][: len(data["dropped"]) - 200]
                self._save(data)
                return False
            data["entries"][key] = {
                "key": key,
                "status": "queued",
                "attempts": attempts,
                "url_https": spec.url_https,
                "display": spec.display or f"{spec.owner}/{spec.repo}",
                "folder_name": spec.folder_name or f"{spec.owner}__{spec.repo}",
                "local_path": spec.local_path,
                "ref": spec.ref,
                "is_local": bool(spec.is_local),
                "message": redact(str(message)),
                "detail": redact(str(detail)),
                "next_retry_at": self._compute_next_retry_at(attempts),
                "queued_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            data["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self._save(data)
            return True

    def _compute_next_retry_at(self, attempts: int) -> float:
        """退避：第 n 次重试延迟 = base * n（上限 1 小时）。"""
        delay = self.backoff_base_sec * max(1, attempts)
        return self._now() + min(delay, _MAX_BACKOFF_SEC)

    def due_tasks(self, now: Optional[float] = None) -> list[dict[str, Any]]:
        """到期（next_retry_at <= now）的重试任务列表，按到期时间升序。"""
        ts = self._now() if now is None else float(now)
        with self._lock:
            data = self.load()
            due = [rec for rec in data["entries"].values()
                   if float(rec.get("next_retry_at", 0)) <= ts]
        due.sort(key=lambda r: float(r.get("next_retry_at", 0)))
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

    def clear(self) -> None:
        """清空队列（保留 dropped 审计）。"""
        with self._lock:
            self._save({"entries": {}, "dropped": self.load().get("dropped", [])})

    # ------------------------------------------------------------ 查询
    def count(self) -> int:
        return len(self.load().get("entries", {}))

    def list_entries(self) -> list[dict[str, Any]]:
        return list(self.load().get("entries", {}).values())

    def attempts_for(self, key: str) -> int:
        rec = self.load().get("entries", {}).get(key)
        return int((rec or {}).get("attempts", 0))

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
