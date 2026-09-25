"""G58-1 API 限流友好：统一 RateLimitState（余量/重置时间读响应头，403/429 退避）。

Star 导入 / 更新检查 / 诊断共用；纯逻辑可注入时钟测试。
"""
from __future__ import annotations

import time
from typing import Optional


class RateLimitState:
    """跟踪 GitHub API 限流余量与退避。

    - observe(headers)：从响应头解析 X-RateLimit-Remaining / X-RateLimit-Reset
    - backoff_seconds()：429/403 触发的建议等待（指数退避，上限 600s）
    """

    def __init__(self, now_fn: Optional[object] = None):  # type: ignore[assignment]
        self._now = now_fn if callable(now_fn) else time.time
        self.remaining: Optional[int] = None
        self.reset_at: Optional[float] = None
        self._consecutive = 0

    def observe(self, headers: dict | None, status: int = 200) -> None:
        """从响应头/状态更新限流状态。"""
        headers = headers or {}
        try:
            rem = headers.get("X-RateLimit-Remaining")
            if rem is not None:
                self.remaining = int(rem)
        except (TypeError, ValueError):
            self.remaining = None
        try:
            reset = headers.get("X-RateLimit-Reset")
            if reset is not None:
                self.reset_at = float(reset)
        except (TypeError, ValueError):
            self.reset_at = None
        if status in (403, 429):
            self._consecutive += 1
        else:
            self._consecutive = 0

    @property
    def is_limited(self) -> bool:
        """余量为 0 或已连续触发限流。"""
        if self.remaining is not None and self.remaining <= 0:
            return True
        return self._consecutive > 0

    def backoff_seconds(self) -> float:
        """建议等待秒数：指数退避 2^n*5s，上限 600s。"""
        if self._consecutive <= 0:
            return 0.0
        return min(600.0, 5.0 * (2 ** (self._consecutive - 1)))

    def wait_until_reset(self) -> Optional[float]:
        """若已限流且有 reset 时间，返回需要等待的秒数；否则 None。"""
        if not self.is_limited or self.reset_at is None:
            return None
        return max(0.0, self.reset_at - self._now())

    def reset(self) -> None:
        """手动清零（如切换凭据/新窗口）。"""
        self._consecutive = 0
        self.remaining = None
        self.reset_at = None
