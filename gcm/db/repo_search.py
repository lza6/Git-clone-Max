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

    def _like_search(self, text: str, host: str | None,
                     limit: int) -> list[dict[str, Any]]:
        """LIKE 下推路径（关键字 <3 字符时走这里；也是 FTS 不可用时的兜底）。"""
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

    def search_repos(self, query: str, host: str | None = None,
                     limit: int = 500) -> list[dict[str, Any]]:
        """搜索仓库：**双路**（C4）—— 关键字 >=3 字符走 FTS5，<3 字符走 LIKE 下推；
        两者之上共同支持 `tag:` / `host:` / `favorite:` / `updated:` 过滤（C4-3）。

        为什么必须双路：FTS5 的 `trigram` 分词要求查询 **>=3 个 unicode 字符**，
        中文两字词（克隆/爬虫/量化）在纯 FTS 下**零命中**（Rev7 §2.1 实测）。
        两字走 LIKE：3000 行实测 3.9ms，完全够快。

        G80-B4-3 起本方法就是搜索的唯一入口（管理页 / `GET /api/repos/search` /
        MCP `search_repos` 共用），所以在这里收口即可，调用方无需改。
        """
        from .repo_fts import FTS_MIN_LEN
        from .repo_query import parse_query
        parsed = parse_query(str(query or ""))
        if parsed.errors:
            from ..app.applog import warn as _warn
            _warn("搜索语法有误（已忽略无效条件）：" + "；".join(parsed.errors))
        text = parsed.text
        plain_host = (host or "").strip().lower()
        # 条件全空 → 不做全表扫描（用户还没输入）
        if not (text or parsed.tags or parsed.hosts or parsed.favorite is not None
                or parsed.updated_within_days or parsed.updated_older_days
                or parsed.neg_tags or parsed.neg_hosts or plain_host):
            return []
        n = max(1, min(2000, int(limit)))

        # ---- 1) 取候选（双路）
        rows: list[dict[str, Any]] = []
        if text and len(text) >= FTS_MIN_LEN:
            rows = self.fts_search(text, limit=n)
            if not rows:            # 索引未建好（或没命中）→ 退回 LIKE，行为不退化
                rows = self._like_search(text, plain_host or None, n)
        elif text:
            rows = self._like_search(text, plain_host or None, n)
        # 无关键字：候选为空，结构化条件交给 _filter_only 走 SQL

        # ---- 2) 结构化条件（在候选集上过滤；条件都在 repos 行里，无需 join）
        out: list[dict[str, Any]] = []
        for r in rows:
            if plain_host and str(r.get("host") or "").lower() != plain_host:
                continue
            if parsed.hosts and str(r.get("host") or "").lower() not in parsed.hosts:
                continue
            if parsed.neg_hosts and str(r.get("host") or "").lower() in parsed.neg_hosts:
                continue
            # S-04：与存储/SQL 侧同一口径 —— 竖线为主、历史逗号归一
            tags = [t.strip().lower() for t in str(r.get("tags") or "")
                    .replace(",", "|").split("|") if t.strip()]
            if any(t not in tags for t in parsed.tags):
                continue
            if any(t in tags for t in parsed.neg_tags):
                continue
            if parsed.favorite is not None and bool(r.get("favorite")) != parsed.favorite:
                continue
            if ((parsed.updated_within_days or parsed.updated_older_days)
                    and not _updated_within(r.get("last_sync_at"), parsed)):
                continue
            out.append(r)
            if len(out) >= n:
                break
        if not out and not text and (
                parsed.tags or parsed.hosts or parsed.favorite is not None
                or parsed.updated_within_days or parsed.updated_older_days
                or parsed.neg_tags or parsed.neg_hosts):
            # 只有结构化条件、没有关键字 → 用 SQL 条件查询（避免全表进内存）
            out = self._filter_only(parsed, plain_host, n)
        return out

    def _filter_only(self, parsed, host: str | None, limit: int) -> list[dict[str, Any]]:
        """只有结构化条件（无关键字）时走 SQL WHERE，避免把全表搬进内存。"""
        sql = "SELECT * FROM repos WHERE 1=1"
        params: list[Any] = []
        if host:
            sql += " AND lower(host)=?"
            params.append(host.lower())
        # `host:` / `-host:` 条件（只走结构化路径时也要生效）
        for h in parsed.hosts:
            sql += " AND lower(host)=?"
            params.append(h)
        for h in parsed.neg_hosts:
            sql += " AND lower(host)!=?"
            params.append(h)
        if parsed.favorite is not None:
            sql += " AND favorite=?"
            params.append(1 if parsed.favorite else 0)
        # 标签按「竖线边界」精确匹配（S-04）：存储侧 `set_tags` 用 `|` 连接，
        # 搜索侧必须用同一边界符 —— 此前这里用逗号边界，多标签仓库 `tag:x`
        # 永久零命中（三种口径之一，实测复现）。仍先去掉空格，避免
        # `| web | ` 这类带空格写法漏配；`%` 前后拼接保证 `tag:py` 不误中 `python`。
        # replace 两步：历史数据可能是逗号分隔（旧版/元数据导入），先归一成竖线
        tag_expr = ("'|' || replace(replace(lower(coalesce(tags, '')), ' ', ''), ',', '|') || '|'")
        for t in parsed.tags:
            sql += f" AND {tag_expr} LIKE ?"
            params.append(f"%|{t}|%")
        for t in parsed.neg_tags:
            sql += f" AND {tag_expr} NOT LIKE ?"
            params.append(f"%|{t}|%")
        if parsed.updated_within_days:
            sql += " AND last_sync_at >= datetime('now','localtime', ?)"
            params.append(f"-{int(parsed.updated_within_days)} days")
        if parsed.updated_older_days:
            # 从未同步（空）也算"很旧"
            sql += (" AND (last_sync_at IS NULL OR last_sync_at = ''"
                    " OR last_sync_at <= datetime('now','localtime', ?))")
            params.append(f"-{int(parsed.updated_older_days)} days")
        sql += " ORDER BY favorite DESC, owner, repo LIMIT ?"
        params.append(max(1, int(limit)))
        try:
            with self._lock:
                rows = self._conn.execute(sql, params).fetchall()
        except Exception as e:      # noqa: BLE001 —— 查询失败按空结果处理，但留痕
            from ..app.applog import warn as _warn
            _warn(f"结构化搜索失败：{e}")
            return []
        return [dict(r) for r in rows]


def _updated_within(last_sync_at: Any, parsed) -> bool:
    """`updated:<Nd` / `updated:>Nd` 判定（没有同步时间的视为"从未同步"）。"""
    import datetime as _dt
    raw = str(last_sync_at or "").strip()
    if not raw:
        # 从未同步：算"很旧"——满足 >Nd，不满足 <Nd
        return bool(parsed.updated_older_days)
    try:
        ts = _dt.datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return bool(parsed.updated_older_days)
    age_days = (_dt.datetime.now() - ts).total_seconds() / 86400.0
    if parsed.updated_within_days and age_days > parsed.updated_within_days:
        return False
    if parsed.updated_older_days and age_days <= parsed.updated_older_days:
        return False
    return True
