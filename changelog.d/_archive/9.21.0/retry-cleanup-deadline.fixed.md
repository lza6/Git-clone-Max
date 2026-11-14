# 超时重试链修复：重试前清残留目录 + 按轮重置超时 + 超时归入网络类
克隆撞超时后自动重试不再秒败：重试前先清理半成品残留目录（此前重试必撞 already exists 且错误分类被污染）；超时 deadline 按轮重置（此前重试共用第一轮 deadline，重试瞬间被判超时，重试机制从未真正生效）；超时标记行带 timed out 信号进 tail，treeless/depth1 弱网降级链与引擎自动重投可达（此前超时失败被判 other 终态）。
@verify tests.test_g85_retry_cleanup 9 例绿；test_a53/test_g58/test_c3/test_rate_limit/test_g38/test_core/test_engine/test_g71/test_g73/test_g67/test_g84_shutdown 331 例定向回归绿；ruff/mypy 绿
