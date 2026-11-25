# 定时队列调度账本（确定性槽位 + 补跑 6h 宽限 + 半开边界统一，DB schema v9；跨零点窗按开始日归属，「周五夜」的凌晨属于周五）
@verify tests.test_b1_scheduler_ledger + s06 + a14 + g80_queue OK；迁移 v9 重开幂等
