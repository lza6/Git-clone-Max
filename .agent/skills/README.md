# .agent/skills — 项目技能索引

> 每个技能都是「**适用场景 → 前置读取 → 步骤 → 验收 → 回滚 → 常见坑**」。
> 与 `skills/gcm-*`（agent 可直接调用的技能包，含可执行脚本）互补：
> 那边是**可执行的工具**，这边是**做事流程与判定标准**。

| 技能 | 什么时候用 |
|------|-----------|
| [add-new-api.md](add-new-api.md) | 新增/修改 CLI、MCP、HTTP 等对外接口 |
| [add-new-feature.md](add-new-feature.md) | 新增一个用户可见功能（含 UI） |
| [release-checklist.md](release-checklist.md) | 发版（tag / 构建 / Release） |
| [incident-rollback.md](incident-rollback.md) | 线上/发布后出问题，回滚与止损 |
| [verification-matrix.md](verification-matrix.md) | 决定「这次改动要跑哪些验证」 |
| [code-review-checklist.md](code-review-checklist.md) | 自审或发起独立审查 |
| [project-audit-sop.md](project-audit-sop.md) | 全项目终局审计 |
| [high-concurrency-checklist.md](high-concurrency-checklist.md) | 并发/性能/可观测性评审 |
| [plan-doc-audit.md](plan-doc-audit.md) | 计划/台账类文档逐条审计、归档与删除 |
