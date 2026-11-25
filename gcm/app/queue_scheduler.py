"""队列调度判定（G80-B7-3 / S2）：**纯函数**，不碰引擎、不碰时间。

把「今天该不该跑这个队列」做成可注入时钟的纯函数，是为了能测跨日/跨周/边界
（23:59 与 00:01、周日到周一、start>end 的跨零点窗）而不必真等一晚。

语义（与设计稿一致，改动必须先改设计）：
1. `days_of_week` 命中当天 **且** 时间在 `[start, end)` → 允许；
2. start/end 都为空 → 视为「随时可启动」（退化为手动触发）；
3. start > end → 跨零点窗（如 22:00–06:00）：窗口按**开始日**归属 —— 凌晨段
   属于前一天开启的窗口，`days_of_week` 也按开始日算（周五夜熬到周六凌晨仍属
   周五）；
4. 错过补跑由调用方结合 `last_run_at` 判定（本模块给 `missed_run_allowed`）；
5. B1：`window_slot` 给出确定性的「队列×计划槽位」键，`QueueStore.claim_slot`
   用它做 INSERT OR IGNORE 幂等抢占 —— 同日同窗只可能启动一次，与 tick 频率无关。
6. 审查 R2（MAJOR）：窗口身份 = `[start_dt, end_dt)` 的**实例**，不是时刻。
   `_window_bounds` 是唯一来源：`window_slot` 的槽位日期、补跑宽限、`already_ran`
   与 `is_due` 的周几归属全部由它推导 —— 否则 07:00 的补跑会被记成当晚窗口，
   把整个夜间窗口静默跳过。
"""
from __future__ import annotations

import datetime
from typing import Any

from ..db.queue_db import parse_hhmm

#: B1：`on_missed=run_once` 的补跑宽限（窗口结束后 N 小时内才允许补）。
#: 参考 dagu 的 catchup_window 语义：桌面软件可能数天不开机，开机后补一个
#: 一周前的窗口在大多数场景是错的。6 小时覆盖「睡个午觉/下班开机」的常态。
CATCHUP_GRACE_HOURS = 6.0

#: 旧字符串反推语义保留（单边/无窗）的当天判定格式。
_TS_FMT = "%Y-%m-%d %H:%M:%S"
#: `last_run_at` 可能是人手/测试写入的分钟精度值（`datetime('now')` 是秒精度）。
_TS_FMTS = (_TS_FMT, "%Y-%m-%d %H:%M")


def _parse_ts(raw: Any) -> datetime.datetime | None:
    text = str(raw or "").strip()[:19]
    for fmt in _TS_FMTS:
        try:
            return datetime.datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _window_bounds(st: tuple[int, int], en: tuple[int, int],
                   now: datetime.datetime) -> tuple[datetime.datetime,
                                                     datetime.datetime]:
    """`now` 所处（或最近一次已结束）窗口实例的 `[start_dt, end_dt)`。

    同日窗：开始 = 今天 st，结束 = 今天 en。
    跨零点窗按开始日归属：`now` 时刻早于 st（凌晨段或窗后白天）→ 窗口在
    昨天开始；否则在今天开始。07:00 遇到 22:00–06:00 → 归属昨晚窗口
    （[昨天 22:00, 今天 06:00)），而不是用今晚的槽位。
    """
    start_day = now.date()
    if st > en and (now.hour, now.minute) < st:
        start_day -= datetime.timedelta(days=1)
    start_dt = datetime.datetime.combine(start_day,
                                         datetime.time(st[0], st[1]))
    end_day = start_day + datetime.timedelta(days=1) if st > en else start_day
    end_dt = datetime.datetime.combine(end_day, datetime.time(en[0], en[1]))
    return start_dt, end_dt


def is_due(days_set: set[int], start: str, end: str,
           now: datetime.datetime | None = None) -> bool:
    """该队列此刻是否允许启动（纯函数）。

    跨零点窗的凌晨段按**开始日**的周几判定（周六 01:00 属于周五夜窗口）。
    """
    if now is None:
        now = datetime.datetime.now()
    st = parse_hhmm(start)
    en = parse_hhmm(end)
    if st is None or en is None:
        # 脏/单边时间串按「无界」处理（保留既有宽容语义）：只看周几。
        return not days_set or now.isoweekday() in days_set
    cur = (now.hour, now.minute)
    if st <= en:
        if days_set and now.isoweekday() not in days_set:
            return False
        return st <= cur < en
    start_dt, end_dt = _window_bounds(st, en, now)
    if not (start_dt <= now < end_dt):
        return False  # 白天（两次窗口之间）不在任何窗口内
    return not days_set or start_dt.isoweekday() in days_set


def missed_run_allowed(days_set: set[int], start: str, on_missed: str,
                       last_run_at: str | None,
                       now: datetime.datetime | None = None, *,
                       end: str = "",
                       grace_hours: float = CATCHUP_GRACE_HOURS) -> bool:
    """进程在窗口后才启动时，是否允许补跑一次。

    规则（保守优先）：
    - `on_missed != "run_once"` → 永不补；
    - 窗口实例（按开始日归属）内**已经跑过** → 不补（datetime 区间比较，
      凌晨 01:00 跑过不影响当晚 22:00 的窗口）；
    - 窗口未开始 / 仍在窗内 → 不算「错过」（窗内由 `is_due` 负责启动）；
    - 窗口结束后超过 `grace_hours` → 当作过期计划丢弃（桌面软件数天不开机时
      不补陈旧窗口）；未给 `end` 的旧调用保持原语义（兼容既有测试与外部调用）。
    """
    if str(on_missed or "skip") != "run_once":
        return False
    if now is None:
        now = datetime.datetime.now()
    st = parse_hhmm(start)
    en = parse_hhmm(end)
    if st is not None and en is not None:
        start_dt, end_dt = _window_bounds(st, en, now)
        if days_set and start_dt.isoweekday() not in days_set:
            return False
        if now <= start_dt or now < end_dt:
            return False  # 窗未开始 / 仍在窗内（后者由 is_due 启动）
        if grace_hours > 0 and now - end_dt > datetime.timedelta(
                hours=float(grace_hours)):
            return False  # 超过补跑宽限 → 视为过期计划
        ran = _parse_ts(last_run_at)
        if ran is None:
            return True  # 历史值不可解析：宽限已做上界，不额外拦补跑
        return not (start_dt <= ran < end_dt)
    # 无窗 / 单边时间串：保留旧字符串语义
    if days_set and now.isoweekday() not in days_set:
        return False
    if en is not None and grace_hours > 0:
        end_dt = now.replace(hour=en[0], minute=en[1], second=0, microsecond=0)
        if end_dt > now:
            end_dt -= datetime.timedelta(days=1)  # 最近一次已结束的窗口
        if now - end_dt > datetime.timedelta(hours=float(grace_hours)):
            return False
    if st is not None and (now.hour, now.minute) <= st:
        return False
    return not str(last_run_at or "").startswith(now.strftime("%Y-%m-%d"))


def already_ran_in_window_today(days_set: set[int], start: str, end: str,
                                last_run_at: str | None,
                                now: datetime.datetime | None = None) -> bool:
    """S-06：该队列**当前窗口实例内**是否已经启动过。

    此前 `due_queues` 的到点分支只看 `is_due`（周几+时间窗），完全不看
    `last_run_at` —— tick 每 60 秒一次，窗口几小时 → 同一批在窗口内被
    重复启动几十次（Rev10 审计实锤复现）。修复：到点分支加这条闸。

    R2 修正：改为窗口实例的 datetime 区间比较（`[start_dt, end_dt)`）。
    旧实现拿 `ran` 的**时刻**与窗内时段比较：凌晨 01:00 跑过一次后，当晚
    22:00 的窗口会被误判「今天已跑」；且与半开区间边界（`< en`）对齐。
    """
    if now is None:
        now = datetime.datetime.now()
    ran = _parse_ts(last_run_at)
    if ran is None:
        return False
    st = parse_hhmm(start)
    en = parse_hhmm(end)
    if st is None or en is None:
        return ran.date() == now.date()  # 无窗（随时跑）：跑过一次当天就算
    start_dt, end_dt = _window_bounds(st, en, now)
    return start_dt <= ran < end_dt


def window_slot(queue_id: int, start: str, end: str,
                now: datetime.datetime) -> tuple[str, str]:
    """B1：确定性计划槽位键与计划时刻 —— `(slot_key, scheduled_for_iso)`。

    同一个「队列 × 窗口实例」必须产出同一个键（窗口内任意 tick / 补跑都一致），
    否则账本幂等失效。窗口实例由 `_window_bounds` 定义（跨零点窗按开始日归属）：
    07:00 的补跑属于昨晚的窗口，键的日期是**昨天**，不会占用今晚窗口的槽位。
    时间窗缺失/脏值时退化为「按天一个槽」（无需精细窗口语义）。
    """
    st = parse_hhmm(start)
    en = parse_hhmm(end)
    day = now.date()
    if st is None or en is None:
        return (f"{int(queue_id)}:{day.isoformat()}:any",
                f"{day.isoformat()}T00:00:00")
    start_dt, _end_dt = _window_bounds(st, en, now)
    start_day = start_dt.date()
    slot_key = (f"{int(queue_id)}:{start_day.isoformat()}:"
                f"{start.strip()}-{end.strip()}")
    scheduled_for = f"{start_day.isoformat()}T{st[0]:02d}:{st[1]:02d}:00"
    return slot_key, scheduled_for


def queue_spec_payload(queue_row: dict[str, Any]) -> list[dict[str, Any]]:
    """取队列的 specs（已由 QueueStore.list_queues 反序列化）。"""
    specs = queue_row.get("specs") or []
    return [dict(x) for x in specs if isinstance(x, dict)]
