"""写事务上下文（C3-3 / Rev7 C-8）：`BEGIN IMMEDIATE` → 语句 → `commit`，
异常时 **rollback 再上抛**。

**为什么单独成模块**：`repo_db.py` 已贴着 800 行硬上限（守卫
`test_g79_repo_hygiene::TestNoNewOversizedModules` 用集合相等断言，多出第 4 个超限
文件即红）。这与 v4 的 `queue_db.py`、v5 的 `repo_profile.py`、搜索的 `repo_search.py`
是同一范式：**新增能力落新模块，不往热文件里塞**。

**要解决的问题**：`upsert_repo` / `add_sync_history` / `set_repo_head` 原来是
`_begin_immediate(); execute(); commit()`，**没有 try/except-rollback**。
一次失败（锁超时 / 约束冲突 / SQL 错）会把事务悬在那里，后续写入要等
`busy_timeout=30s` 才报错 —— 用户侧只表现为「越用越慢、偶尔卡住」，
而不是一条清晰的错误。

用法（宿主须提供 `_conn` 与 `_begin_immediate()`）：

    with db._lock, write_txn(db):
        db._conn.execute(...)
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any


@contextlib.contextmanager
def write_txn(db: Any) -> Iterator[Any]:
    """见模块文档。``db`` 需有 `_conn`（sqlite3 连接）与 `_begin_immediate()`。"""
    db._begin_immediate()
    try:
        yield db._conn
    except Exception:
        try:
            db._conn.rollback()
        except Exception as e:      # noqa: BLE001 —— 回滚都失败时只留痕，别掩盖原始异常
            from ..app.applog import warn as _warn
            _warn(f"DB 回滚失败（事务可能仍悬挂，后续写入可能卡住）：{e}")
        raise
    else:
        db._conn.commit()
