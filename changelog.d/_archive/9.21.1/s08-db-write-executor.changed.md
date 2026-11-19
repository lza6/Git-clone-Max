# S-08 后台执行器：结果落库迁出 GUI 线程（单线程保序 + 关停冲刷 + 降级原地执行）
@verify tests.test_db_executor 4 例绿（含 discard 冲刷后落库断言）
