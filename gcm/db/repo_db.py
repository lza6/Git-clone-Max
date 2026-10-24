"""SQLite 数据库：仓库元数据 + 同步历史快照。断点续传基于 progress.json 单独实现。"""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

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
_SCHEMA_VERSION = 3


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
    if v < 2:
        # v2：repos 增加 备注列（G37-6 每仓库备注）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(repos)")]
        if "note" not in cols:
            conn.execute("ALTER TABLE repos ADD COLUMN note TEXT DEFAULT ''")
    if v < 3:
        # v3：repos 增加 ssh_key 列（G48-3 指定 SSH key）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(repos)")]
        if "ssh_key" not in cols:
            conn.execute("ALTER TABLE repos ADD COLUMN ssh_key TEXT DEFAULT ''")
    conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
    conn.commit()


class Database:
    """线程安全 SQLite 封装。"""

    def __init__(self, db_path: str | Path, readonly: bool = False):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        if readonly:
            # G70：只读快路径（HTTP API 读端点用）——跳过建表/迁移写事务。
            # 每次读请求都跑 executescript(_SCHEMA)+BEGIN IMMEDIATE 会让
            # 30 并发读被串行化（实测 p50 ~7s）；mode=ro 连接无写锁，实测降到毫秒级。
            uri = f"file:{self.path.as_posix()}?mode=ro"
            self._conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS};")  # 高并发读不立刻抛锁错误
            self._conn.execute("PRAGMA foreign_keys=ON;")
            # 旧库/空库不能走只读快路径：缺表或缺少 v1 之后的列时，
            # 调用方应回退到可写连接执行建表/迁移，而不是 500。
            try:
                self._conn.execute("SELECT favorite FROM repos LIMIT 1")
            except sqlite3.OperationalError:
                self._conn.close()
                raise
        else:
            self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS};")  # 高并发写不立刻抛锁错误
            with self._lock:
                self._conn.executescript(_SCHEMA)
                _migrate(self._conn)
                self._conn.commit()
        # G33-5：sync_history 容量治理计数器（每 100 次 add_sync_history 自动清理一次）
        self._prune_counter = 0
        self._history_retention_days = 90  # G53-4 历史保留天数（默认 90，可配）

    def close(self):
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ 写
    def _begin_immediate(self):
        """G22-3：写事务显式 BEGIN IMMEDIATE，避免 WAL 下共享锁升级死锁。"""
        try:
            self._conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            pass  # 已在事务中（例如隐式开启）：沿用原语义

    @staticmethod
    def _strip_url_creds(url: str) -> str:
        """入库前剥掉 URL 内嵌凭据（G69-11）。

        **这是凭证外泄的单一收口点**：本地扫描会把仓库 `.git/config` 里的 remote
        原样带出来（`https://ghp_xxx@github.com/team/private.git`），若不拦，
        它会同时进入 ① 数据库 ② 元数据导出 JSON（该文件定位就是「便于分享」）
        ③ 详情页「复制克隆命令」的剪贴板。在**写库**这一处剥掉，
        读库的这三条下游就全都干净了（读库方无需各自记得打码）。

        唯一例外是 UI 上直接展示**扫描结果**（不经库）的列表项，那里单独打码。
        """
        try:
            from ..app.url_lib import strip_embedded_credentials
            clean, _had = strip_embedded_credentials(url or "")
            return clean
        except Exception:
            return url or ""

    def upsert_repo(self, spec: RepoSpec, local_path: str, host: str = "github.com",
                    default_branch: str | None = None, head_sha: str | None = None) -> int:
        """插入或更新仓库记录，返回 repo_id。"""
        with self._lock:
            self._begin_immediate()
            self._conn.execute(
                """
                INSERT INTO repos
                    (owner, repo, folder_name, url, local_path, host,
                     default_branch, head_sha, last_sync_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(owner, repo, host) DO UPDATE SET
                    folder_name=excluded.folder_name,
                    url=excluded.url,
                    local_path=excluded.local_path,
                    default_branch=excluded.default_branch,
                    head_sha=excluded.head_sha,
                    updated_at=datetime('now','localtime')
                """,
                (spec.owner, spec.repo, spec.folder_name,
                 self._strip_url_creds(spec.url_https),   # G69-11：入库即剥凭据
                 local_path, host, default_branch, head_sha,
                 time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT id FROM repos WHERE owner=? AND repo=? AND host=?",
                (spec.owner, spec.repo, host),
            ).fetchone()
            return int(row["id"])

    def bulk_upsert_repos(self, items) -> int:
        """批量 upsert（单事务 executemany），返回写入条数（P-perf：本地扫描导入提速）。

        items: iterable of (spec, local_path, host, head_sha)。
        语义与 upsert_repo 一致（owner+repo+host 冲突则更新）；单条失败整体回滚。
        """
        items = list(items)
        if not items:
            return 0
        rows = [
            (spec.owner, spec.repo, spec.folder_name,
             self._strip_url_creds(spec.url_https),   # G69-11：入库即剥凭据
             local_path, host or "local", None, head_sha,
             time.strftime("%Y-%m-%d %H:%M:%S"))
            for (spec, local_path, host, head_sha) in items
        ]
        with self._lock:
            self._begin_immediate()
            try:
                self._conn.executemany(
                    """
                    INSERT INTO repos
                        (owner, repo, folder_name, url, local_path, host,
                         default_branch, head_sha, last_sync_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(owner, repo, host) DO UPDATE SET
                        folder_name=excluded.folder_name,
                        url=excluded.url,
                        local_path=excluded.local_path,
                        default_branch=excluded.default_branch,
                        head_sha=excluded.head_sha,
                        updated_at=datetime('now','localtime')
                    """,
                    rows,
                )
                self._conn.commit()
            except Exception:
                try:
                    self._conn.rollback()
                except Exception:
                    pass
                raise
            return len(rows)

    def set_repo_head(self, repo_id: int, head_sha: str | None, default_branch: str | None = None):
        with self._lock:
            self._begin_immediate()
            if default_branch is not None:
                self._conn.execute(
                    "UPDATE repos SET default_branch=?, head_sha=?,"
                    " updated_at=datetime('now','localtime') WHERE id=?",
                    (default_branch, head_sha, repo_id))
            else:
                self._conn.execute(
                    "UPDATE repos SET head_sha=?,"
                    " updated_at=datetime('now','localtime') WHERE id=?",
                    (head_sha, repo_id))
            self._conn.commit()

    # ------------------------------------------------------------ G03 标签/收藏/黑名单
    def set_tags(self, repo_id: int, tags: list[str]) -> None:
        """覆盖式设置标签（竖线分隔存储）。"""
        val = "|".join(str(t).strip() for t in tags if str(t).strip())
        with self._lock:
            self._begin_immediate()
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
            self._begin_immediate()
            self._conn.execute("UPDATE repos SET favorite=? WHERE id=?",
                               (1 if fav else 0, repo_id))
            self._conn.commit()

    def set_note(self, repo_id: int, note: str) -> None:
        """G37-6 设置仓库备注（覆盖式）。"""
        with self._lock:
            self._begin_immediate()
            self._conn.execute(
                "UPDATE repos SET note=?, updated_at=datetime('now','localtime') WHERE id=?",
                (str(note or ""), repo_id))
            self._conn.commit()

    def set_excluded(self, repo_id: int, excluded: bool) -> None:
        with self._lock:
            self._begin_immediate()
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
            self._begin_immediate()
            self._conn.execute(
                """
                INSERT INTO sync_history
                    (repo_id, status, action, message, detail, commits, head_before,
                     head_after, remote_head, duration_ms, started_at, ended_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (repo_id, status.value, action.value, message, detail, commits,
                 head_before, head_after, remote_head, duration_ms,
                 started_at or time.strftime("%Y-%m-%d %H:%M:%S"),
                 ended_at or time.strftime("%Y-%m-%d %H:%M:%S")),
            )
            self._conn.commit()
            # G33-5：插入后按计数器节奏触发容量清理
            self._maybe_prune_history()

    # ------------------------------------------------------------ G33-5 容量治理
    def _maybe_prune_history(self, force: bool = False) -> int:
        """sync_history 容量治理：每仓保留 200 条 + 可配保留天数（G53-4）。"""
        self._prune_counter += 1
        if not (force or self._prune_counter % 100 == 0):
            return 0
        n = self.prune_history(self._history_retention_days)
        self._prune_counter = 0
        return n
    def set_history_retention_days(self, days: int) -> None:
        """G53-4 设置历史保留天数（1-3650，默认 90）。"""
        try:
            self._history_retention_days = max(1, min(3650, int(days)))
        except (TypeError, ValueError):
            self._history_retention_days = 90

    def get_history_retention_days(self) -> int:
        """G53-4 当前历史保留天数。"""
        return self._history_retention_days

    def prune_history(self, retention_days: int | None = None) -> int:
        """G53-4 按保留天数清理 sync_history；删除超窗口且超 200 行/仓的记录。"""
        if retention_days is None:
            retention_days = self._history_retention_days
        try:
            days = max(1, min(3650, int(retention_days)))
        except (TypeError, ValueError):
            days = 90
        with self._lock:
            cur = self._conn.execute(
                """
                DELETE FROM sync_history WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (PARTITION BY repo_id ORDER BY id DESC) AS rn
                        FROM sync_history
                    ) WHERE rn <= 200
                ) OR started_at < datetime('now', 'localtime', ?)
                """
                ,
                (f"-{days} days",),
            )
            self._conn.commit()
            self._prune_counter = 0
            return cur.rowcount

    # ------------------------------------------------------------------ 读

    # ------------------------------------------------------------------ 读
    def get_repo(self, owner: str, repo: str,
                 host: str | None = "github.com") -> Optional[dict[str, Any]]:
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

    def list_repos(self, host: str | None = None) -> list[dict[str, Any]]:
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

    def list_repos_page(self, host: str | None = None, limit: int = 500,
                        offset: int = 0) -> tuple[list[dict[str, Any]], int]:
        """G70：分页查询（HTTP API 用）——SQL LIMIT/OFFSET，返回 (rows, total)。"""
        limit = max(1, int(limit))
        offset = max(0, int(offset))
        with self._lock:
            if host is None:
                total = int(self._conn.execute(
                    "SELECT COUNT(*) FROM repos").fetchone()[0])
                rows = self._conn.execute(
                    "SELECT * FROM repos ORDER BY favorite DESC, owner, repo "
                    "LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            else:
                total = int(self._conn.execute(
                    "SELECT COUNT(*) FROM repos WHERE host=?", (host,)).fetchone()[0])
                rows = self._conn.execute(
                    "SELECT * FROM repos WHERE host=? ORDER BY favorite DESC, owner, repo "
                    "LIMIT ? OFFSET ?", (host, limit, offset)).fetchall()
            return ([dict(r) for r in rows], total)

    def history(self, repo_id: int, limit: int = 20) -> list[dict[str, Any]]:
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

    def stats_usage(self) -> dict:
        """G37-5 用量统计：累计克隆/更新次数与新增提交数（按 action 聚合）。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    SUM(CASE WHEN action='cloned' THEN 1 ELSE 0 END) AS clones,
                    SUM(CASE WHEN action='updated' THEN 1 ELSE 0 END) AS updates,
                    SUM(CASE WHEN action='updated' THEN commits ELSE 0 END) AS commits
                FROM sync_history
                """
            ).fetchone()
            return {
                "clones": int(row["clones"] or 0),
                "updates": int(row["updates"] or 0),
                "commits": int(row["commits"] or 0),
            }

    def stats_daily(self, days: int = 30) -> list[dict]:
        """最近 N 天每日同步聚合（成功/失败/冲突三序列），用于趋势图。

        返回 [{"date": "YYYY-MM-DD", "success": n, "failed": n, "conflict": n}, ...]，
        日期升序；无数据的天补零（G37-1 趋势展示用）。
        """
        days = max(1, min(int(days), 90))
        import datetime as _dt
        today = _dt.date.today()
        start = today - _dt.timedelta(days=days - 1)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT substr(started_at, 1, 10) AS day,
                       SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) AS ok,
                       SUM(CASE WHEN status='failed'  THEN 1 ELSE 0 END) AS bad,
                       SUM(CASE WHEN status='conflict' THEN 1 ELSE 0 END) AS conf
                FROM sync_history
                WHERE started_at >= ?
                GROUP BY day
                """,
                (start.strftime("%Y-%m-%d") + " 00:00:00",),
            ).fetchall()
        by_day = {str(r["day"]): r for r in rows}
        out = []
        for i in range(days):
            d = (start + _dt.timedelta(days=i)).strftime("%Y-%m-%d")
            r = by_day.get(d)
            out.append({
                "date": d,
                "success": int(r["ok"] or 0) if r else 0,
                "failed": int(r["bad"] or 0) if r else 0,
                "conflict": int(r["conf"] or 0) if r else 0,
            })
        return out

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

    # ------------------------------------------------------------ G43-3 慢仓 TopN
    def stats_slow_repos(self, limit: int = 10) -> list[dict]:
        """G43-3 平均耗时 TopN 慢仓库（帮助定位网络/大仓问题）。

        按仓库聚合 sync_history.duration_ms 平均耗时降序取 TopN；
        返回 [{owner_repo, url, avg_ms, count, last_sync}]；空库返回 []。
        """
        limit = max(1, min(int(limit or 10), 100))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT (r.owner || '/' || r.repo) AS owner_repo,
                       r.url AS url,
                       ROUND(AVG(h.duration_ms), 1) AS avg_ms,
                       COUNT(*) AS count,
                       MAX(h.started_at) AS last_sync
                FROM sync_history h
                JOIN repos r ON r.id = h.repo_id
                WHERE h.duration_ms > 0
                GROUP BY h.repo_id
                ORDER BY avg_ms DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "owner_repo": str(r["owner_repo"] or ""),
                "url": str(r["url"] or ""),
                "avg_ms": float(r["avg_ms"] or 0),
                "count": int(r["count"] or 0),
                "last_sync": str(r["last_sync"] or ""),
            }
            for r in rows
        ]

    # ------------------------------------------------------------ G43-7 WAL 维护
    def wal_checkpoint(self) -> dict:
        """G43-7 执行 PRAGMA wal_checkpoint(PASSIVE)，返回统计。

        返回 {"busy": bool, "log": int, "checkpointed": int}；
        非 WAL/异常时返回 {"busy": True, "log": 0, "checkpointed": 0}，不抛异常。
        """
        try:
            with self._lock:
                row = self._conn.execute("PRAGMA wal_checkpoint(PASSIVE);").fetchone()
            if not row:
                return {"busy": True, "log": 0, "checkpointed": 0}
            # row: (busy, log_frames, checkpointed_frames)
            return {
                "busy": bool(row[0]),
                "log": int(row[1] or 0),
                "checkpointed": int(row[2] or 0),
            }
        except Exception:
            return {"busy": True, "log": 0, "checkpointed": 0}

    def wal_autocheckpoint(self, pages: int = 1000) -> None:
        """G43-7 设置连接级 wal_autocheckpoint（页数阈值，幂等）。"""
        try:
            pages = max(0, int(pages))
            with self._lock:
                self._conn.execute(f"PRAGMA wal_autocheckpoint={pages};")
        except Exception:
            pass

    # ------------------------------------------------------------ G33-3 备份
    def backup_to(self, dest: str | Path) -> int:
        """备份数据库到 dest，返回备份时的仓库总数（self.count()）。

        WAL 模式下直接拷贝主库会漏掉未检查点的数据：这里先在同连接执行
        PRAGMA wal_checkpoint(TRUNCATE) 把 WAL 落盘并截断，commit 后再
        shutil.copy2 拷贝主库文件，保证备份包含全部已提交数据。
        checkpoint 会短暂阻塞其它写者，建议 UI 空闲时调用（内部已加锁门闩）。
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except Exception:
                pass  # 非 WAL / 其它异常：跳过 checkpoint，主库文件本身完整可拷
            self._conn.commit()
            shutil.copy2(self.path, dest)
            return self.count()


# ---------------------------------------------------------------------------


    # ------------------------------------------------------------ G47/G48 数据洞察与深化
    def repo_health(self, repo_id: int) -> int:
        """G47-1 健康度评分 0-5：成功率(近20次,50%) + 陈旧度(25%) + 冲突惩罚(25%)。空历史默认 3。"""
        try:
            hist = self.history(repo_id, 20)
            if not hist:
                return 3
            terms = [h for h in hist if h.get("status")
                     in ("success", "failed", "conflict", "cancelled")]
            if not terms:
                return 3
            n = len(terms)
            ok = sum(1 for h in terms if h.get("status") == "success")
            conflict = sum(1 for h in terms if h.get("status") == "conflict")
            score = 3.0 + 2.0 * (ok / n)
            score -= min(1.0, float(conflict) * 0.5)
            try:
                import datetime as _dt
                now = _dt.datetime.now()
                last = None
                for h in terms:
                    if h.get("ended_at"):
                        try:
                            t = _dt.datetime.strptime(str(h["ended_at"])[:19], "%Y-%m-%d %H:%M:%S")
                            last = max(last or t, t)
                        except Exception:
                            pass
                if last is not None:
                    days = (now - last).days
                    score -= min(1.0, max(0.0, days / 60.0))
            except Exception:
                pass
            return int(max(0, min(5, round(score))))
        except Exception:
            return 3

    @staticmethod
    def dir_size(path) -> int:
        """G47-5 目录总字节数（跳过 reparse point 防 junction 环）。"""
        import os as _os
        from pathlib import Path as _P
        root = _P(path)
        total = 0
        try:
            for entry in _os.scandir(root):
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if getattr(entry, "is_junction", lambda: False)():
                            continue
                        total += Database.dir_size(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        try:
                            total += entry.stat().st_size
                        except Exception:
                            pass
                except Exception:
                    pass
        except Exception:
            return 0
        return total

    def list_repos_tags(self, tags: list[str] | None = None,
                        mode: str = "and") -> list[dict]:
        """G47-6 多标签过滤：AND(默认, 全部命中) / OR(任一命中)。"""
        if not tags:
            return self.list_repos()
        wanted = [str(t).strip() for t in tags if str(t).strip()]
        if not wanted:
            return self.list_repos()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM repos ORDER BY favorite DESC, owner, repo").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            row_tags = {t.strip() for t in str(d.get("tags") or "").split("|")
                        if t.strip()}
            if mode == "or":
                hit = bool(set(wanted) & row_tags)
            else:
                hit = set(wanted) <= row_tags
            if hit:
                out.append(d)
        return out

    def export_metadata_json(self, out_path) -> int:
        """G47-7 导出仓库元数据（tags/favorite/note/excluded + 标识字段）。返回条数。"""
        rows = self.list_repos()
        payload = []
        for r in rows:
            payload.append({
                "owner": r.get("owner"), "repo": r.get("repo"),
                "host": r.get("host", "github.com"),
                "folder_name": r.get("folder_name"), "url": r.get("url"),
                "local_path": r.get("local_path"),
                "tags": r.get("tags", ""), "favorite": int(r.get("favorite") or 0),
                "note": r.get("note", ""), "excluded": int(r.get("excluded") or 0),
            })
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        return len(payload)

    def import_metadata_json(self, in_path) -> int:
        """G47-7 按 owner/repo/host 合并导入元数据（upsert 保留已有字段）。"""
        data = json.loads(Path(in_path).read_text(encoding="utf-8"))
        n = 0
        for item in data:
            owner = str(item.get("owner") or "")
            repo = str(item.get("repo") or "")
            host = str(item.get("host") or "github.com")
            if not owner or not repo:
                continue
            spec = RepoSpec(owner=owner, repo=repo,
                            url_https=str(item.get("url") or ""),
                            folder_name=str(item.get("folder_name") or repo),
                            local_path=str(item.get("local_path") or ""))
            with self._lock:
                self._begin_immediate()
                self._conn.execute(
                    """
                    INSERT INTO repos (owner, repo, folder_name, url, local_path, host,
                                       default_branch, head_sha, last_sync_at)
                    VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
                    ON CONFLICT(owner, repo, host) DO UPDATE SET
                        url=excluded.url, local_path=excluded.local_path,
                        updated_at=datetime('now','localtime')
                    """,
                    (owner, repo, spec.folder_name, spec.url_https, spec.local_path, host))
                rid = self._conn.execute(
                    "SELECT id FROM repos WHERE owner=? AND repo=? AND host=?",
                    (owner, repo, host)).fetchone()["id"]
                self._conn.execute(
                    "UPDATE repos SET tags=?, favorite=?, note=?, excluded=?, "
                    "updated_at=datetime('now','localtime') WHERE id=?",
                    (str(item.get("tags") or ""), int(item.get("favorite") or 0),
                     str(item.get("note") or ""), int(item.get("excluded") or 0), rid))
                self._conn.commit()
            n += 1
        return n

    # ------------------------------------------------------------ G48-3 指定 SSH key
    def get_repo_ssh_key(self, owner: str, repo: str, host: str = "github.com") -> str:
        """读仓库级 SSH 私钥**路径**（空串 = 用全局 `settings.ssh_key_path`）。

        G80-B8：`repos.ssh_key` 此前只有写入方法没有读取点（死列）。这里补上唯一读取入口，
        由引擎在每个任务启动时查一次并覆盖全局配置（per-repo > global）。
        只返回路径，绝不返回私钥内容（库里也不存内容）。
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT ssh_key FROM repos WHERE owner=? AND repo=? AND host=?",
                (str(owner or ""), str(repo or ""), str(host or "github.com")),
            ).fetchone()
        if row is None:
            return ""
        try:
            return str(row["ssh_key"] or "")
        except (IndexError, KeyError, TypeError):
            return ""

    def set_ssh_key(self, repo_id: int, key_path: str) -> None:
        """G48-3：为仓库指定 SSH key（clone/fetch 时经 GIT_SSH_COMMAND 注入）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE repos SET ssh_key=?, updated_at=datetime('now','localtime') WHERE id=?",
                (str(key_path or ""), repo_id))
            self._conn.commit()


# ---------------------------------------------------------------------------
# 断点续传：progress.json（含已完任务记录）
#
# G67-14：实现已拆到 `gcm/db/progress_file.py`（与 SQLite 无关的纯文件持久化，
# 混在 DB 模块里会撑破 800 行上限，还逼出「模块中部 import」的变通写法）。
# 这里**原样再导出**，engine / worker / UI / 测试的既有导入路径全部不变。
from .progress_file import (  # noqa: E402,F401  （再导出，保持既有导入路径不变）
    _progress_guard,
    load_progress,
    mark_finished,
    save_progress,
)
