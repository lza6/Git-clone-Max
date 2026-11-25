# 周报（docs/plans/weekly.md）

> 按周追加，一条任务一段。只记事实与证据指针。

## 2026-W48（2026-11-23 ~ 2026-11-29）

- **2026-11-26 · 仅文档交付：下一步改进指南（Rev21 候选路线）**
  - 基线：v9.22.0（tag `2ccbe19`），工作区 clean `14806f4`，DRIFT 0，开放 beads 仅 `13k`/`cuz`（硬件人工项）。
  - 产出：`docs/plans/下一步改进指南.md`（含现状基线、P0–P3 问题清单、三方案对比、v9.23.0 路线、P21-1..5 原子卡、TDD/验收矩阵、风险回滚、清理清单、技能检查、AI 提示词建议、待执行验收项 V1–V9）；`docs/plans/riper5_20261126_002538.md`。
  - 关键发现：`scripts/dev_metrics.py` 的 `ACCEPTED_OVERSIZED` 缺 `progress_table.py`，与测试登记不同口径（自报一致=False）→ P3-I4，建议随 9.23.0 一行级修复 + 单一来源化守卫。
  - 未执行（按模式约定）：所有测试/构建/发布命令；均已写成待执行验收项并附证据要求与回滚。
  - 待用户拍板：v9.23.0 范围（方案 1+2 或仅 9.22.1 patch）；v9.24.0 候选（3a 知识索引导出 / 3b 无障碍工程化）。

- **2026-11-26 · Rev21 执行计划交付 + 用户拍板**
  - 产出：`docs/plans/Rev21执行计划-20261126.md`（自包含执行版；先复核《下一步改进指南》未过时后继承其结论）。
  - 拍板：D1 v9.23.0 全量（P21-1+2+3）、D2 P21-3 纳入、D3 v9.24 候选都不做。
  - 验证：`doc_drift_check.py` DRIFT 0；`test_g67_docs_freshness` 11 例绿；引用路径与行数全核对（progress_table 808 / main_window 3597 / settings_groups 800 / repo_db 766）。
  - 状态：待执行——用户过目后按执行计划 §3 领卡。

- **2026-11-26 03:41 · v9.24.0 发布（B 批 + Rev21 工具链）**
  - commits：`0117ef9`（B 批五批能力 + Rev21 工具链）→ `3a1f64f`（release）→ `a203e04`（发布记录回填）；tag `v9.24.0`。
  - B 批：instance_bus / path_guard / startup_recovery / catalog 等恢复与防呆模块；except 预算 797→823 一次性登记（pass-only 未涨）。

- **2026-11-26 · Rev22 终极审计轮 + v9.24.1 修复版**
  - 发现与修复：
    (1) **统计中心关闭闪退（0xC0000409）**：聚合线程 `finished→deleteLater` 后 wrapper 已删，`closeEvent` 里 `t.isRunning()` 抛 RuntimeError → PyQt6 abort。这同时解释了截图审计脚本长期在 `dialog_statistics` 之后中止、后 4 个对话框从未被截到。修复：`sip.isdeleted` 防御 + `wait()` 无超时；守卫 `tests/test_rev22_fixes`。
    (2) **历史按钮 U+25BE 无字形（tofu）+ 管理表 4 个列头 i18n 键双缺**（作者/仓库、上次同步、HEAD、历史；`tr(变量)` 是 i18n 守卫声明过的盲区）。修复：纯文本按钮、补 4 键、AST 字形守卫 + 列头键守卫。
    (3) **winget 清单 sha256 漂移**（快集 `test_g80_winget_manifest` 红，v9.24.0 发布漏步）→ 按产物重生成，`--check` exit 0。
    (4) sparkline 主题兜底写死旧品牌色 `#4cc2ff` → 中性灰。
  - 验证：定向 62 例 OK；截图审计**首跑完整 38 张 exit 0**（4 个此前不可达对话框首次入库）；`health_check` v9.24.0 产物 started_ok/smoke 1803ms。
  - 计划文档：`docs/plans` 三份按 plan-doc-audit 归档至 `docs/archive/plans/20261126/`；`计划书/UIUX与前端架构审查计划-20261126.md` 的 B1–B3 执行完毕，B4/B5 待产品决策。
  - 发布：commit `dc9ad12` · tag `v9.24.1` · Release 双资产上传且服务端 sha256 复验一致（`https://github.com/lza6/Git-clone-Max/releases/tag/v9.24.1`）；放行快集 2444 例 0 失败 / 覆盖率 81%（`evidence/rev22-release-gate.json`）。
