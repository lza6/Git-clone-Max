"""同步队列持久化（G80-B7-3 / S1）：`sync_queue` 表 + schema v4 迁移。

设计要点（来自 `docs/B7-3-持久队列设计.md`）：
- **只新增表**，不动既有列/表 → 旧库升级是幂等的 `CREATE TABLE IF NOT EXISTS`，
  回滚只需删代码（老版本打开新库会忽略未知表）。
- 队列记录的是「什么时候跑一批什么」，**不是**任务级重试（那是 `retry_queue` 的职责，
  且 G67-3 已确认两层重投会互相污染）—— 本模块不做任何重试语义。
- `days_of_week` 用 '1..7'（1=周一），与 ISO 一致，避免 0/1 起始歧义。
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Any

from ..i18n import tr

_QUEUE_SCHEMA = """
CREATE TABLE IF NOT EXISTS sync_queue (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    specs_json    TEXT NOT NULL,
    days_of_week  TEXT NOT NULL DEFAULT '1,2,3,4,5,6,7',
    start_time    TEXT DEFAULT '',
    end_time      TEXT DEFAULT '',
    enabled       INTEGER DEFAULT 1,
    on_missed     TEXT DEFAULT 'skip',
    last_run_at   TEXT DEFAULT '',
    last_status   TEXT DEFAULT '',
    created_at    TEXT,
    updated_at    TEXT
);
"""

_ON_MISSED_VALUES = ("skip", "run_once")

#: B1 调度账本：每个「队列 × 计划槽位」只允许被抢占一次（INSERT OR IGNORE）。
#: slot_key 由 `queue_scheduler.window_slot` 确定性生成（含窗口日期与起止时刻），
#: 因此同一窗口内的多次 tick / 补跑都落到同一行 —— 幂等由主键保证，不再依赖
#: `last_run_at` 字符串反推。**抢占只由 `app/queue_runner.tick` 发起**：手动批次
#: 走既有 `_launch` 路径，不经过本表。失败不释放槽位（见 queue_runner 内的定案）。
_SCHEDULED_RUNS_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_runs (
    queue_id      INTEGER NOT NULL,
    slot_key      TEXT NOT NULL,
    scheduled_for TEXT NOT NULL DEFAULT '',
    trigger       TEXT NOT NULL DEFAULT '',
    state         TEXT NOT NULL DEFAULT 'claimed',
    run_id        TEXT NOT NULL DEFAULT '',
    created_at    TEXT,
    PRIMARY KEY (queue_id, slot_key)
);
"""


def migrate_to_v4(conn: sqlite3.Connection) -> None:
    """schema v3 → v4：新增 sync_queue 表（幂等，旧数据零影响）。"""
    conn.executescript(_QUEUE_SCHEMA)


def migrate_to_v9(conn: sqlite3.Connection) -> None:
    """schema v8 → v9：新增 scheduled_runs 调度账本表（B1，幂等只增表）。

    只新增表、不动既有列：老版本打开新库会忽略未知表，回滚只需删代码。
    """
    conn.executescript(_SCHEDULED_RUNS_SCHEMA)


def parse_days(raw: str) -> set[int]:
    """'1,3,5' → {1,3,5}；非法段忽略；空串/None → 全周。"""
    text = str(raw or "").strip()
    if not text:
        return set(range(1, 8))
    out: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if part.isdigit() and 1 <= int(part) <= 7:
            out.add(int(part))
    return out or set(range(1, 8))


def parse_hhmm(raw: str) -> tuple[int, int] | None:
    """'02:30' → (2, 30)；空/非法 → None（表示不限）。"""
    text = str(raw or "").strip()
    if not text:
        return None
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        return None
    hh, mm = int(parts[0]), int(parts[1])
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None
    return (hh, mm)


class QueueStore:
    """sync_queue 表的读写（与 repo_db 相同的锁与事务纪律）。"""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock):
        self._conn = conn
        self._lock = lock

    # ---------------------------------------------------------------- 写
    def _write_txn(self, sql: str, args: tuple) -> int:
        """队列写事务（沿用 repo_db 的锁 + BEGIN IMMEDIATE + 回滚纪律）。"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(sql, args)
                self._conn.commit()
                return int(cur.lastrowid or 0)
            except Exception:
                self._conn.rollback()
                raise

    def add_queue(self, name: str, specs: list[dict[str, Any]], *,
                  days: str = "", start: str = "", end: str = "",
                  on_missed: str = "skip") -> int:
        """新建队列；返回 id。specs 序列化失败或参数非法时抛 ValueError。"""
        name = str(name or "").strip()
        if not name:
            raise ValueError("队列名不能为空")
        if not isinstance(specs, list) or not specs:
            raise ValueError("specs 必须是非空列表")
        if on_missed not in _ON_MISSED_VALUES:
            raise ValueError(tr("on_missed 只支持 {}").format("/".join(_ON_MISSED_VALUES)))
        if parse_hhmm(start) is None and start:
            raise ValueError(tr("start_time 非法：{}").format(repr(start)))
        if parse_hhmm(end) is None and end:
            raise ValueError(tr("end_time 非法：{}").format(repr(end)))
        sql = (
            "INSERT INTO sync_queue (name, specs_json, days_of_week, start_time,"
            " end_time, enabled, on_missed, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, 1, ?, datetime('now','localtime'),"
            " datetime('now','localtime'))")
        return self._write_txn(sql, (
            name, json.dumps(specs, ensure_ascii=False),
            str(days or ""), str(start or ""), str(end or ""), on_missed))

    def update_queue(self, queue_id: int, *, days: str = "", start: str = "",
                     end: str = "", on_missed: str = "skip") -> None:
        """更新队列时间窗；非法参数抛 ValueError 且整条回滚。

        参数语义与 `add_queue` 一致：days 空串 = 每天；start/end 空串 = 不限。
        """
        if on_missed not in _ON_MISSED_VALUES:
            raise ValueError(tr("on_missed 只支持 {}").format("/".join(_ON_MISSED_VALUES)))
        if start and parse_hhmm(start) is None:
            raise ValueError(tr("start_time 非法：{}").format(repr(start)))
        if end and parse_hhmm(end) is None:
            raise ValueError(tr("end_time 非法：{}").format(repr(end)))
        sql = (
            "UPDATE sync_queue SET days_of_week=?, start_time=?, end_time=?,"
            " on_missed=?, updated_at=datetime('now','localtime') WHERE id=?")
        self._write_txn(sql, (str(days or ""), str(start or ""), str(end or ""),
                              on_missed, int(queue_id)))

    def delete_queue(self, queue_id: int) -> None:
        """删除队列（幂等）。"""
        self._write_txn("DELETE FROM sync_queue WHERE id=?", (int(queue_id),))

    def set_enabled(self, queue_id: int, enabled: bool) -> None:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE sync_queue SET enabled=?, updated_at=datetime('now','localtime')"
                    " WHERE id=?", (1 if enabled else 0, int(queue_id)))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def mark_run(self, queue_id: int, status: str) -> None:
        """记录一次真实运行（成功/失败/跳过都算「跑过」）。"""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                self._conn.execute(
                    "UPDATE sync_queue SET last_run_at=datetime('now','localtime'),"
                    " last_status=?, updated_at=datetime('now','localtime') WHERE id=?",
                    (str(status or ""), int(queue_id)))
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    # ------------------------------------------------------ B1 调度账本
    def claim_slot(self, queue_id: int, slot_key: str, scheduled_for: str,
                   trigger: str, run_id: str = "") -> bool:
        """抢占一个计划槽位；同一 (queue_id, slot_key) 只有第一次返回 True。

        `INSERT OR IGNORE` + 主键冲突天然幂等：窗口内反复 tick、补跑与手动触发
        共用同一 slot_key，不会重复启动同一计划。
        """
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "INSERT OR IGNORE INTO scheduled_runs"
                    " (queue_id, slot_key, scheduled_for, trigger, state, run_id,"
                    "  created_at) VALUES (?, ?, ?, ?, 'claimed', ?,"
                    "  datetime('now','localtime'))",
                    (int(queue_id), str(slot_key), str(scheduled_for or ""),
                     str(trigger or ""), str(run_id or "")))
                self._conn.commit()
                return int(cur.rowcount or 0) > 0
            except Exception:
                self._conn.rollback()
                raise

    def release_slot(self, queue_id: int, slot_key: str) -> None:
        """释放抢占（仅测试/人工修复用；runner 在启动失败时**不**调用）。

        B1 审查定案（MAJOR-3）：失败路径保留槽位 + mark_run 计「今天已跑」，
        防止 60s 重试风暴；本方法只留作测试与未来人工修复入口。
        """
        self._write_txn(
            "DELETE FROM scheduled_runs WHERE queue_id=? AND slot_key=?",
            (int(queue_id), str(slot_key)))

    def set_slot_state(self, queue_id: int, slot_key: str, state: str) -> None:
        """推进槽位状态（claimed → done）；不存在时静默（幂等）。"""
        self._write_txn(
            "UPDATE scheduled_runs SET state=? WHERE queue_id=? AND slot_key=?",
            (str(state or ""), int(queue_id), str(slot_key)))

    def list_slots(self, queue_id: int, limit: int = 20) -> list[dict[str, Any]]:
        """最近的槽位记录（审计/测试用）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM scheduled_runs WHERE queue_id=?"
                " ORDER BY created_at DESC LIMIT ?",
                (int(queue_id), int(limit))).fetchall()
        return [dict(r) for r in rows]

    # ---------------------------------------------------------------- 读
    def list_queues(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sync_queue ORDER BY enabled DESC, id").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["specs"] = json.loads(d.get("specs_json") or "[]")
            except ValueError:
                d["specs"] = []
            d["days_set"] = sorted(parse_days(d.get("days_of_week") or ""))
            out.append(d)
        return out

    def get_queue(self, queue_id: int) -> dict[str, Any] | None:
        for d in self.list_queues():
            if int(d.get("id") or 0) == int(queue_id):
                return d
        return None
