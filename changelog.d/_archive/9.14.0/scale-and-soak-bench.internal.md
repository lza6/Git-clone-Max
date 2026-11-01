# 规模与泄漏两个度量脚本（把「能不能撑住几千仓库」变成可复算数字）
scripts/bench_scale.py：合成 500/1000/3000 仓库，量建库、list_repos、分页、LIKE 搜索、模型重建+首帧、dir_stats 命中、10 万行 prune。实测 3000 仓：list_repos 14.2ms / 分页 0.7ms / 搜索 3.9ms / 模型 4.1ms / 首帧 0.14ms；10 万行 prune 238ms。
scripts/mem_soak.py：模拟 N 轮「结果→进度→事件发布→面板刷新」，tracemalloc 对比 Top 10 分配点。实测 500 轮 276.4 KiB / 2000 轮 279.7 KiB —— 事件环形缓冲有界（500+200），内存**不随轮数增长**。
@verify 两脚本本机真跑并产出上述数字；结果将登记进 docs/慢测定位.md
