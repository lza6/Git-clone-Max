# 并发记忆恢复加三道守卫（独立审查发现它会静默钉死低并发）
独立审查指出：开启读侧（C1-9）后，一次网络抖动把并发降到 1 → 写进 concurrency_state.json → 之后每次构造引擎都恢复 1；而 _launch 每批新建引擎、成功连击随批清零，用户按「一次 1~2 个仓库」用时永远攒不满 3 次 → 并发永久钉在 1 且无提示。现加：①只在 fail_rate>0 时恢复（陈旧状态文件不得覆盖用户设置）；②恢复值不低于天花板一半；③恢复时 applog.warn + adapt_changed（可观测）。
顺带（审查意见）：_adapt_result 只把 SUCCESS/SKIPPED 计入成功连击（CANCELLED/冲突不该触发「网络恢复」假回升）；_running 在 __init__ 初始化；cancel_all 为待发项合成终态时用 _on_worker_done(count_running=False) 不递减在跑计数。
@verify tests/test_c1_wiring.TestConcurrencyMemoryNotShortCircuited（6 例：门控/下限/可观测且可回升/上限钳制/损坏文件/无队列也恢复）
