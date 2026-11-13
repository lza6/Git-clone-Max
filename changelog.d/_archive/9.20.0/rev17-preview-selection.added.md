# 执行前预览支持勾选执行：删除重建行默认排除
预览差异表新增「执行」勾选列——clone/update 默认全选，replace（删目录重建）默认不勾（破坏性动作默认排除，与关窗默认 No 同一人因）；blocked/duplicate/invalid 不进勾选语义；全不选时「执行勾选行」按钮禁用；确认后未勾选行从输入区剔除并逐条播报，主链路与 CLI/MCP 同源 plan_batch 不变。
@verify tests/test_rev17_preview_selection 6 例（含变异自检：翻转 replace 默认勾选→红→恢复→绿）
