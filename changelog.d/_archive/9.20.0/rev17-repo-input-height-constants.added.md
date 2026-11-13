# 极矮折叠窗输入框高度常量化 + 常量消费守卫
Rev16 复审 P3 收口：_apply_compact_visibility 的 96/112/560 字面量抽为模块常量 REPO_INPUT_MAX_FOLDED_SHORT/MAX_NORMAL/SHORT_WINDOW，守卫断言代码引用常量而非字面量（offscreen 可达的最大强度，真机渲染仍归 beads ph3）。
@verify test_g78_quick_scroll 4 例（含变异自检：字面量回退→红→恢复→绿）
