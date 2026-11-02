"""自适应自动更新间隔（G83 P1-1，NewsNow 式动态抓取间隔的本地化版本）。

设计约束：
- 纯函数、无时钟依赖（调用方负责把"连续无变更轮数"喂进来）；
- 不新增设置项：基础间隔就是既有的 `auto_update_minutes`，
  退避只发生在"仓库无任何变更"的场景，用户感知是"没变化时更省请求"，
  有变化时行为与旧版完全一致（向后兼容，回滚 = 删本模块）。
"""
from __future__ import annotations

import math

MAX_FACTOR = 4.0
STEP = 1.5


def next_interval_minutes(base_minutes: int,
                          no_change_streak: int | None) -> int:
    """根据连续无变更轮数计算下一轮间隔（分钟）。

    - base_minutes <= 0：功能关闭，原样返回；
    - no_change_streak 为 None（无历史）或 0（上轮有变更）：返回 base；
    - streak >= 1：base × 1.5^streak，封顶 4×base，向上取整。
    """
    if base_minutes <= 0:
        return base_minutes
    if no_change_streak is None:
        return base_minutes  # 尚无历史，不做退避
    if no_change_streak <= 0:
        return base_minutes  # 上轮有变更，回基础间隔
    factor = min(STEP ** int(no_change_streak), MAX_FACTOR)
    return max(base_minutes, math.ceil(base_minutes * factor))
