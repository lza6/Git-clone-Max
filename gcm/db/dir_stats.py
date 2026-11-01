"""目录大小缓存（C3-1 / Rev7 §B3-5）：schema v6。

**为什么落库而不是只放内存**：`dir_size()` 是**递归 `scandir`** —— 实测 15 万文件
484ms、机械盘/杀软下更久。只放内存意味着每次重开应用都要把几百个仓库重新 stat 一遍。
落库后第二次启动直接命中。

**铁律（ADR-001 单写者）**：本模块的方法**只能从 GUI/引擎主线程**调用。
`manage_panel` 的目录统计线程只做纯文件系统遍历（`Database.dir_size` 是静态方法、
不碰连接），算完的结果由 GUI 线程在 `_apply_sizes()` 里一次性批量写回 ——
绝不能让工作线程直接拿 `Database._conn` 写。

**为什么单独成模块**：`repo_db.py` 贴着 800 行硬上限（守卫用集合相等断言），
与 v4 的 `queue_db.py`、v5 的 `repo_profile.py` 同一范式。
"""
from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from typing import Any


def migrate_to_v6(conn: sqlite3.Connection) -> None:
    """schema v5 → v6：新增 `dir_stats` 表（目录大小缓存）。

    与 v4/v5 一致：**只新增表**，不动既有列，旧库升级幂等安全。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS dir_stats (
            path       TEXT PRIMARY KEY,
            size       INTEGER NOT NULL,
            updated_at REAL NOT NULL
        )
        """
    )


class DirStatsMixin:
    """`dir_stats` 的读写。宿主类须提供 `_lock` 与 `_conn`。"""

    #: 缓存有效期默认 30 分钟：仓库大小变化很慢，且用户手动刷新可清缓存。
    DIR_STATS_TTL_SEC = 1800.0

    _lock: Any
    _conn: Any

    def get_dir_sizes(self, paths: Iterable[str],
                      max_age_sec: float | None = None) -> dict[str, int]:
        """批量取未过期的目录大小（一次查询，避免 N 次往返）。"""
        want = [str(p) for p in paths or () if p]
        if not want:
            return {}
        ttl = self.DIR_STATS_TTL_SEC if max_age_sec is None else float(max_age_sec)
        cutoff = time.time() - ttl
        out: dict[str, int] = {}
        try:
            with self._lock:
                # SQLite 的参数个数上限很保守（旧版 999），分批查
                for i in range(0, len(want), 500):
                    chunk = want[i:i + 500]
                    marks = ",".join("?" * len(chunk))
                    rows = self._conn.execute(
                        f"SELECT path, size FROM dir_stats"          # noqa: S608 —— 占位符是问号
                        f" WHERE updated_at >= ? AND path IN ({marks})",
                        (cutoff, *chunk)).fetchall()
                    for r in rows:
                        out[str(r["path"])] = int(r["size"])
        except Exception as e:      # noqa: BLE001 —— 缓存读失败按未命中处理，但留痕
            from ..app.applog import warn as _warn
            _warn(f"读取目录大小缓存失败（按未命中处理）：{e}")
        return out

    def set_dir_sizes(self, items: Iterable[tuple[str, int]]) -> int:
        """批量写回目录大小（GUI 线程调用）。返回写入条数。"""
        rows = [(str(p), int(s), time.time()) for p, s in (items or ()) if p]
        if not rows:
            return 0
        from .txn import write_txn
        with self._lock, write_txn(self):
            self._conn.executemany(
                "INSERT INTO dir_stats (path, size, updated_at) VALUES (?,?,?)"
                " ON CONFLICT(path) DO UPDATE SET"
                " size=excluded.size, updated_at=excluded.updated_at",
                rows)
        return len(rows)

    def clear_dir_sizes(self) -> int:
        """清空缓存（「刷新」时调用）。返回删除条数。"""
        from .txn import write_txn
        with self._lock, write_txn(self):
            cur = self._conn.execute("DELETE FROM dir_stats")
            return int(cur.rowcount or 0)
