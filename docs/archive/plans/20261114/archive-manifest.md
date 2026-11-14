# archive-manifest · 20261114 · Rev15 UI 计划文档归档 + Rev18 施工指南归档

> 归档时间：2026-11-14（两批）。第一批触发：Rev18 B3-0（施工指南 §B3-0）。
> 第二批触发：Rev18 v9.21.0 发布完成（tag/Release/双资产在架，main=53d4bf2），
> 按《下一步改进指南》§6 生命周期承诺「全部闭环后归档，不留僵尸文档」执行。
> 流程依据：`.agent/skills/plan-doc-audit.md`（审计 → 归档 → 从活文档目录移除）。

## 1. 归档清单

| 原路径 | 归档路径 | 归档时状态 |
|---|---|---|
| `docs/plans/UI-跨设备适配与设计系统-Rev15-20261110.md` | `docs/archive/plans/20261114/UI-跨设备适配与设计系统-Rev15-20261110.md` | **R15-00…13 全部执行完毕**。执行证据：`docs/验证记录-2026-11-10-Rev15.md`、`docs/验证记录-2026-11-11-Rev15-UI.md`、`evidence/rev15.json`、`evidence/rev15-ui.json`。附录 C 状态表逐项「已验证」 |
| `docs/plans/下一步改进指南.md` | `docs/archive/plans/20261114/下一步改进指南-Rev18-20261114.md` | **Rev18 全批次闭环**（附录 B 自身状态表已回填 ✅）。B0 G85 收口 / B1 错误三段式 11 处迁移 + AST 守卫 / B2 覆盖率精确 ≥81 / B3 三小项 / B4 清单交付 / V 发布。执行证据：`docs/验证记录-2026-11-14-Rev18.md`、`evidence/rev18-b0-b3.json`、`evidence/rev18-coverage.json`、`evidence/health_rev18.json`、`evidence/acceptance-rev18.json`（四轨） |

## 2. 本 manifest 不声称「全部闭环」

未闭环项（如实登记，不藏）：
- **真机双屏/拔屏/混合 DPI/贴靠**：beads `ph3` 维持开放，等硬件窗口（离屏只能做常量消费守卫强度）；
- **屏幕阅读器（NVDA/JAWS）真人流程**：五项静态已验证 ≠ 真人流程已通过，beads `d78` 维持开放；
- **撤销链路（R-01 prune 三态）真人点击**：同上，beads `d78`。

## 3. 仍开放项（如实登记，不藏）

- **B4 真机验收（4 类 17 项）**：清单在 `docs/真机验收清单-Rev18.md`，**未执行**
  ——双屏/拔屏/混合 DPI/贴靠等硬件窗口依赖（原 beads `ph3` 口径）、
  撤销链路真人点击 + 屏幕阅读器真人流程（原 beads `d78` 口径）。
  注：该两条 issue 因 beads 被动导出链缺口（embeddeddolt 只读期间创建）不在当前本地库
  （`bd list` 0 open，`issues.jsonl` 为 24 条全 closed 的旧导出）；口径以
  `docs/验证记录-2026-11-14-Rev18.md` §7 为准。
- **macOS CI `test_ui_compact_visibility` 预存红**：维持 CUT（无 macOS 本机）。

## 4. 下一轮入口

`docs/plans/` 现已清空（本 manifest 所在目录为归档区，不是活文档区）。
下一轮入口：`bd ready`（当前 0 open）+ 本 manifest §3 + `CLAUDE.md` §6 已知边界。
恢复方法：`git show HEAD:docs/plans/下一步改进指南.md`（或任一历史 sha）。
