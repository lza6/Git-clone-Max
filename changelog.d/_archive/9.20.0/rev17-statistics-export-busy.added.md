# 统计中心导出 PNG 接忙态（U-15 慢入口收尾）
大表渲染可能慢：_export_png 接 busy() 上下文（进入禁用/退出必恢复，含异常路径），事件进 JOB_STARTED/JOB_FINISHED。
@verify tests/test_rev17_statistics_busy 3 例（含异常路径按钮恢复断言）
