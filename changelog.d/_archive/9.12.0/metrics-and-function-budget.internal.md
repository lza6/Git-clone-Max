# 度量与函数规模双守卫：数字可一键复测，函数膨胀必须登记
scripts/dev_metrics.py：一条命令打印模块行数/超限集合/近限模块/超 100 行函数/except 预算/测试规模/技能包，支持 --json 供 CI 与守卫复用。
tests/test_function_size_budget.py：超 100 行函数集合相等断言（基线 20 个，逐项登记）；拦「又多了第 21 个」也拦「偷偷拆掉却不更新文档」。自带反向自检（阈值降到 3 必须报出一大批）。
@verify tests/test_function_size_budget 4 例绿；python scripts/dev_metrics.py 输出与文档逐项对得上
