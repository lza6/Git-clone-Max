"""操作台账 + 撤销（Rev12 U-01…U-04，尼尔森原则③「用户控制与自由」）。

**为什么不是数据库表**：Rev10 的 O-01 设计把 oplog 放进 `repo_db`，需要
`_SCHEMA_VERSION` 8→9 的迁移 —— 那是 `policy.md` 的 **T3 高风险**（持久化数据），
且 Rev10 尚未开工。本模块用**独立 JSON 文件**承担同一职责，代价是少一次 JOIN，
收益是不触碰任何现存表、不需要迁移、不需要回滚方案，可整体删除。

**三个不变量**（破坏任何一个都会让「撤销」变成新的数据丢失源）：

1. **单写者**：所有写入经过 `cross_process_lock(OPLOG_NAME)`，且读-改-写在**同一个**
   临界区内完成（照抄 `db/progress_file.py:68-93` 的范式 —— 那条注释记录了当年
   「load 取锁又释放、save 再独立取锁」导致丢更新的教训）。
2. **出站即打码**：`before_json` 里可能出现 `https://user:pass@host/o/r`，
   写入前过 `redact()`；不是展示层才打码，而是**落盘前**（诊断包会整个搬走这个文件）。
3. **只有 `undoable=1` 的条目才允许出现撤销入口**。`undoable=0` 的条目不承诺可撤销 ——
   `prune` / 批次克隆 / 磁盘删除都属此类。UI 不得对 `undoable=0` 画撤销按钮。

**保留期**：默认 200 条上限（写入时裁掉最旧的）。不做按天清理 —— 这个文件很小
（每条 < 1KB），而「撤销」只有在当前会话附近才有意义，按条数裁剪比按时间更可预测。
"""
from __future__ import annotations

import contextlib
import json
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .atomic_io import write_json_atomic
from .filelock import cross_process_lock
from .redact import redact

#: 台账文件名（落在 data_dir 下）
OPLOG_NAME = "oplog.json"
#: 默认保留条数上限
DEFAULT_KEEP = 200

#: 允许撤销的 action → 逆向操作的 action 名（仅供展示/审计，真逆做在调用方）
UNDOABLE_ACTIONS = frozenset({
    "repo_delete",
    "repo_tags",
    "repo_favorite",
    "repo_excluded",
    "list_delete",
    "retry_queue_clear",
    "url_history_clear",
    "input_clear",
})


@dataclass(slots=True)
class OpRecord:
    """一条操作记录。`before` 是撤销所需的旧值快照（已打码）。"""

    id: str
    ts: float
    action: str
    target: str
    summary: str = ""
    before: dict[str, Any] = field(default_factory=dict)
    #: 1 = 承诺可撤销（有对应 undo 实现）；0 = 只记录，UI 不得出现撤销按钮
    undoable: int = 0
    #: 同一批判次共享，便于「整批撤销」
    batch_id: str = ""
    #: 已被撤销的时间戳（0 = 未撤销）；用于防重复撤销
    undone_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ts": self.ts,
            "action": self.action,
            "target": self.target,
            "summary": self.summary,
            "before": self.before,
            "undoable": int(self.undoable),
            "batch_id": self.batch_id,
            "undone_at": self.undone_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> OpRecord:
        """宽松解析：字段缺失/类型不对时取安全默认值，绝不因一条坏记录炸掉整个台账。"""
        before = d.get("before")
        return cls(
            id=str(d.get("id") or uuid.uuid4().hex),
            ts=float(d.get("ts") or 0.0),
            action=str(d.get("action") or ""),
            target=str(d.get("target") or ""),
            summary=str(d.get("summary") or ""),
            before=dict(before) if isinstance(before, dict) else {},
            undoable=1 if d.get("undoable") else 0,
            batch_id=str(d.get("batch_id") or ""),
            undone_at=float(d.get("undone_at") or 0.0),
        )


def _redact_deep(value: Any) -> Any:
    """递归打码。只处理 str/dict/list —— JSON 里不会出现别的东西。"""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_deep(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_deep(v) for v in value]
    return value


class OpLog:
    """操作台账（JSON + 跨进程锁）。线程安全由 `cross_process_lock` 顺带覆盖。"""

    def __init__(self, path: str | Path, *, keep: int = DEFAULT_KEEP) -> None:
        self.path = Path(path)
        self.keep = max(1, int(keep))

    # ---------------------------------------------------------------- 内部
    @contextlib.contextmanager
    def _guard(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 锁文件由 cross_process_lock 自行追加 `.lock` 后缀，这里只能传目标文件路径。
        with cross_process_lock(self.path, label="操作台账"):
            yield

    def _load_unlocked(self) -> list[OpRecord]:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return []
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            # 台账损坏不该阻断任何业务：当作空台账，下一条写入会整体覆盖它
            return []
        items = data.get("records") if isinstance(data, dict) else data
        if not isinstance(items, list):
            return []
        out: list[OpRecord] = []
        for it in items:
            if isinstance(it, dict):
                try:
                    out.append(OpRecord.from_dict(it))
                except (TypeError, ValueError):
                    continue
        return out

    def _save_unlocked(self, records: list[OpRecord]) -> None:
        # 新的在前；超过上限裁掉最旧的（被裁掉的自然不可撤销，符合预期）
        ordered = sorted(records, key=lambda r: r.ts, reverse=True)[: self.keep]
        write_json_atomic(self.path, {
            "version": 1,
            "records": [r.as_dict() for r in ordered],
        })

    # ---------------------------------------------------------------- 写
    def record(self, action: str, target: str, *, summary: str = "",
               before: dict[str, Any] | None = None, undoable: bool | None = None,
               batch_id: str = "") -> OpRecord:
        """记一条操作。`undoable` 省略时按 `UNDOABLE_ACTIONS` 判定。

        返回值即落盘的那条记录（供调用方拿到 id 做即时撤销）。
        """
        if undoable is None:
            undoable = action in UNDOABLE_ACTIONS
        rec = OpRecord(
            id=uuid.uuid4().hex[:16],
            ts=time.time(),
            action=action,
            target=str(target),
            summary=str(summary),
            before=_redact_deep(dict(before or {})),
            undoable=1 if undoable else 0,
            batch_id=str(batch_id),
        )
        with self._guard():
            records = self._load_unlocked()
            records.append(rec)
            self._save_unlocked(records)
        return rec

    def mark_undone(self, record_id: str) -> bool:
        """标记为已撤销（防重复撤销）。返回是否真的标记到了。"""
        with self._guard():
            records = self._load_unlocked()
            hit = False
            for r in records:
                if r.id == record_id and not r.undone_at:
                    r.undone_at = time.time()
                    hit = True
                    break
            if hit:
                self._save_unlocked(records)
        return hit

    # ---------------------------------------------------------------- 读
    def recent(self, limit: int = 20, *, undoable_only: bool = False,
               include_undone: bool = False) -> list[OpRecord]:
        with self._guard():
            records = self._load_unlocked()
        out = [
            r for r in records
            if (not undoable_only or r.undoable == 1)
            and (include_undone or not r.undone_at)
        ]
        return out[: max(0, int(limit))]

    def get(self, record_id: str) -> OpRecord | None:
        with self._guard():
            for r in self._load_unlocked():
                if r.id == record_id:
                    return r
        return None

    def clear(self) -> int:
        """清空台账（测试与「清除操作历史」用）。返回清掉的条数。"""
        with self._guard():
            n = len(self._load_unlocked())
            self._save_unlocked([])
        return n


def default_oplog(data_dir: str | Path) -> OpLog:
    return OpLog(Path(data_dir) / OPLOG_NAME)


__all__ = ["DEFAULT_KEEP", "OPLOG_NAME", "OpLog", "OpRecord", "UNDOABLE_ACTIONS",
           "default_oplog"]
