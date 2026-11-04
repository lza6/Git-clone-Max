"""一致性备份（G33-3 起，G84-10 强化）：`BackupMixin`。

**为什么单独成模块**：`repo_db.py` 贴着 800 行硬上限（守卫
`test_g79_repo_hygiene::TestNoNewOversizedModules` 用集合相等断言，多出第 4 个超限
文件即红）。G84 给备份补原子写 + `quick_check` 时，把它抽出来 —— 与 v4 的
`queue_db.py`、v5 的 `repo_profile.py`、v6 的 `dir_stats.py`、C3 的 `txn.py`
是同一范式。

**要解决的问题**（两层）：WAL 模式下直接拷主库会漏掉未检查点的数据；而"先拷再说"
一旦中途失败，`dest` 会是一个**名字正常、内容残缺**的 db 文件 —— 用户拿去恢复才
发现坏了，比没有备份更危险。所以：checkpoint → 拷到 tmp → fsync →
**`quick_check` 校验 tmp** → 原子替换（校验在替换之前）。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from ..app.applog import warn as _warn


class BackupMixin:
    """宿主类须提供 `_lock`、`_conn`、`path`、`count()`。"""

    _lock: Any
    _conn: Any
    path: Path

    def count(self) -> int:      # pragma: no cover - 由宿主实现（仅类型声明）
        raise NotImplementedError

    def backup_to(self, dest: str | Path, *, verify: bool = True) -> int:
        """备份数据库到 dest，返回备份时的仓库总数。

        步骤：`wal_checkpoint(TRUNCATE)`（把 WAL 落盘并截断）→ `tmp` → `fsync`
        → `os.replace` → `quick_check`。要么 dest 是上一份完整备份，要么是这一份
        新的完整备份，不存在"半个"的中间态。
        """
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except Exception as e:      # noqa: BLE001 —— 非 WAL 也要能备份
                _warn(f"备份前 wal_checkpoint 失败（按非 WAL 处理，继续备份）：{e}")
            self._conn.commit()
            tmp = dest.with_name(f".{dest.name}.{os.getpid()}.tmp")
            try:
                shutil.copy2(self.path, tmp)
                try:
                    with open(tmp, "rb+") as fh:      # 内容先落盘，再换目录项
                        os.fsync(fh.fileno())
                except OSError as e:                  # 部分文件系统不支持 fsync
                    _warn(f"备份文件 fsync 失败（继续，但掉电可能丢这一份）：{e}")
                # R3（审查发现）：**必须先校验 tmp 再 replace**。原实现先换掉 dest
                # 再 quick_check，校验失败时上一份好备份已经被覆盖掉了 ——
                # 与 docstring 承诺的"不存在坏中间态"正好相反。
                if verify:
                    self._verify_backup(tmp)
                os.replace(tmp, dest)
            except Exception:
                try:
                    tmp.unlink(missing_ok=True)
                except OSError as e:
                    _warn(f"清理残留 tmp 失败（下次同路径会重试）：{e}")
                raise
            total = self.count()
        return total

    @staticmethod
    def _verify_backup(dest: Path) -> None:
        """备份产物必须能独立打开且 `quick_check` 通过（坏备份比没有备份更危险）。"""
        try:
            conn = sqlite3.connect(str(dest))
            try:
                row = conn.execute("PRAGMA quick_check;").fetchone()
                ok = bool(row) and str(row[0]).lower() == "ok"
            finally:
                conn.close()
        except sqlite3.DatabaseError as e:
            raise RuntimeError(f"备份文件不是可用的 SQLite 库（{e}）：{dest}") from e
        if not ok:
            raise RuntimeError(f"备份校验失败（quick_check 未通过）：{dest}")
