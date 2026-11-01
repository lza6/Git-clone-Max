# 同平台并发上限真正可配（此前参数被硬编码覆盖）
engine 构造参数 host_concurrency 被 __init__ 末尾一行 self._host_concurrency = 4 无条件覆盖：设 9 实际 4，设 0（语义=不限流）也是 4，set_host_concurrency 零生产调用点 = 可配置性伪装。删掉该行 + 设置页新增控件（ui/settings_network.py）+ 取 0 时 _host_gate 返回 None 真不限流。
顺带补 time_budget_min（单仓总时间预算）的设置页控件 —— 它此前只有读侧，A5-3 花大代价做的降级链预算用户根本够不到。
@verify tests/test_c1_wiring.TestHostConcurrencyInjection（3 例）+ TestSettingsNetworkWiring（4 例）
