"""仓库画像的读写（G81 / N-6）。

为什么抽成 mixin：`repo_db.py` 已贴着 800 行红线（`test_g79_repo_hygiene` 的
超限集合守卫会红），再往里加访问器就成了第 4 个超限模块。这里沿用
`repo_ssh.py` 的既有范式：`Database` 继承本 mixin，对外 API 是
`db.set_repo_profile(...)` / `db.get_repo_profile(...)`，repo_db.py 不涨。

存储形态：`repos.profile_json` 一列存 JSON（`RepoProfile.to_json()`）。
刻意不拆成 lang/kind/summary 三列：
- 画像字段会继续长（未来可能加 stars_of_tech / license / 依赖清单）；
  每加一个字段就迁移一次 schema 的代价，远高于「一列 JSON 整体替换」；
- 画像**不是**查询条件的主体（当前没有 `WHERE lang=` 的需求）。真要做
  「按语言检索」，正确做法是 FTS/派生索引表，而不是给主表加列 ——
  那件事在 `docs/plans/下一步改进指南-*.md` 的 B4-1 里有独立规划，
  本模块不预占。

边界：**只读本机目录、只写本表**，绝不联网、不调 LLM（见 `app/knowledge.py` 顶部）。
"""
from __future__ import annotations

import sqlite3
from typing import Any

from ..app.knowledge import RepoProfile


def migrate_to_v5(conn: sqlite3.Connection) -> None:
    """schema v4 → v5：`repos` 加 `profile_json` 列（G81 / N-6）。

    幂等：列已存在就跳过（旧库升级 / 重复迁移都安全）。
    空串 = 尚未探测（老数据），不是"没有语言"——`RepoProfile.from_json('')`
    返回空画像，UI 显示「尚未探测」而不是「未知语言」。

    为什么放在本模块而不是 `repo_db.py`：`repo_db.py` 已贴着 800 行红线
    （守卫 `test_g79_repo_hygiene` 用集合相等断言，多出第 4 个超限文件即红）。
    这与 v4 把建表放在 `queue_db.py`、SSH 访问器放在 `repo_ssh.py` 是同一范式。
    """
    cols = [r[1] for r in conn.execute("PRAGMA table_info(repos)")]
    if "profile_json" not in cols:
        conn.execute("ALTER TABLE repos ADD COLUMN profile_json TEXT DEFAULT ''")


class RepoProfileMixin:
    """`repos.profile_json` 的读写。宿主类须提供 `_lock` 与 `_conn`。"""

    _lock: Any
    _conn: Any

    def set_repo_profile(self, repo_id: int, profile: RepoProfile) -> None:
        """整列替换一个仓库的画像（探测结果）。"""
        with self._lock:
            self._conn.execute(
                "UPDATE repos SET profile_json=?,"
                " updated_at=datetime('now','localtime') WHERE id=?",
                (profile.to_json(), int(repo_id)))
            self._conn.commit()

    def get_repo_profile(self, owner: str, repo: str,
                         host: str = "github.com") -> RepoProfile:
        """按 owner/repo/host 读画像；不存在或 JSON 损坏一律返回空画像（绝不抛）。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT profile_json FROM repos"
                " WHERE owner=? AND repo=? AND host=?",
                (str(owner or ""), str(repo or ""), str(host or "github.com")),
            ).fetchone()
        if row is None:
            return RepoProfile()
        try:
            return RepoProfile.from_json(row["profile_json"])
        except (IndexError, KeyError, TypeError):
            return RepoProfile()

    def list_repo_langs(self, limit: int = 20) -> list[tuple[str, int]]:
        """全库语言分布（按仓库数降序）。供统计页「我的仓库都是什么语言」用。

        只做聚合展示，不参与筛选；筛选请另走派生索引（见模块 docstring）。
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT profile_json FROM repos WHERE profile_json IS NOT NULL"
                " AND profile_json != ''").fetchall()
        counter: dict[str, int] = {}
        for row in rows:
            try:
                prof = RepoProfile.from_json(row["profile_json"])
            except (IndexError, KeyError, TypeError):
                continue
            if prof.lang:
                counter[prof.lang] = counter.get(prof.lang, 0) + 1
        ordered = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))
        return ordered[:max(1, int(limit))]
