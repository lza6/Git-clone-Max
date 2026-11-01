# 疑似卡死任务终于有 UI 入口可处理
engine.cancel_stuck_tasks() 有实现有单测，但 gcm/ui/ 零引用 —— 用户只在日志里看到「疑似卡死任务」，界面上没有任何按钮能动它。失败汇总面板顶部新增一行（默认隐藏）：显示 N 个疑似卡死任务 + 「取消这些卡死任务」按钮；引擎日志也补了指向该面板的提示。
@verify tests/test_c1_wiring.TestStuckTaskUiEntry（3 例，含 AST 接线断言）
