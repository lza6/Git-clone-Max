"""批次②（对标 Surge/crawl4ai）：引擎感知调度——慢 worker 健康检查 +
per-host 自适应退避。

两个能力（纯逻辑、无 Qt、可注入时钟）：

1. `SlowWorkerMonitor`：Surge "0.3x 均速" 判据——某 worker 完成耗时超过
   本批已完结任务**中位数的 1/0.3 倍**（即速度低于均速 0.3 倍）即判慢。
   引擎可据此把它移到重试队列尾部/降并发，而不是傻等它超时。
2. `HostBackoffTable`：crawl4ai 式 per-host 退避——某 host 连续失败时按
   `base * 2^(streak-1)` 抬高下次投放延迟（封顶 `max_delay`），成功一次即清零。
   与 `app/errors.classify_error`（重试判据唯一来源）协同：这里只消费
   "失败/成功"事实，不重复判定错误类别。

线程安全：两个类都可能被引擎 worker 线程并发调用，方法内部持锁。
"""
from __future__ import annotations

import threading
import time
from collections import defaultdict
from collections.abc import Callable


class SlowWorkerMonitor:
    """基于中位耗时比的慢 worker 判定（Surge 0.3x 规则）。

    - `record(key, seconds)`：worker 收工时登记耗时；
    - `is_slow(key, seconds)`：秒数 > max(中位数/0.3, floor) 判慢。
      floor（默认 60s）防止"全部任务都很快时，2s 也被判慢"的噪声；
    - `median()`：本批已登记耗时中位数（空批返回 0.0）。
    """

    def __init__(self, ratio: float = 0.3, floor_seconds: float = 60.0):
        self._ratio = max(0.01, float(ratio))
        self._floor = max(0.0, float(floor_seconds))
        self._samples: dict[str, float] = {}
        self._lock = threading.Lock()

    def record(self, key: str, seconds: float) -> None:
        with self._lock:
            self._samples[str(key)] = max(0.0, float(seconds))

    def median(self) -> float:
        with self._lock:
            vals = sorted(self._samples.values())
        if not vals:
            return 0.0
        n = len(vals)
        mid = n // 2
        if n % 2:
            return float(vals[mid])
        return (vals[mid - 1] + vals[mid]) / 2.0

    def threshold(self) -> float:
        """当前判慢阈值（中位数/0.3 与 floor 取大）；无基准返回 0。"""
        med = self.median()
        if med <= 0:
            return 0.0
        return max(med / self._ratio, self._floor)

    def is_slow(self, key: str, seconds: float) -> bool:
        med = self.median()
        if med <= 0:
            return False  # 无基准时不判慢（首批/单任务场景）
        threshold = max(med / self._ratio, self._floor)
        return float(seconds) > threshold

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()


class HostBackoffTable:
    """per-host 指数退避表（成功清零、失败翻倍、封顶封底）。

    - `on_success(host)`：清零该 host 的连续失败计数；
    - `on_failure(host)`：streak +1，返回本次建议延迟秒数
      `min(base * 2**(streak-1), max_delay)`；
    - `delay_for(host)`：只读查询当前建议延迟（不改变状态）；
    - `clock`：可注入单调时钟（测试用）；生产用 time.monotonic。
    """

    def __init__(self, base_seconds: float = 5.0, max_seconds: float = 300.0,
                 clock: Callable[[], float] | None = None):
        self._base = max(0.0, float(base_seconds))
        self._max = max(self._base, float(max_seconds))
        self._clock = clock or time.monotonic
        self._streaks: defaultdict[str, int] = defaultdict(int)
        self._until: dict[str, float] = {}
        self._lock = threading.Lock()

    def on_success(self, host: str) -> None:
        with self._lock:
            self._streaks.pop(host, None)
            self._until.pop(host, None)

    def on_failure(self, host: str) -> float:
        with self._lock:
            self._streaks[host] += 1
            delay = min(self._base * (2 ** (self._streaks[host] - 1)),
                        self._max)
            self._until[host] = float(self._clock()) + delay
            return float(delay)

    def delay_for(self, host: str) -> float:
        """该 host 当前还需等待的秒数（<=0 = 可立即投放）。"""
        with self._lock:
            until = self._until.get(host, 0.0)
        return max(0.0, until - float(self._clock()))

    def streak(self, host: str) -> int:
        with self._lock:
            return int(self._streaks.get(host, 0))
