# Workflow Status — G82（A 批收口 + 四类验收 + 发布）

## Task Contract
- 原始目标：按《下一步改进指南-2026-10-31》A 批根因批次（A5-1…A5-6）全部落地，
  四类验收（UI 截图 / 真实调用 / 真实 E2E / 深度复审）全过，文档漂移修正，完整发布（bump+push+tag+release）
- 用户拍板（2026-10-31）：
  - Q1 范围 = **A5 全批 + 四类验收**
  - Q2 发布 = **完整发布**（bump + push main + tag + Release 含 exe 资产）
  - Q3 GUI = **截图审计 + 静态交互审查**（不尝试真实 GUI 自动化点击，历史 offscreen 崩溃）
- 停止条件：A5-1…A5-6 全绿 + 四类验收证据落盘 + 发布完成；或遇阻塞按 §2.4 提问

## Task Graph
| ID | 任务 | 依赖 | 状态 |
|----|------|------|------|
| G82-0 | workflow_status 建立 + 已完成项登记 | - | DONE |
| G82-1 | A5-1 补漏：C-3 `_PLATFORM_KEYWORDS` 补中文 + 评估删 `service._classify_failure` 私有表（C-28） | 0 | IN_PROGRESS |
| G82-2 | A5-4 收口：先红后绿补证（变异实验）+ 独立审查 + 提交 | 0 | PENDING |
| G82-3 | A5-2/R4：retry_queue / runtime_sessions 跨进程读改写收口 + key 统一 folder_name + merge=True | 0 | PENDING |
| G82-4 | A5-3/R1：run_git_ui 轮询取消 + 进程组 kill 返回码 + 每仓库时间预算 | 3 | PENDING |
| G82-5 | A5-5/R3：engine per-host Semaphore + 最小间隔 | 3 | PENDING |
| G82-6 | A5-6/R7：统计中心后台线程 + progress 落盘节流 | 0 | PENDING |
| G82-7 | A-2 文档漂移 5 处 + A-4 收口清单 | 1 | PENDING |
| G82-8 | 四类验收：截图审计 / 真实调用（CLI+MCP+HTTP）/ e2e_smoke / 独立复审 | 1-6 | PENDING |
| G82-9 | HTML 变更报告 + 测验 | 8 | PENDING |
| G82-10 | 发布：bump + changelog build + 打包 + health_check + push + tag + Release | 8 | PENDING |

## Validation Log（只追加）
| 时间 | 动作 | 结果 | 证据 |
|------|------|------|------|
| 2026-10-31 | A5-1 主改动提交 `4a5086e`（engine._adapt_result → classify_error） | 26 tests OK / ruff+mypy 绿 | 提交与测试输出 |
| 2026-10-31 | A5-4 六入口确认范式实施 | 12 tests OK（待提交） | tests/test_a54_destructive_confirm.py |
| 2026-10-31 | C-3 复现实测：中文平台文案 classify_error → cat=other（未修） | 缺陷确认 | classify_error 实跑输出 |

## Decisions
- A5-4 先红后绿欠账：用「git stash 回滚修复 → 跑新测试应红 → 恢复 → 绿」补证据（G81 教训：变异必须先验证落地）
- C-28 收口评估：`service._classify_failure` 产出的是「人类可读文案」而非「重试判定」，
  与 `classify_error` 职责不同 —— 是否删除需看调用方（engine 只消费 message/detail 字符串）。
  若职责确属文案层则保留并登记例外，不强行合并（避免为守卫而守卫）。

## Blockers
- （无）

## Agent 管理 Agent 结论（2026-11-01，一轮）
- ICM 记忆检索（per-host 限流/批量下载最佳实践）：无本项目相关高价值新项。
- 既有指南 §9A-3 已登记 8 条通用实践（覆盖本方向），B8 批次为落地载体。
- 停止条件达成：无新增高价值项 → 不再追加轮次。

## 最强自我反驳（§5.4，逐条落定）
| 反驳 | 结论 |
|---|---|
| 是否偷换需求？ | 无 —— 用户拍板 A5 全批+四类验收+完整发布，逐项执行 |
| 浅实现当完整？ | 抓住过一次：budget 零接线（审查 C-1）→ 已全链路接通 |
| 只修表面？ | 按 A-7 根因修（8 根因→6 批次），非逐条打补丁 |
| 破坏旧逻辑？ | 快集抓住 1 次真回归（test_g46 弹框卡死）→ 已修；key 格式向后兼容 |
| 文档没同步？ | 版本 10 文件、接缝 S37-S40、验证记录、HTML 报告、记忆文件全部落盘 |
| 无证据宣称完成？ | evidence/G82-A-batch.json 19 条真实命令；4 轮独立审查 |
| 遗漏用户第一眼问题？ | 截图审计模拟小白视角抓到按钮裁切 |

## 最终交付状态（2026-11-01）
- **complete**：A5 全批（6 根因）+ A-1 疑点关闭 + A-2 文档漂移 + 四类验收 + v9.11.0 发布物
- 提交链：4a5086e → 62875a1 → 01c6715 → d0473d2 → da77fd3 → 0ee50a6 → 9430bb9 → b7dbaa9 → 087ebfd → a72f730
- 剩余：push/tag/Release 待快集终绿后执行（用户已授权完整发布）

## Next Gate
- G82-1 → G82-2 → G82-3…G82-6 → G82-8 四类验收 → G82-10 发布
