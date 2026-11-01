"""查询语法（C4-3 / Rev7 B8-3）：把「输入框里的一行」解析成结构化条件。

**纯函数、无 IO、无 Qt** —— 可单测、可拒绝非法输入。

支持（都对应 `repos` 表里**真实存在**的列，不画饼）：
    python                  → 关键字（走 FTS / LIKE 双路）
    tag:python              标签包含 python
    host:gitlab.com         平台过滤
    favorite:1              只看收藏（`favorite:0` 只看未收藏）
    updated:<30d            最近 30 天内同步过（`updated:>30d` = 超过 30 天没动）
    -tag:archive            负向过滤（排除带 archive 标签的）
    多个条件之间是 AND；关键字部分整体作为一个短语匹配。

**明确不支持并报错**（不静默忽略 —— 静默忽略会让用户以为筛选生效了）：
    lang:go / size:>10MB / archived:1
  原因：`lang` 存在 `repos.profile_json`（JSON 文本，需解析后才能比），
  `size` 在 `dir_stats` 且只是缓存（可能是空的）；把这两个做成"看起来能用、
  实际按不存在的列过滤"才是更坏的选择。将来若需要，应先加可索引的派生列。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 已知会被拒绝的字段（给出明确原因，而不是"未知字段"）
_UNSUPPORTED_FIELDS = {
    "lang": "语言在 profile_json 里（需解析 JSON），当前不支持按语言过滤",
    "size": "仓库大小在 dir_stats 缓存里（可能为空），当前不支持按大小过滤",
    "archived": "没有 archived 列；可用 `-tag:` 或标签约定代替",
}

_FIELD_RE = re.compile(r"^(?P<neg>-)?(?P<key>[a-z_]+):(?P<val>.+)$", re.I)
_UPDATED_RE = re.compile(r"^(?P<op>[<>])?(?P<n>\d+)d$", re.I)


@dataclass
class ParsedQuery:
    """解析结果。`text` 是去掉字段条件后的关键字部分（可能为空）。"""

    text: str = ""
    tags: list[str] = field(default_factory=list)
    neg_tags: list[str] = field(default_factory=list)
    hosts: list[str] = field(default_factory=list)
    neg_hosts: list[str] = field(default_factory=list)
    favorite: bool | None = None
    updated_within_days: int | None = None   # updated:<Nd
    updated_older_days: int | None = None    # updated:>Nd
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def parse_query(raw: str) -> ParsedQuery:
    """解析查询串；非法/不支持的字段记进 `errors`（调用方据此提示，不静默）。"""
    q = ParsedQuery()
    tokens = str(raw or "").split()
    text_parts: list[str] = []
    for tok in tokens:
        m = _FIELD_RE.match(tok)
        if not m:
            text_parts.append(tok)
            continue
        neg = bool(m.group("neg"))
        key = m.group("key").lower()
        val = m.group("val").strip()
        if key in _UNSUPPORTED_FIELDS:
            q.errors.append(f"不支持 `{key}:`：{_UNSUPPORTED_FIELDS[key]}")
            continue
        if key == "tag":
            (q.neg_tags if neg else q.tags).append(val.lower())
        elif key == "host":
            (q.neg_hosts if neg else q.hosts).append(val.lower())
        elif key == "favorite":
            if neg:
                q.errors.append("`favorite:` 不支持负向（用 favorite:0 表示未收藏）")
                continue
            q.favorite = val not in ("0", "false", "no")
        elif key == "updated":
            mu = _UPDATED_RE.match(val)
            if not mu:
                q.errors.append(f"`updated:` 只支持 `<Nd` / `>Nd`（如 updated:<30d），收到 {val!r}")
                continue
            days = int(mu.group("n"))
            if (mu.group("op") or "<") == "<":
                q.updated_within_days = days
            else:
                q.updated_older_days = days
        else:
            q.errors.append(f"未知字段 `{key}:`（支持 tag / host / favorite / updated）")
    q.text = " ".join(text_parts).strip()
    return q
