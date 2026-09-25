# gcm-resume：Git-clone-Max 断点续跑技能包（G56-1 生态 agent 化）

为 agent 提供的**断点续跑/增量更新**入口。核心语义：对同一批 URL 再次调用
CLI（`check_existing` 预检 + 引擎去重），已完成且目录在的仓库跳过
（status=success, action=skipped），未完成/失败的重跑；本技能脚本负责把
`--json` 结果中的 `success+skipped` 与 `failed` 分开展示，供 agent 判断
「还剩哪些没成功」。

## 适用场景
- 上一次批量克隆中途失败/被杀，重启后继续补齐剩余仓库。
- 例行增量更新：对已有克隆目录再跑一次 CLI，`fetch` 检查增量。

## 关键约束
1. 同一批 URL 重跑**不重复克隆**：引擎按 folder_name 去重，目录在则跳过。
2. 只写 `--dir` 输出目录；**禁止**动源码/数据库/既有测试。
3. 结果打码与契约 schema 一致（`results[].status` 可含 skipped/conflict/cancelled）。

## 用法

```bash
python skills/gcm-resume/scripts/resume.py <urls.txt> --dir <out>
```

## 脚本
- `scripts/resume.py`：调 CLI `--json`，输出
  `{version, ok, counts, results, invalid, summary:{remaining_failed, already_ok}}`，
  退出码 0=无失败（含跳过）/ 1=仍有失败 / 2=无有效输入。

## 验收清单（本包交付即自检）
1. 首次跑 2 裸仓 → 全部 cloned，退出 0。
2. 同批再跑一次 → 全部跳过（action=skipped）或 fetched（增量检查），退出 0。
3. 人为制造一个失败（目标目录占用）→ 退出 1 且 `remaining_failed >= 1`。
