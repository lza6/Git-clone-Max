# -*- coding: utf-8 -*-
"""SQLite 数据库：仓库元数据 + 同步历史快照。断点续传基于 progress.json 单独实现。"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..models import RepoSpec, SyncAction, SyncStatus

_SCHEMA = """
CREATE TABLE IF NOT EXISTS repos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner TEXT NOT NULL,
    repo TEXT NOT NULL,
    folder_name TEXT NOT NULL,
    url TEXT NOT NULL,
    local_path TEXT NOT NULL,
    host TEXT NOT NULL DEFAULT 'github.com',
    default_branch TEXT,
    head_sha TEXT,
    last_sync_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    UNIQUE(owner, repo, host)
);
CREATE TABLE IF NOT EXISTS sync_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_id INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    action TEXT NOT NULL,
    message TEXT,
    detail TEXT,
    commits INTEGER DEFAULT 0,
    head_before TEXT,
    head_after TEXT,
    remote_head TEXT,
    duration_ms INTEGER,
    started_at TEXT,
    ended_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_history_repo ON sync_history(repo_id, started_at DESC);
"""


class Database:
    """线程安全 SQLite 封装。"""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()

    def close(self):
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ 写
    def upsert_repo(self, spec: RepoSpec, local_path: str, host: str = "github.com",
                    default_branch: str | None = None, head_sha: str | None = None) -> int:
        """插入或更新仓库记录，返回 repo_id。"""
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO repos (owner, repo, folder_name, url, local_path, host, default_branch, head_sha, last_sync_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner, repo, host) DO UPDATE SET
                    folder_name=excluded.folder_name,
                    url=excluded.url,
                    local_path=excluded.local_path,
                    default_branch=excluded.default_branch,
                    head_sha=excluded.head_sha,
                    updated_at=datetime('now','localtime')
                """,
                (spec.owner, spec.repo, spec.folder_name, spec.url_https, local_path,
                 host, default_branch, head_sha, time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id FROM repos WHERE owner=? AND repo=? AND host=?",
                (spec.owner, spec.repo, host),
            ).fetchone()
            return int(row["id"])

    def set_repo_head(self, repo_id: int, head_sha: str | None, default_branch: str | None = None):
        with self._lock:
            if default_branch is not None:
                self._conn.execute("UPDATE repos SET default_branch=?, head_sha=?, updated_at=datetime('now','localtime') WHERE id=?",
                                   (default_branch, head_sha, repo_id))
            else:
                self._conn.execute("UPDATE repos SET head_sha=?, updated_at=datetime('now','localtime') WHERE id=?",
                                   (head_sha, repo_id))
            self._conn.commit()

    def add_sync_history(self, repo_id: int, status: SyncStatus, action: SyncAction,
                         message: str = "", detail: str = "", commits: int = 0,
                         head_before: str | None = None, head_after: str | None = None,
                         remote_head: str | None = None, duration_ms: int = 0,
                         started_at: str | None = None, ended_at: str | None = None):
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO sync_history
                    (repo_id, status, action, message, detail, commits, head_before, head_after,
                     remote_head, duration_ms, started_at, ended_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (repo_id, status.value, action.value, message, detail, commits,
                 head_before, head_after, remote_head, duration_ms,
                 started_at or time.strftime("%Y-%m-%d %H:%M:%S"),
                 ended_at or time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            self._conn.commit()

    # ------------------------------------------------------------------ 读
    def get_repo(self, owner: str, repo: str, host: str = "github.com") -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM repos WHERE owner=? AND repo=? AND host=?",
                (owner, repo, host),
            ).fetchone()
            return dict(row) if row else None

    def list_repos(self, host: str = "github.com") -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM repos WHERE host=? ORDER BY owner, repo", (host,)
            ).fetchall()
            return [dict(r) for r in rows]

    def history(self, repo_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sync_history WHERE repo_id=? ORDER BY id DESC LIMIT ?",
                (repo_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    def delete_repo(self, repo_id: int):
        with self._lock:
            self._conn.execute("DELETE FROM sync_history WHERE repo_id=?", (repo_id,))
            self._conn.execute("DELETE FROM repos WHERE id=?", (repo_id,))
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) AS c FROM repos").fetchone()["c"])


# ---------------------------------------------------------------------------
# 断点续传：progress.json（含已完任务记录）
# ---------------------------------------------------------------------------
def load_progress(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"finished": [], "in_progress": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"finished": [], "in_progress": {}}


def save_progress(path: Path, data: Dict[str, Any]):
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)  # 原子写


def mark_finished(path: Path, spec: RepoSpec, result: Dict[str, Any]):
    data = load_progress(path)
    key = f"{spec.owner}/{spec.repo}"
    data["finished"] = [x for x in data.get("finished", []) if x.get("key") != key]
    data["finished"].append({"key": key, **result})
    save_progress(path, data)
