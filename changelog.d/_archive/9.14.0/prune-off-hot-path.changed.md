# 同步历史治理从「每 100 条」挪到「每批一次」
原来每插入 100 条 sync_history 就在 GUI 线程的 _on_result 里跑一次 prune_history —— 那是对全表跑的窗口函数，实测 10 万行 238ms。挂机几周后每 100 个结果就卡一帧。改为批次收尾调一次（db.prune_history_if_due），仍在引擎主线程（ADR-001 单写者不变）。
@verify tests/test_c3_data.TestPruneMovedOutOfHotPath（3 例）
