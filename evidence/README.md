# evidence/ — 逐节点证据

> 每个文件 = 一个任务节点的**可复核证据**。只写本轮真实执行的命令与输出摘要；
> 没跑就写 `null`，不搬运历史数字。
> 索引与验收口径见 `test_ledger.md`；门禁规则见 `policy.md`。

## 文件命名

`<节点ID>-<短名>.json`，例如 `N1-plan-doc-audit.json`。

## 字段

| 字段 | 含义 |
|------|------|
| `task_id` | 节点 ID（与 `docs/archive/working/` 下的 workflow_status 归档对应） |
| `goal` | 该节点的验收目标 |
| `status` | `done` / `partial` / `blocked` / `failed` |
| `commands[]` | `cmd` + `exit_code` + `output_tail`（真实输出尾部） |
| `files_changed[]` / `files_created[]` | 改了什么 |
| `tests` | 本轮运行数/失败数/错误数 |
| `coverage` | 覆盖率；未知写 `null` |
| `seams_touched[]` | 触碰的接缝 ID（见 `test_ledger.md` S01–S14） |
| `mock_scenarios[]` | 用 mock 覆盖了哪些外部边界场景 |
| `limitations[]` | 未验证项、外部限制、合理假设 |

## 纪律

- **禁止**把「静态阅读代码」写成 `commands` 里的运行证据。
- `exit_code` 必须是真实退出码；被管道吞掉时要说明。
- 结论用四档：`已验证` / `静态确认` / `合理推断` / `待验证`。
