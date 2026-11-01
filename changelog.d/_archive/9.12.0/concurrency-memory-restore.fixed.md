# 跨会话并发记忆真的会被恢复（此前读侧被短路）
并发记忆的写入侧（批次结束无条件 _persist_concurrency_state）一直是好的，但读侧 _restore_concurrency_state 被关在 if self._retry_queue is not None 里；而默认 retry_enabled=False → 只想不写：上次被限流降到 4，重启又回 32，再被限流一次。现改为只看 concurrency_state_path 是否存在。
@verify tests/test_c1_wiring.TestConcurrencyMemoryNotShortCircuited（2 例，含上限钳制回归）
