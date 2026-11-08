"""FTS5 全文索引（C4-1/C4-2 / Rev7 §2.1 修正版）：**trigram + LIKE 双路**。

## 为什么不是「纯 FTS5 trigram」（Rev7 §2.1 的实测修正）

实测（SQLite 3.50.4）：`trigram` 分词器要求查询串 **≥3 个 unicode 字符**，
不足 3 字符时 FTS5 规定 **match nothing**：

    trigram MATCH '克隆'   -> []        ← 两字中文词零命中
    trigram MATCH '机器学' -> [(2,)]    ← 三字才命中

而中文用户最常搜的恰恰是两个字（克隆/爬虫/量化/笔记）。所以正确做法是双路：
- `len(q) >= 3` → FTS5 `trigram`（bm25 排序 + 高亮）
- `len(q) <  3` → 现有 `LIKE` 下推（3000 行实测 3.9ms，两字查询完全够用）

## 索引是**派生**的（C4-2）

`repo_fts` 是 `content='repos'` 的**外部内容表** —— 它不存副本，只存索引。
因此它随时可以 `rebuild`（换分词器、修数据、升级都对得上），**DB 里的 `repos` 才是
唯一事实源**。重建时机：启动时若索引为空/行数不匹配、以及每批结束时。

**为什么不加触发器自动同步**：本项目仓库记录由引擎主线程批量写，触发器会在每次
`upsert_repo` 上多一轮索引写入（3000 仓导入时明显变慢）；而搜索是低频操作，
"批次收尾重建一次"已经足够新。
"""
from __future__ import annotations

import sqlite3
from typing import Any

#: 走 FTS 的最小查询长度（trigram 的硬下限，见模块文档）
FTS_MIN_LEN = 3


def migrate_to_v8(conn: sqlite3.Connection) -> None:
    """schema v7 → v8：新增 `repo_fts` 外部内容 FTS5 表（trigram）。

    只建虚表，不动既有列。若当前 SQLite 不带 FTS5/trigram，这里会抛
    `OperationalError` —— 由 `Database` 记日志后**降级为纯 LIKE**（搜索仍可用）。
    """
    conn.execute(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS repo_fts USING fts5(
            owner, repo, folder_name, tags, note,
            content='repos', content_rowid='id',
            tokenize='trigram'
        )
        """
    )


class RepoFtsMixin:
    """`repo_fts` 的维护与查询。宿主类须提供 `_lock` 与 `_conn`。"""

    _lock: Any
    _conn: Any
    #: 索引是否可用（建表失败/FTS5 不可用时由 Database 置 False）
    _fts_ready: bool = True

    # ---------------------------------------------------------------- 维护
    def rebuild_fts(self) -> int:
        """重建全文索引，返回索引行数。失败只 warn（搜索会降级为 LIKE）。"""
        if not getattr(self, "_fts_ready", False):
            return 0
        try:
            with self._lock:
                self._conn.execute("INSERT INTO repo_fts(repo_fts) VALUES('rebuild')")
                self._conn.commit()
                row = self._conn.execute("SELECT count(*) FROM repo_fts").fetchone()
                return int(row[0]) if row else 0
        except Exception as e:      # noqa: BLE001 —— 索引坏掉不该影响主功能
            from ..app.applog import warn as _warn
            _warn(f"全文索引重建失败（搜索将只用 LIKE）：{e}")
            self._fts_ready = False
            return 0

    def fts_row_count(self) -> int:
        if not getattr(self, "_fts_ready", False):
            return 0
        try:
            with self._lock:
                row = self._conn.execute("SELECT count(*) FROM repo_fts").fetchone()
                return int(row[0]) if row else 0
        except Exception:           # noqa: BLE001
            return 0

    def ensure_fts(self) -> int:
        """把全文索引与 `repos` 对齐（重建），返回索引行数（0 = 库里没有仓库/不可用）。

        **为什么是"调用即重建"而不是"先比较行数"**：`repo_fts` 是 `content='repos'`
        的**外部内容表** —— 它不存正文，`SELECT count(*) FROM repo_fts` 会从内容表读，
        于是"索引没建"和"已建好"返回同一个数（实测两者都返回 repos 行数），拿它判过期
        是假信号。重建成本实测很低（3000 行毫秒级），而调用点只有两个
        （应用启动一次、每批结束一次），所以直接对齐最诚实。
        """
        if not getattr(self, "_fts_ready", False):
            return 0
        try:
            with self._lock:
                repos_n = int(self._conn.execute("SELECT count(*) FROM repos").fetchone()[0])
        except Exception as e:      # noqa: BLE001 —— 读不到行数就按"没有仓库"处理
            from ..app.applog import warn as _warn
            _warn(f"检查仓库数失败（跳过索引重建）：{e}")
            return 0
        if repos_n == 0:
            return 0
        return self.rebuild_fts()

    # ---------------------------------------------------------------- 查询
    def fts_search(self, query: str, limit: int = 500) -> list[dict[str, Any]]:
        """FTS5 查询（>=FTS_MIN_LEN 才走这里），按 bm25 相关性排序。

        返回与 `list_repos` 同形状的行（`SELECT repos.* FROM ... JOIN`），
        让调用方不必区分走了哪条路。
        """
        q = str(query or "").strip()
        if not getattr(self, "_fts_ready", False) or len(q) < FTS_MIN_LEN:
            return []
        n = max(1, min(2000, int(limit or 500)))
        try:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT r.* FROM repo_fts f
                    JOIN repos r ON r.id = f.rowid
                    WHERE repo_fts MATCH ?
                    ORDER BY bm25(repo_fts), r.favorite DESC, r.owner, r.repo
                    LIMIT ?
                    """,
                    (_fts_pattern(q), n)).fetchall()
                return [dict(r) for r in rows]
        except Exception as e:      # noqa: BLE001 —— FTS 查询语法出错时绝不拖垮搜索
            from ..app.applog import warn as _warn
            _warn(f"FTS 查询失败（本次降级为 LIKE）：{e}")
            return []


def _fts_pattern(query: str) -> str:
    """把用户输入变成 FTS5 短语查询（双引号包裹并转义内部引号）。

    用**短语**而不是裸 token：裸 token 会被 FTS5 语法解释（`-`/`*`/`OR` 等），
    用户搜 `a-b` 会得到语法错误或意外结果。
    """
    safe = str(query or "").replace('"', '""')
    return f'"{safe}"'


def _mark_v8_degraded(conn: sqlite3.Connection) -> None:
    """S-03：FTS 建表失败时的**有界降级** —— 只把版本推进到 8，避免每次打开
    都重试建虚表；其余表结构（v0–v7）此时已由 `_migrate` 成功推进。

    与旧实现的区别：旧代码把**整个** `_migrate` 包在 except 里并强推到头版本，
    磁盘满/锁超时的半迁移会被固化为已完成 → 永久坏库。现在降级只可能发生在
    「迁移已推进到 v8 一步」时（调用方判定 `v_now >= _SCHEMA_VERSION - 1`）。
    """
    conn.execute("PRAGMA user_version=8")
