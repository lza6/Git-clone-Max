"""`sync_history` 容量治理（C3-2 / Rev7 C-9）：保留期 + 每仓条数上限。

**为什么单独成模块**：`repo_db.py` 贴着 800 行硬上限（守卫用集合相等断言）。
与 v4 的 `queue_db.py`、v5 的 `repo_profile.py`、v6 的 `dir_stats.py` 同一范式。

**调用时机（重要）**：`prune_history` 是对全表跑的窗口函数 —— 实测 10 万行 **238ms**。
因此它**不能在每插入 100 条历史时触发**（原来就是这样，发生在 GUI 线程的
`_on_result` 里，挂机几周后每 100 个结果卡一帧）。现在只在**批次收尾**调一次
（`prune_history_if_due`），且仍在引擎主线程（ADR-001 单写者，绝不换线程）。
"""
from __future__ import annotations

from typing import Any

#: 每个仓库最多保留的历史条数（超出按 id 倒序丢最旧）。
PER_REPO_KEEP = 200
#: 保留天数的默认值与钳制区间。
DEFAULT_RETENTION_DAYS = 90
MIN_RETENTION_DAYS = 1
MAX_RETENTION_DAYS = 3650


class HistoryPruneMixin:
    """宿主类须提供 `_lock` 与 `_conn`，并初始化 `_prune_counter` / `_history_retention_days`。"""

    _lock: Any
    _conn: Any
    #: 由宿主 `Database.__init__` 初始化（此处声明类型，供 mypy 认）
    _prune_counter: int
    _history_retention_days: int

    def prune_history_if_due(self) -> int:
        """批次收尾时调用：按配置保留期做一次容量治理。返回删除行数。"""
        try:
            return int(self._maybe_prune_history(force=True))
        except Exception as e:      # noqa: BLE001 —— 治理失败不该影响批次结果
            from ..app.applog import warn as _warn
            _warn(f"同步历史容量治理失败（跳过本次）：{e}")
            return 0

    def _maybe_prune_history(self, force: bool = False) -> int:
        """按计数器节奏触发治理（`force=True` 时立即执行）。

        历史调用方（每 100 条）已移除，保留本方法是因为**测试与将来的批量导入**
        仍可能需要"立刻治理一次"的入口。
        """
        self._prune_counter += 1
        if not (force or self._prune_counter % 100 == 0):
            return 0
        n = self.prune_history(self._history_retention_days)
        self._prune_counter = 0
        return n

    def set_history_retention_days(self, days: int) -> None:
        """G53-4 设置历史保留天数（1-3650，默认 90）。"""
        try:
            self._history_retention_days = max(
                MIN_RETENTION_DAYS, min(MAX_RETENTION_DAYS, int(days)))
        except (TypeError, ValueError):
            self._history_retention_days = DEFAULT_RETENTION_DAYS

    def get_history_retention_days(self) -> int:
        """G53-4 当前历史保留天数。"""
        return self._history_retention_days

    def prune_history(self, retention_days: int | None = None) -> int:
        """G53-4 按保留天数清理 `sync_history`；删除超窗口**且**超每仓上限的记录。"""
        if retention_days is None:
            retention_days = self._history_retention_days
        try:
            days = max(MIN_RETENTION_DAYS,
                       min(MAX_RETENTION_DAYS, int(retention_days)))
        except (TypeError, ValueError):
            days = DEFAULT_RETENTION_DAYS
        with self._lock:
            cur = self._conn.execute(
                """
                DELETE FROM sync_history WHERE id NOT IN (
                    SELECT id FROM (
                        SELECT id, ROW_NUMBER() OVER (PARTITION BY repo_id ORDER BY id DESC) AS rn
                        FROM sync_history
                    ) WHERE rn <= ?
                ) OR started_at < datetime('now', 'localtime', ?)
                """,
                (PER_REPO_KEEP, f"-{days} days"),
            )
            self._conn.commit()
            self._prune_counter = 0
            return cur.rowcount
