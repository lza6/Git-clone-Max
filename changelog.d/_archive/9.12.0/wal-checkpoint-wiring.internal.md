# 批次收尾触发一次被动 WAL 收缩
db.wal_checkpoint(PASSIVE) 有实现、零生产调用点（只有测试）→ 长挂机 + 未结束事务会让 WAL 涨到几十 MB 不收缩。现批次收尾调一次；失败只 warn 不影响本批结果（数据库被关/被占用是真实场景）。
顺带删除死常量 _FLUSH_INTERVAL —— 模块 docstring 曾宣称「周期落盘」，而它全库零引用（真实落盘由 _progress_flush_maybe 的 2s 节流 + 100ms 心跳承担）。
@verify tests/test_c1_wiring.TestWalCheckpointOnBatchFinish（2 例）+ TestDeadFlushIntervalRemoved（2 例）
