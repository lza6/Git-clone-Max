# 分阶段耗时（schema v7）：直接回答「这批为什么慢」
此前 sync_history 只有整仓总时长 duration_ms。用户报「今天全都慢」时只能翻原始 git 日志猜是 DNS/TLS、镜像探测、传输还是检出。现在 SyncResult.stages 记录 precheck / transfer（含全部重试与降级链）/ checkout / hook 四段，落 sync_history.stage_json，配套 db.stage_stats 的 slow_stages()（最慢仓库）与 stage_totals()（各段累计，已接进诊断报告）。
埋点方式：用一个 _timed(res, name, fn) 包住「传输/更新」与「检出」两个调用点 —— 而不是在 run_git_ui 的 5 处调用里逐处插桩（漏一处就失真）。
@verify tests/test_c3_data.TestStageTiming（5 例，含真实本地克隆断言 transfer/checkout 两段都有值）
