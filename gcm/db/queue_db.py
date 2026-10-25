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


def migrate_to_v4(conn: sqlite3.Connection) -> None:
    """schema v3 → v4：新增 sync_queue 表（幂等，旧数据零影响）。"""
    conn.executescript(_QUEUE_SCHEMA)


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
            raise ValueError(f"on_missed 只支持 {'/'.join(_ON_MISSED_VALUES)}")
        if parse_hhmm(start) is None and start:
            raise ValueError(f"start_time 非法：{start!r}")
        if parse_hhmm(end) is None and end:
            raise ValueError(f"end_time 非法：{end!r}")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                cur = self._conn.execute(
                    "INSERT INTO sync_queue (name, specs_json, days_of_week, start_time,"
                    " end_time, enabled, on_missed, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, 1, ?, datetime('now','localtime'),"
                    " datetime('now','localtime'))",
                    (name, json.dumps(specs, ensure_ascii=False),
                     str(days or ""), str(start or ""), str(end or ""), on_missed))
                self._conn.commit()
                return int(cur.lastrowid or 0)
            except Exception:
                self._conn.rollback()
                raise

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
