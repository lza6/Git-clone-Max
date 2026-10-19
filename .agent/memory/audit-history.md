# 审计历史（audit-history）

| 日期 | 范围 | 结论 | 产出 |
|------|------|------|------|
| 2026-10-16 | G51–G59 台账复验 + 前后端接缝 + agent 契约 + 安全 | 台账「全部闭环」**不成立**；发现定时报表零调用点、告警无出口、9 个设置字段不可达、MCP cp1252 崩、数据目录单源违规 | `docs/reports/G69-18-*.html`、`docs/验证记录-2026-10-16.md` |
| 2026-10-17 上午 | CI 80+ 次红灯根因 | 5 类成因（无诊断 / 泄漏线程 / Qt 引用环 / MCP 编码 / CI 自身编码） | `docs/验证记录-2026-10-17.md` §3 |
| 2026-10-17 下午 | 引用环 + Webhook + 清单 + 输入校验 + 压测 | 引用环已根治（含 `show()` 路径），移除 GC 安全网；Webhook 并发竞态已修 | `docs/reports/G69-19-*.html` |
| 2026-10-17 晚 | 独立审查（v9.4.2–v9.4.4）→ 整改 → 复验 | 5 个阻塞项 **全 PASS**；复验另抓 7 项（审计无锁 / 键名写错 / 守卫仍不 show 等），已全部整改 | `docs/验证记录-2026-10-17.md` §11–§12 |
| 2026-10-18 | 独立审查（v9.5.0）+ 复验轮（G69-21） | 2 阻塞（凭据随 origin 泄漏给镜像、测速按钮 TypeError）+ 8 Required 全部修复；复验再提 3 HIGH 并修复；顺带修退出 0xC0000409 与 CLI 事件循环误报 | `evidence/N11-review-fixes.json`、`docs/验证记录-2026-10-17.md` §14、`reports/change-report-2026-10-18-security-review.html` |
| 2026-10-17/18 | 计划文档二次审计 + 独立盲点扫描（G69-20） | 计划台账：12 已闭环 / 2 未闭环 / 1 不适用，`计划书/` 归档后删除；盲点扫描 15 项「写了但用户够不到」，修 8 项、登记 7 项 | `docs/archive/plans/20261017/archive-manifest.md`、`reports/change-report-2026-10-17-plan-audit.html`、`docs/验证记录-2026-10-17.md` §12 |

## 审计方法沉淀

1. **对照实验**优于推理：本机绿 / CI 红 → 做干净检出。
2. **判定性守卫**优于「跑一遍没崩」：如「`del w` 后 `weakref` 是否已失效」。
3. **压测**能抓到单线程永远绿的问题（幂等竞态）。
4. **独立审查**必须复验「修复本身」；本轮 10 个缺陷里 4 个是「声称已完成但证据不支持」。
5. **涉及远端状态的结论先 `fetch`**（tag 断档就是未 fetch 的误判）。

## 2026-10-19 G71 队列可观测性 / 事件流 / 诊断包（v9.8.0）

- 范围：新增 `event_bus/errors/batch_state/diag_bundle/retry_panel/event_panel/engine_factory/download_layout`；
  修改 `http_api/engine/retry_queue/atomic_io/progress_file/git.service/main_window/settings/onboarding/help/failure_panel/i18n`。
- 独立审查：`python-reviewer` 无 Critical，5 Required（R1 无限重投、R2 @tag 计数覆盖、
  R3 诊断包泄露本机路径、R4 GUI 启停阻塞、R5 禁用态假成功）全部关闭；7 Suggestions 已处理。
- 验证：ruff/mypy 通过；G71 定向 54 OK；相关回归 142 OK；快集 1316 tests / 0 failures / 0 errors / 81%。
- 真实 E2E：`scripts/e2e_smoke.py` PASS；CLI `--json` 空输入 rc=2、version 9.8.0。
- 未闭环：B4 仓库知识索引、B5 MCP annotations/outputSchema、B6 真实浏览器/真机点击。
