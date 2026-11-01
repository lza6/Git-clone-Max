"""分阶段耗时聚合（C3-4 / Rev7 C-29）：回答「这批为什么慢」。

**背景**：此前 `sync_history` 里只有 `duration_ms`（整仓总时长）。用户报"今天全都
很慢"时，只能翻原始 git 日志去猜是 DNS/TLS、镜像探测、fetch/克隆，还是检出阶段。
`SyncResult.stages`（`precheck` / `transfer` / `checkout` / `hook`）落库后，
就能直接回答「慢在传输还是别处」。

**数据形态**：`sync_history.stage_json` 存一段 JSON（毫秒值），空串 = 老数据/未埋点。
按需聚合，不新建表（阶段集合将来可能变，JSON 比列更耐改）。

**为什么单独成模块**：`repo_db.py` 贴着 800 行硬上限，与 v4/v5/v6 同一范式。
"""
from __future__ import annotations

import json
from typing import Any


def dumps_stages(stages: dict | None) -> str:
    """把 `SyncResult.stages` 序列化成入库文本（空/无效 → 空串）。"""
    if not stages:
        return ""
    try:
        # 只保留「键: 数值」，避免把任意对象塞进库
        clean = {str(k): round(float(v), 1) for k, v in stages.items()
                 if isinstance(v, (int, float))}
    except Exception:               # noqa: BLE001 —— 脏数据按"未埋点"处理
        return ""
    return json.dumps(clean, ensure_ascii=False) if clean else ""


def loads_stages(text: Any) -> dict[str, float]:
    """把入库文本还原成 `{阶段: 毫秒}`；坏数据返回空 dict（绝不抛）。"""
    if not text or not isinstance(text, str):
        return {}
    try:
        got = json.loads(text)
    except Exception:               # noqa: BLE001
        return {}
    if not isinstance(got, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in got.items():
        try:
            out[str(k)] = round(float(v), 1)
        except (TypeError, ValueError):
            continue
    return out


class StageStatsMixin:
    """`stage_json` 的聚合查询。宿主类须提供 `_lock` 与 `_conn`。"""

    _lock: Any
    _conn: Any

    def slow_stages(self, limit: int = 20) -> list[dict[str, Any]]:
        """按「传输阶段耗时」倒序列出最慢的仓库（去重到每仓一条最新记录）。

        返回 `[{repo_id, owner, repo, duration_ms, stages}]`；没有埋点数据时返回空列表
        （调用方据此显示"暂无阶段数据"，而不是 0 值骗人）。
        """
        n = max(1, min(500, int(limit or 20)))
        try:
            with self._lock:
                rows = self._conn.execute(
                    """
                    SELECT h.repo_id AS repo_id, r.owner AS owner, r.repo AS repo,
                           h.duration_ms AS duration_ms, h.stage_json AS stage_json
                    FROM sync_history h
                    JOIN repos r ON r.id = h.repo_id
                    WHERE h.stage_json IS NOT NULL AND h.stage_json != ''
                    ORDER BY h.duration_ms DESC
                    LIMIT ?
                    """,
                    (n,)).fetchall()
        except Exception as e:      # noqa: BLE001 —— 统计失败按"无数据"处理，但留痕
            from ..app.applog import warn as _warn
            _warn(f"阶段耗时聚合失败：{e}")
            return []
        out: list[dict[str, Any]] = []
        seen: set[int] = set()
        for r in rows:
            rid = int(r["repo_id"])
            if rid in seen:          # 同一个仓库只保留最慢的那条
                continue
            seen.add(rid)
            out.append({
                "repo_id": rid,
                "owner": r["owner"], "repo": r["repo"],
                "duration_ms": int(r["duration_ms"] or 0),
                "stages": loads_stages(r["stage_json"]),
            })
        return out

    def stage_totals(self) -> dict[str, float]:
        """各阶段累计毫秒（全库），用于"最耗时的是哪一段"的一眼判断。"""
        totals: dict[str, float] = {}
        try:
            with self._lock:
                rows = self._conn.execute(
                    "SELECT stage_json FROM sync_history"
                    " WHERE stage_json IS NOT NULL AND stage_json != ''"
                    " ORDER BY id DESC LIMIT 5000").fetchall()
        except Exception as e:      # noqa: BLE001
            from ..app.applog import warn as _warn
            _warn(f"阶段耗时汇总失败：{e}")
            return {}
        for r in rows:
            for k, v in loads_stages(r["stage_json"]).items():
                totals[k] = round(totals.get(k, 0.0) + v, 1)
        return totals
