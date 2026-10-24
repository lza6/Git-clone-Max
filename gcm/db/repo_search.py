"""仓库关键字搜索（G80-B4-3）：把「全表进内存过滤」下推成 SQL LIKE。

为什么单独成模块：`repo_db.py` 已贴着 800 行上限（791），再加一个带转义与 LIMIT
的查询方法就会越线（守卫 `test_g79_repo_hygiene` 会红）。抽成混入后
`Database` 一并继承，对外仍是 `db.search_repos(q)`。
"""
from __future__ import annotations

from typing import Any

_SEARCH_COLS = ("owner", "repo", "host", "folder_name", "tags", "note")


class SearchMixin:
    """按关键字搜索仓库（不分页时的轻量查询；返回行，敏感列由调用方剔除）。"""

    _lock: Any
    _conn: Any

    @staticmethod
    def _like_pattern(q: str) -> str:
        r"""把用户输入转成 LIKE 模式：转义 \ % _，避免通配符被当语法（注入面）。"""
        bs = chr(92)
        escaped = q.replace(bs, bs * 2).replace("%", bs + "%").replace("_", bs + "_")
        return f"%{escaped}%"

    def search_repos(self, query: str, host: str | None = None,
                     limit: int = 500) -> list[dict[str, Any]]:
        """按 owner/repo/host/folder_name/tags/note 做不区分大小写的包含匹配。

        G80-B4-3：原实现（HTTP `/api/repos/search` 与 MCP `search_repos`）都是
        `db.list_repos()` 取**全表**再在 Python 里过滤 —— 仓库上千时每次搜索都要搬全表。
        这里下推成一条参数化 SQL，并显式 LIMIT（默认 500）。
        """
        text = str(query or "").strip()
        if not text:
            return []
        pattern = self._like_pattern(text.lower())
        cols = " || ' ' || ".join(f"lower(coalesce({c}, ''))" for c in _SEARCH_COLS)
        sql = f"SELECT * FROM repos WHERE ({cols}) LIKE ? ESCAPE '" + chr(92) + "'"
        params: list[Any] = [pattern]
        if host:
            sql += " AND host=?"
            params.append(str(host))
        sql += " ORDER BY favorite DESC, owner, repo LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
