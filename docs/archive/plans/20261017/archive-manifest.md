# 归档清单 — 2026-10-17 计划文档审计

> 本目录存放**已审计的计划/台账类文档**。归档 ≠ 认定全部完成；
> 每条任务的实际状态见下方台账。任何「已删除」的文档都可以用
> `git show <commit>:<原路径>` 取回。

- **审计日期**：2026-10-17
- **审计依据**：`policy.md` §5 · `.agent/skills/plan-doc-audit.md`
- **仓库基线**：`main`，v9.4.6（`gcm/__init__.py:__version__`）
- **判定方法**：只看生产调用点 / UI 入口 / 本轮真实运行，不看文档里的 ✅

---

## 1. 归档文件

| 原路径 | 现路径 | 性质 | 归档理由 |
|--------|--------|------|---------|
| `计划书/README.md` | `计划书-README-墓碑-20261014.md` | 空目录墓碑（1.1KB，仅记录迁移去向） | 目录已无任何未闭环任务；指向的事实已全部迁入 `docs/` 与 `CLAUDE.md` §6。保留墓碑会造成「计划的唯一事实源在根目录」的错觉。 |

> `计划书/` 下的 9 个台账文件（`下一步改进指南.md`、验收清单、project_specs、
> db_structure、规划总览、参考结果计划指南、跨端响应式优化计划、v8 历史、扫描证据 zip）
> 已于 `e6a8759`（2026-10-14）删除。本次归档的是它们留下的**墓碑**。

---

## 2. 任务台账（对原《下一步改进指南》56 条中可判定项的复验）

> 完整逐条复验见 `workflow_status.md` §2（2026-10-16 独立复验）与
> `docs/验证记录-2026-10-16.md`。下表是**截至 v9.4.6 的最终状态**。

| ID | 原始任务 | 声称 | 复验后的实际状态 | 证据 |
|----|---------|------|-----------------|------|
| G52-1 | 失败自动重试队列 | ✅ | **已闭环**（设置页控件 + 引擎重投 + 复用批次上下文） | `settings_panel` retry_* 控件；`engine.py` 重投；`TestRetryReusesBatchContext` |
| G52-3 | 看门狗续跑 | ✅ | **已闭环**（设置页开关/间隔 + 实例化） | `TestG69_18WiringClosure` |
| G52-5 | 失败原因聚合面板 | ✅ | **已闭环**（真实可用） | `ui/failure_panel.py` |
| G52-6 | 并发自适应记忆 | ✅ | **已闭环** | `engine.py` `concurrency_state.json` |
| G53-1 | 多维评分卡（`health_card` 列 + 雷达图） | ✅ | **未闭环**：只有派生视图，未落列、无雷达图 | `grep -rn health_card gcm/` 无结果；`known-issues.md` |
| G53-2 | 阈值告警引擎 | ✅ | **已闭环**（`on_alerts` 已接线，弱引用 emit） | `TestG69_18WiringClosure` |
| G53-5 | 定时报表 | ✅ | **已闭环**（调度器有生产调用点，真实生成 CSV） | `_start_report_scheduler`；验证记录 2026-10-16 |
| G54-4 | 引导清单 | ✅ | **已闭环**（v9.4.3；5 项，首启提示一次） | `ui/checklist.py`；`test_g69_checklist` |
| G56-2 | MCP server | ✅ | **已闭环**（实测 initialize/tools/list） | `test_g56_mcp*` |
| G56-4 | 本地 HTTP API | ✅ | **已闭环**（只读 + 回环绑定） | `test_g56_api*`；`TestHttpApiBindHardening` |
| G58-1 | 统一限流 | ✅ | **不适用（已撤销）**：`rate_limit.py` 在 v9.2.0 因零生产调用被删除；git 侧限速由 `settings.rate_limit_kbps` 承担 | `gcm/__init__.py` changelog 9.2.0 |
| G58-2 | 镜像测速择优 | ✅ | **未闭环**：只有静态 `mirror_prefix` 配置 | `grep mirror gcm/` 无测速逻辑 |
| G58-3 | 代理凭据加密落盘 | ✅ | **已闭环**：`SettingsStore.save` 对 `proxy` 调 `_encrypt_token`，加载时 `_decrypt_or_keep`；代理以完整 URL 注入 `http_proxy/https_proxy`（git 原生支持 `user:pass@host`，无需再拆字段） | `settings.py:366,407-409`；`service.py:492-494` |
| G58-5 | 自建实例识别/认证头 | ✅ | **已闭环** | `host_auth_types` |
| G59-7 | 企业 IM 告警 | ✅ | **已闭环**（UI 配置入口已补） | `TestG69_18WiringClosure` |

---

## 3. 删除清单

| 路径 | 动作 | 前提检查 |
|------|------|---------|
| `计划书/README.md` | 归档 → 删除 | ① 逐条审计完成（§2）② 已入归档目录 ③ 已列清单 ④ 目录内无未闭环任务 |
| `计划书/`（空目录） | 删除 | 已 `rmdir`，无残留 |

**未删除的文档及原因**：

| 文档 | 原因 |
|------|------|
| `docs/RELEASE_CHECKLIST.md` | **活 SOP**，每次发版都要走，不是计划 |
| `docs/architecture.md` / `data-model.md` / `agent-contract.md` / `code-gems.md` / ADR | 事实源，唯一权威 |
| `docs/验证记录-*.md` / `docs/reports/*.html` / `docs/audit-v8.2.0-*.md` | 历史证据快照，删了就无法复核 |
| `docs/perf-observability.md` | 含 2 条**未实施**的候选优化（标为可选增强，非缺陷） |
| `docs/慢测定位.md` | 耗时唯一事实源；文中「测试基础设施缺陷尚未闭环」已于 v9.4.0–v9.4.6 修复（`_can_show_modal` + 引用环），本次同步更正 |
| `docs/product-brief.md` | 产品定位；文中「Webhook 幂等/审计未实现」已过期，本次同步更正 |
| `.agent/**` / `skills/**` | 活技能与活记忆 |

---

## 4. 仍未闭环（保留跟踪，不在本目录）

| 项 | 去处 |
|----|------|
| G53-1 `health_card` 列 / 雷达图 | `CLAUDE.md` §6 · `.agent/memory/known-issues.md`（需 DB 迁移 + 产品决策） |
| G58-2 镜像测速择优 | 同上 |
| 两个跨进程测试高负载偶发 | `.agent/memory/known-issues.md` |
| CI 未锁 PyQt6 版本 | 同上 |

---

## 5. 恢复方法

```bash
git show e6a8759^:计划书/README.md
git show e6a8759^:计划书/下一步改进指南.md
```

墓碑内容也保存在本目录的 `计划书-README-墓碑-20261014.md`。
