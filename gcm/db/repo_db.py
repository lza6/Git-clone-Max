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


_BUSY_TIMEOUT_MS = 30000  # SQLite 写锁竞争等待上限，避免高并发直接抛 database is locked

# 数据库 schema 版本（PRAGMA user_version）。每次结构变更，递增此值并补一段迁移。
_SCHEMA_VERSION = 1


def _migrate(conn):
    """把 conn 连接的数据库从当前 user_version 迁移到 _SCHEMA_VERSION。

    迁移在 'CREATE TABLE IF NOT EXISTS'（保证表存在）之后、业务访问之前调用。
    幂等：user_version 已达标则直接返回。
    """
    v = conn.execute("PRAGMA user_version").fetchone()[0]
    if v >= _SCHEMA_VERSION:
        return
    if v < 1:
        # v1：repos 增加 标签/收藏/排除 三类扩展列（G06-6 迁移基线）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(repos)")]
        if "tags" not in cols:
            conn.execute("ALTER TABLE repos ADD COLUMN tags TEXT DEFAULT ''")
        if "favorite" not in cols:
            conn.execute("ALTER TABLE repos ADD COLUMN favorite INTEGER DEFAULT 0")
        if "excluded" not in cols:
            conn.execute("ALTER TABLE repos ADD COLUMN excluded INTEGER DEFAULT 0")
    conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
    conn.commit()


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
        self._conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS};")  # 高并发写不立刻抛锁错误
        with self._lock:
            self._conn.executescript(_SCHEMA)
            _migrate(self._conn)
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

    # ------------------------------------------------------------ G03 标签/收藏/黑名单
    def set_tags(self, repo_id: int, tags: list[str]) -> None:
        """覆盖式设置标签（竖线分隔存储）。"""
        val = "|".join(str(t).strip() for t in tags if str(t).strip())
        with self._lock:
            self._conn.execute("UPDATE repos SET tags=? WHERE id=?", (val, repo_id))
            self._conn.commit()

    def list_by_tag(self, tag: str) -> list[dict]:
        """按标签精确匹配（tags 字段含该标签段）。"""
        tag = (tag or "").strip()
        with self._lock:
            if not tag:
                return []
            rows = self._conn.execute(
                "SELECT * FROM repos WHERE tags=? OR tags LIKE ? OR tags LIKE ? OR tags LIKE ?",
                (tag, f"{tag}|%", f"%|{tag}|%", f"%|{tag}"),
            ).fetchall()
            return [dict(r) for r in rows]

    def set_favorite(self, repo_id: int, fav: bool) -> None:
        with self._lock:
            self._conn.execute("UPDATE repos SET favorite=? WHERE id=?",
                               (1 if fav else 0, repo_id))
            self._conn.commit()

    def set_excluded(self, repo_id: int, excluded: bool) -> None:
        with self._lock:
            self._conn.execute("UPDATE repos SET excluded=? WHERE id=?",
                               (1 if excluded else 0, repo_id))
            self._conn.commit()

    def list_pending_updates(self) -> list[dict]:
        """待更新仓库（排除黑名单 excluded=1）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM repos WHERE excluded=0 ORDER BY favorite DESC, owner, repo"
            ).fetchall()
            return [dict(r) for r in rows]

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
    def get_repo(self, owner: str, repo: str, host: str | None = "github.com") -> Optional[Dict[str, Any]]:
        with self._lock:
            if host is None:
                rows = self._conn.execute(
                    "SELECT * FROM repos WHERE owner=? AND repo=? ORDER BY id DESC LIMIT 1",
                    (owner, repo),
                ).fetchall()
                return dict(rows[0]) if rows else None
            row = self._conn.execute(
                "SELECT * FROM repos WHERE owner=? AND repo=? AND host=?",
                (owner, repo, host),
            ).fetchone()
            return dict(row) if row else None

    def list_repos(self, host: str | None = None) -> List[Dict[str, Any]]:
        """列出仓库；host 为 None 时返回全部（含本地导入的仓库）。收藏优先。"""
        with self._lock:
            if host is None:
                rows = self._conn.execute(
                    "SELECT * FROM repos ORDER BY favorite DESC, owner, repo").fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM repos WHERE host=? ORDER BY favorite DESC, owner, repo",
                    (host,)).fetchall()
            return [dict(r) for r in rows]

    def history(self, repo_id: int, limit: int = 20) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM sync_history WHERE repo_id=? ORDER BY id DESC LIMIT ?",
                (repo_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------ G03-8 / G07-1 统计
    def stats_for_repo(self, repo_id: int) -> dict:
        """单仓库同步聚合：次数/成功/失败/冲突/取消/平均耗时。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS success,
                       SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN status='conflict' THEN 1 ELSE 0 END) AS conflict,
                       SUM(CASE WHEN status='cancelled' THEN 1 ELSE 0 END) AS cancelled,
                       AVG(duration_ms) AS avg_ms
                FROM sync_history WHERE repo_id=?
                """,
                (repo_id,),
            ).fetchone()
            total = int(row["total"] or 0)
            return {
                "total": total,
                "success": int(row["success"] or 0),
                "failed": int(row["failed"] or 0),
                "conflict": int(row["conflict"] or 0),
                "cancelled": int(row["cancelled"] or 0),
                "avg_duration_ms": int(row["avg_ms"] or 0) if total else 0,
            }

    def stats_overview(self) -> dict:
        """全局统计：总仓库/总同步/成功失败/各 host 分布。"""
        with self._lock:
            total_repos = int(self._conn.execute(
                "SELECT COUNT(*) AS c FROM repos").fetchone()["c"] or 0)
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok,
                       SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END) AS bad
                FROM sync_history
                """
            ).fetchone()
            by_host_rows = self._conn.execute(
                "SELECT host, COUNT(*) AS c FROM repos GROUP BY host ORDER BY c DESC"
            ).fetchall()
            return {
                "total_repos": total_repos,
                "total_syncs": int(row["total"] or 0),
                "success_syncs": int(row["ok"] or 0),
                "failed_syncs": int(row["bad"] or 0),
                "by_host": {str(r["host"]): int(r["c"]) for r in by_host_rows},
            }

    def delete_repo(self, repo_id: int):
        with self._lock:
            self._conn.execute("DELETE FROM sync_history WHERE repo_id=?", (repo_id,))
            self._conn.execute("DELETE FROM repos WHERE id=?", (repo_id,))
            self._conn.commit()

    def delete_all_repos(self):
        """清空全部仓库记录（含历史）。供测试/重置使用。"""
        with self._lock:
            self._conn.execute("DELETE FROM sync_history")
            self._conn.execute("DELETE FROM repos")
            self._conn.commit()

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) AS c FROM repos").fetchone()["c"])


# ---------------------------------------------------------------------------
# 断点续传：progress.json（含已完任务记录）
# ---------------------------------------------------------------------------
# progress.json 的读写需要互斥：多 worker 并发（或未来多实例）写同一 tmp 路径
# 会导致 PermissionError（Windows）。这里提供线程级 + 进程级双层锁。
import contextlib as _contextlib

_progress_lock = threading.RLock()


@_contextlib.contextmanager
def _progress_guard(path: Path):
    """线程锁 + 进程锁双层互斥，保护对 progress.json 的读改写。

    进程锁：Windows msvcrt（每句柄独占锁）/ POSIX fcntl.flock。
    同进程多线程 → RLock 串行；多进程 → 文件锁串行。锁失败降级为仅线程锁。
    """
    with _progress_lock:
        lock_fh = None
        try:
            if os.name == "nt":
                import msvcrt
                lock_fh = open(str(path) + ".lock", "a+")
                msvcrt.locking(lock_fh.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl
                lock_fh = open(str(path) + ".lock", "a+")
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        except Exception:
            lock_fh = None  # 锁获取失败 → 降级为仅线程锁
        try:
            yield
        finally:
            if lock_fh is not None:
                try:
                    if os.name == "nt":
                        import msvcrt
                        lock_fh.seek(0)
                        msvcrt.locking(lock_fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                try:
                    lock_fh.close()
                except Exception:
                    pass


def load_progress(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"finished": [], "in_progress": {}}
    try:
        with _progress_guard(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception:
        return {"finished": [], "in_progress": {}}


def save_progress(path: Path, data: Dict[str, Any]):
    tmp = path.with_suffix(".json.tmp")
    with _progress_guard(path):
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)  # 原子写


def mark_finished(path: Path, spec: RepoSpec, result: Dict[str, Any]):
    data = load_progress(path)
    key = f"{spec.owner}/{spec.repo}"
    data["finished"] = [x for x in data.get("finished", []) if x.get("key") != key]
    data["finished"].append({"key": key, **result})
    save_progress(path, data)
