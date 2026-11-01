# 批次流控上限按线程池派生（原恒 64 > 池上限 32）
首批入队上限恒为模块常量 64，而线程池上限最多 32 —— 超出的 worker 已构造完（各持一个 GitService 与 env 字典）在 Qt 队列里干等，既占内存又拉长「取消后多久真停」。改为 batch_start_limit() = min(硬上限, 池上限)；补投改为补满到当前上限（用户运行中调高并发可立刻生效）。
@verify tests/test_c1_wiring.TestBatchStartLimitDerived（3 例）+ tests/test_g43_engine 流控断言同步更新（精度不降）
