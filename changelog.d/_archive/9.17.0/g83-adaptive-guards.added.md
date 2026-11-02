# 零断言守卫+记忆治理+自适应更新间隔

## added
- 零断言测试静态守卫：`scripts/zero_assert_scan.py`（AST 扫描）+ `tests/test_g83_zero_assert.py`；首轮实测抓出 8 个"不抛即过"的测试并全部补真实断言（含 paint 像素级断言）。
- 记忆治理校验器：`scripts/memory_check.py`（frontmatter schema：topic/updated/owner，180 天过期复核，change-index 模块路径有效性）；`.agent/memory/*.md` 全部补齐 frontmatter。
- 自适应自动更新间隔（`gcm/app/adaptive_interval.py`）：连续无变更轮按 1.5× 退避、封顶 4×，有变更回基础间隔；不新增设置项，关闭行为不变。
- `docs/agent-contract.md` 新增 §10 工具风险分级与治理（Tier 0–2 / 隔离区 / 放行即审批词汇，对齐 OWASP MCP Governance 与 mcpproxy 模型）。

## fixed
- `scan_alerts` 移除死参数 `global_fail_rate_pct`（vulture 90% 置信确认零调用零读取）。
- 根目录白名单登记 bd (beads) 引入的 5 项（.beads/AGENTS.md/.agents/.codex/.claude），修复先前已有的守卫红灯。

@verify 快集全量 0 失败 · DRIFT 0 · memory_check 0 · zero_assert 0 · 变异自检通过
