# 耗时入口统一忙态：进入禁用、退出必恢复（异常也一样）
此前耗时入口各自实现忙态：有的禁用按钮、有的只写日志、有的什么都不做 → 用户侧就是「点了没反应 / 又点一次」。新增 ui/busy.py 的 busy() 上下文：进入禁用入口按钮并发布 job_started，退出**无条件**恢复（finally）并发布 job_finished；失败时 result=failed 并留日志后上抛。恢复的是**进入前**的状态（本来就该禁用的按钮不会被点亮）。
首批接两个当前完全没反馈的入口：诊断包导出（要遍历事件/日志并压 zip）与报表导出（几千仓库导 xlsx 要数秒）。已有的（克隆批次 busy、Star 导入 _star_busy、镜像测速 _mirror_probe_busy）不重复改造 —— 为整齐去动能跑的代码不值当。事件 batch 侧新增 job_started/job_finished 并登记进 EventType.ALL。
@verify tests/test_c2_ux.TestUnifiedBusy（5 例：禁用/恢复、异常也恢复、恢复原状态、事件发出且登记进 ALL、AST 断言两处真实接线）
