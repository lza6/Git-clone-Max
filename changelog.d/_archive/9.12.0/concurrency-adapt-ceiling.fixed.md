# 用户改并发后不再被自适应顶回旧值
set_concurrency 只改线程池、不改自适应天花板 _adapt_max：用户把并发从 8 调到 3，连续 3 次成功后自适应把它顶回 8。现区分 by_user（用户显式改 → 同步天花板并立刻补投待发队列）与 by_user=False（引擎内部自适应/跨会话恢复 → 只动线程池，不动天花板）。
@verify tests/test_c1_wiring.TestSetConcurrencyUpdatesAdaptMax（3 例，含真实 _adapt_result 路径）
