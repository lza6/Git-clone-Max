# 归档：Rev10 计划草稿（2026-11-08）

> 归档依据：`.agent/skills/plan-doc-audit.md`（计划文档闭环审计与清理）。
> 本目录保存的是**另一会话在 `docs/plans/` 下写的未跟踪草稿**（Rev10 part1–5），
> 内容已由本轮（2026-11-08）逐条实施或明确登记为不做。**归档保留证据，不入执行依据。**

## 逐条状态（以代码为准，2026-11-08 复核）

| 任务 | 状态 | 证据 |
|------|------|------|
| P-01 批次计划纯函数 `app/batch_plan.py` | 已落地（v9.18.0） | `gcm/app/batch_plan.py` + `tests/test_g84_batch_plan` |
| P-02 预览差异表 UI | **本轮补上** | `gcm/ui/batch_preview_dialog.py`（下载页「预览…」） |
| P-03 CLI/MCP dry_run | 已落地（v9.18.0） | `cli.py` `--dry-run`、`mcp_server.py` `dry_run` |
| O-01 操作台账 oplog | 已落地（变体：JSON 台账，v9.18.8） | `gcm/util/oplog.py` |
| O-02 撤销入口 | 已落地（UI 侧；CLI 侧未做） | `gcm/app/undo.py` + `ui/undo_bar.py` |
| G-01 git 命令环形缓冲 | **本轮补上** | `gcm/app/git_command_log.py` |
| G-02 git 命令查看入口 | **本轮补上** | `gcm/ui/git_command_dialog.py`（失败汇总入口） |
| R-01 prune 三态 | **本轮补上** | `gcm/app/prune.py` + CLI 三旗 + `--yes` 闸 |
| Q-01…Q-07 治理收尾 | 已落地（Q-01 bump_version / Q-02 README 横幅 / Q-03 根目录卫生 / Q-04 守卫 / Q-05 文档同步 / Q-06 清理 / Q-07 收口） | v9.18.0 G84 批 |
| S-01…S-10 稳定性批次 | **本轮全部落地**（S-08 部分：批量提交已做，后台执行器未做） | `docs/验证记录-2026-11-08-Rev10收尾.md` §2.1 |

## 为什么保留而不是删除

本项目 `计划书/` 曾以「全部闭环」为由删除、后被独立复验推翻（见 `known-issues.md` 历史）。
这些草稿里有**判定依据与失败场景**（例如 S-03 的「强推版本号 → 永久坏库」推导），
删除会丢证据；归档后不再作为执行依据，但可追溯。
