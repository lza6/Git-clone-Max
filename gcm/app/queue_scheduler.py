"""队列调度判定（G80-B7-3 / S2）：**纯函数**，不碰引擎、不碰时间。

把「今天该不该跑这个队列」做成可注入时钟的纯函数，是为了能测跨日/跨周/边界
（23:59 与 00:01、周日到周一、start>end 的跨零点窗）而不必真等一晚。

语义（与设计稿一致，改动必须先改设计）：
1. `days_of_week` 命中当天 **且** 时间在 `[start, end)` → 允许；
2. start/end 都为空 → 视为「随时可启动」（退化为手动触发）；
3. start > end → 跨零点窗（如 22:00–06:00）；
4. 错过补跑由调用方结合 `last_run_at` 判定（本模块给 `missed_today`）。
"""
from __future__ import annotations

import datetime
from typing import Any

from ..db.queue_db import parse_hhmm


def is_due(days_set: set[int], start: str, end: str,
           now: datetime.datetime | None = None) -> bool:
    """该队列此刻是否允许启动（纯函数）。"""
    if now is None:
        now = datetime.datetime.now()
    if days_set and now.isoweekday() not in days_set:
        return False
    st = parse_hhmm(start)
    en = parse_hhmm(end)
    if st is None and en is None:
        return True  # 无时间窗 → 随时（手动触发场景）
    if st is None or en is None:
        # 脏时间串（parse 失败）按「无界」处理：宁可多跑一次，不可整队列失效。
        # 单边有效窗保留（start 有效→到点后随时；end 有效→到点前随时）。
        return True
    cur = (now.hour, now.minute)
    if st is not None and en is None:
        return cur >= st
    if st is None and en is not None:
        return cur < en
    # 两侧都有：start <= end 为同日窗；start > end 为跨零点窗
    if st is not None and en is not None:
        if st <= en:
            return st <= cur < en
        return cur >= st or cur < en
    return False


def missed_run_allowed(days_set: set[int], start: str, on_missed: str,
                       last_run_at: str | None,
                       now: datetime.datetime | None = None) -> bool:
    """进程在窗口后才启动时，是否允许补跑一次。

    规则（保守优先）：
    - `on_missed != "run_once"` → 永不补；
    - 当天（按 now 的日期）**已经跑过** → 不补（last_run_at 含日期前缀即可判）；
    - 启动时刻晚于窗口结束 → 允许补一次。
    """
    if str(on_missed or "skip") != "run_once":
        return False
    if now is None:
        now = datetime.datetime.now()
    if days_set and now.isoweekday() not in days_set:
        return False
    st = parse_hhmm(start)
    if st is not None and (now.hour, now.minute) <= st:
        return False  # 窗口还没开始，谈不上"错过"
    today = now.strftime("%Y-%m-%d")
    return not str(last_run_at or "").startswith(today)


def already_ran_in_window_today(days_set: set[int], start: str, end: str,
                                last_run_at: str | None,
                                now: datetime.datetime | None = None) -> bool:
    """S-06：该队列**今天在时间窗内**是否已经启动过。

    此前 `due_queues` 的到点分支只看 `is_due`（周几+时间窗），完全不看
    `last_run_at` —— tick 每 60 秒一次，窗口几小时 → 同一批在窗口内被
    重复启动几十次（Rev10 审计实锤复现）。修复：到点分支加这条闸。
    """
    if now is None:
        now = datetime.datetime.now()
    raw = str(last_run_at or "").strip()
    if not raw.startswith(now.strftime("%Y-%m-%d")):
        return False  # 今天没跑过 / 上次是别天
    st = parse_hhmm(start)
    # 上次启动时刻必须落在**本次窗口**内才算"已跑过"（防止跨零点窗误判：
    # 凌晨窗跑过一次，晚上窗仍应正常跑）
    try:
        ran = datetime.datetime.strptime(raw[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    en = parse_hhmm(end)
    if st is not None and en is not None:
        if st <= en:
            in_window = st <= (ran.hour, ran.minute) <= en
        else:  # 跨零点窗： ran 在 st 之后或 en 之前
            in_window = (ran.hour, ran.minute) >= st or (ran.hour, ran.minute) < en
        return in_window
    return True  # 无窗（随时跑）：跑过一次当天就算


def queue_spec_payload(queue_row: dict[str, Any]) -> list[dict[str, Any]]:
    """取队列的 specs（已由 QueueStore.list_queues 反序列化）。"""
    specs = queue_row.get("specs") or []
    return [dict(x) for x in specs if isinstance(x, dict)]
