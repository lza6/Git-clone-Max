"""跨会话续跑清单持久化（G52-2 / G52-3）。

data/session_runtime.json 记录当前批次每任务状态（pending/running/failed），
供「启动续跑对话框」与「看门狗」复用 engine 续跑。要点：
- 原子写：临时文件 + os.replace，损坏时兜底为空清单；
- 落盘前 redact 打码（URL 内嵌凭据 / 认证头等）；
- 线程安全：worker 回调与主线程定时器都可能更新，锁保护。
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import RepoSpec
from ..util.redact import redact

# 清单结构版本（向前兼容：未知字段忽略）
_SCHEMA = "1"
_STATUS_PENDING = "pending"
_STATUS_RUNNING = "running"
_STATUS_FAILED = "failed"
_STATUS_SUCCESS = "success"


@dataclass
class SessionTask:
    """续跑清单中的单个任务。"""

    key: str                     # owner/repo
    status: str                  # pending / running / failed / success
    url_https: str               # 已 redact 的远端地址（不含凭据）
    display: str = ""
    folder_name: str = ""
    local_path: str = ""
    ref: str = ""
    is_local: bool = False
    message: str = ""            # 失败原因（已 redact）


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


class RuntimeSessionStore:
    """session_runtime.json 封装：加载 / 原子写 / 状态更新 / 续跑 spec 还原。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    # ------------------------------------------------------------ 读写
    def load(self) -> dict[str, Any]:
        """读取清单；缺失 / 损坏 / 结构非法 → 返回空结构（不抛异常）。"""
        with self._lock:
            raw: dict[str, Any] = {}
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        raw = data
                except Exception:
                    raw = {}
            tasks = raw.get("tasks")
            if not isinstance(tasks, list):
                tasks = []
            # 逐条类型安全清洗（未知/坏字段丢弃，避免下游崩溃）
            cleaned: list[dict[str, Any]] = []
            for t in tasks:
                if not isinstance(t, dict) or not t.get("key"):
                    continue
                cleaned.append({
                    "key": str(t["key"]),
                    "status": str(t.get("status") or _STATUS_PENDING),
                    "url_https": str(t.get("url_https") or ""),
                    "display": str(t.get("display") or ""),
                    "folder_name": str(t.get("folder_name") or ""),
                    "local_path": str(t.get("local_path") or ""),
                    "ref": str(t.get("ref") or ""),
                    "is_local": bool(t.get("is_local", False)),
                    "message": str(t.get("message") or ""),
                })
            return {
                "schema": _SCHEMA,
                "finished_ok": bool(raw.get("finished_ok", False)),
                "updated_at": str(raw.get("updated_at") or ""),
                "tasks": cleaned,
            }

    def _save(self, data: dict[str, Any]) -> None:
        """原子写 + redact（防御性打码，双保险）。"""
        with self._lock:
            payload = json.loads(json.dumps(data, ensure_ascii=False))
            tasks = payload.get("tasks") or []
            for t in tasks:
                if isinstance(t, dict):
                    for k in ("url_https", "display", "message"):
                        if isinstance(t.get(k), str):
                            t[k] = redact(t[k])
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)

    # ------------------------------------------------------------ 业务操作
    def replace(self, specs: list[RepoSpec], status: str = _STATUS_PENDING) -> None:
        """以新批次整体替换清单（launch 时调用；不中断时保留旧失败项语义）。"""
        tasks = []
        for s in specs:
            tasks.append({
                "key": f"{s.owner}/{s.repo}",
                "status": status,
                "url_https": s.url_https,
                "display": s.display,
                "folder_name": s.folder_name,
                "local_path": s.local_path,
                "ref": s.ref,
                "is_local": bool(s.is_local),
                "message": "",
            })
        self._save({
            "schema": _SCHEMA,
            "finished_ok": False,
            "updated_at": _now(),
            "tasks": tasks,
        })

    def update_status(self, key: str, status: str, message: str = "") -> None:
        """更新单任务状态（结果回调）；成功/取消终态从清单移除（已完成无需续跑）。"""
        with self._lock:
            data = self.load()
            kept: list[dict[str, Any]] = []
            for t in data.get("tasks", []):
                if t.get("key") != key:
                    kept.append(t)
                    continue
                if status in (_STATUS_SUCCESS, "cancelled", "skipped", "conflict"):
                    continue  # 终态非失败 → 无需续跑，移除
                t["status"] = status
                if message:
                    t["message"] = redact(str(message))
                kept.append(t)
            data["tasks"] = kept
            data["updated_at"] = _now()
            self._save(data)

    def mark_failed(self, key: str, message: str = "") -> None:
        """便捷：标记失败（保留在清单内供续跑/看门狗）。"""
        self.update_status(key, _STATUS_FAILED, message)

    def mark_completed(self) -> None:
        """批次正常完成（未取消）→ finished_ok=True 并清空任务（看门狗不再续跑）。"""
        with self._lock:
            self._save({
                "schema": _SCHEMA,
                "finished_ok": True,
                "updated_at": _now(),
                "tasks": [],
            })

    def clear(self) -> None:
        """清空清单（用户选择「清空」，或不需要续跑）。"""
        with self._lock:
            self._save({
                "schema": _SCHEMA,
                "finished_ok": True,
                "updated_at": _now(),
                "tasks": [],
            })

    # ------------------------------------------------------------ 查询
    def is_empty(self) -> bool:
        return not bool(self.list_tasks())

    def is_resumable(self) -> bool:
        """看门狗/启动续跑判定：非空且上次未正常完成。"""
        data = self.load()
        return bool(data.get("tasks")) and not bool(data.get("finished_ok"))

    def list_tasks(self) -> list[dict[str, Any]]:
        return self.load().get("tasks", [])

    def to_repo_specs(self) -> list[RepoSpec]:
        """把清单还原为可续跑的 RepoSpec 列表（url 已 redact，不含凭据）。"""
        specs: list[RepoSpec] = []
        for t in self.list_tasks():
            url = str(t.get("url_https") or "")
            key = str(t.get("key") or "")
            owner, _, repo = key.partition("/")
            if not owner or not repo:
                continue
            specs.append(RepoSpec(
                owner=owner,
                repo=repo,
                url_https=url,
                display=str(t.get("display") or key),
                folder_name=str(t.get("folder_name") or f"{owner}__{repo}"),
                local_path=str(t.get("local_path") or ""),
                is_local=bool(t.get("is_local", False)),
                ref=str(t.get("ref") or ""),
            ))
        return specs
