# 安全与契约审计报告（v8.2.0，2026-09-26）

> 独立主控审计（真实代码检查 + 命令验证），未改动业务代码。

## 1. SQL 安全

**结论：PASS（无注入/锁死/数据丢失风险）**

| 检查项 | 证据 | 结论 |
|--------|------|------|
| 数据操作参数化 | repo_db.py 全部 SELECT/INSERT/UPDATE/DELETE 用 `?` 占位符 + 参数元组 | ✅ 无注入 |
| f-string execute | 仅 3 处 PRAGMA：`user_version={_SCHEMA_VERSION}` `busy_timeout={_BUSY_TIMEOUT_MS}` `wal_autocheckpoint={pages}`，均为内部常量整数 | ✅ 无用户输入 |
| 锁死防护 | `BEGIN IMMEDIATE`（写事务）+ `busy_timeout=30000` + WAL | ✅ |
| 数据丢失防护 | 迁移链 `PRAGMA user_version` 幂等 + backup_to 一致性备份 | ✅ |

## 2. 凭据与 redact

**结论：PASS（无凭据泄漏出口）**

| 出口 | 证据 | 结论 |
|------|------|------|
| updater token | `_gh_token()` 仅进 `Authorization` 头（内存请求），不进 url/日志 | ✅ |
| Release url 打印 | `browser_download_url` 为公开资产 URL，无凭据 | ✅ |
| CLI --json | `_result_dict`/`_error` 全部过 `redact()` | ✅ |
| MCP/HTTP API | `_sanitize_row` 剔除 token/host_tokens/ssh_key + url redact | ✅ |
| settings 落盘 | token/host_tokens/webhook token 加密（DPAPI/Keyring/XOR） | ✅ |

## 3. 契约一致性

**结论：PASS（文档与实现一致）**

| 契约 | 验证 | 结果 |
|------|------|------|
| CLI --json 顶层 schema | 空输入实测：`{version,ok,counts,results,invalid}` | ✅ 与 agent-contract.md 一致 |
| 退出码 | 全成功 0 / 有失败 1 / 无有效输入 2 / MCP 未启用 3 | ✅ 一致 |
| 错误 schema | `{code,message,redacted}` | ✅ 一致 |
| MCP 工具 | list_repos/search_repos/status/export_report（export 需显式 enable） | ✅ 一致 |
| HTTP 端点 | /version /api/repos /api/repos/search /api/status /api/report /api/clone | ✅ 一致 |

## 4. 并发与资源

**结论：PASS-WITH-NOTE**

| 项 | 结论 |
|----|------|
| SQLite 连接 | 引擎单线程写库，worker 不写 DB（统一 _on_result 落库）✅ |
| progress.json | 原子写（tmp+replace）+ 进程级锁 ✅ |
| 线程/定时器 | drain/shutdown 停 retry 定时器；看门狗/扫描线程 daemon ✅ |
| 注意 | AlertScanner/ReportScheduler 线程由 MainWindow 生命周期管理，未见泄漏路径 |

## 5. 遗留建议（非阻塞）

- [MEDIUM] settings_panel 的 G53 告警/历史保留/定时报表 UI 区未落地（逻辑层已闭环，入口待接）——登记为下一轮 M10 补位
- [LOW] updater L154 print url 建议统一走 redact（防御性，当前无凭据）
- [LOW] 建议 CI 增加 `--diag --json` 冒烟（已本地验证）

## Verdict

**PASS-WITH-WARNINGS**（无 CRITICAL/HIGH；2 个 MEDIUM/LOW 建议项，不影响上线）
