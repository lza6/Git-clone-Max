# gcm-report：Git-clone-Max 报表技能包（G56-1 生态 agent 化）

为 agent 提供的**报表导出/统计**入口。脚本复用 `gcm/reports.py`
（CSV / Markdown）与 `gcm/db/repo_db.py` 统计，全部**只读**数据源
（repos.db PRIMARY 连接，WAL 下不阻塞写方），产出文件写到指定报表目录。

## 适用场景
- 把数据库里的仓库/同步历史导出成 CSV（Excel 友好，utf-8-sig）或 Markdown。
- 给 agent/下游系统喂结构化统计（仓库数、成功/失败、host 分布）。

## 关键约束
1. **只读**：不写 repos.db，只写出报告文件；不要动 `--dir` 之外的文件。
2. 报表列固定（`owner/repo/host/status/action/message/commits/duration_ms/started_at`），
   不含 token/host_tokens/ssh_key 等敏感列（沿用 G44-1 规则）。
3. 数据目录默认 `GCM_DATA_DIR`，否则 `data/`（与主程序一致）；脚本接受 `--data-dir`。

## 用法

```bash
python skills/gcm-report/scripts/export-report.py --out ./reports --format csv [--data-dir <dir>]
python skills/gcm-report/scripts/export-report.py --out ./reports --format markdown
```

## 脚本
- `scripts/export-report.py`：导出 CSV/Markdown 到 `--out`；JSON 汇总包含
  `{version, ok, counts, results:[{path, rows, format}], invalid: []}`，退出码 0=成功 / 1=失败。

## 验收清单（本包交付即自检）
1. 对空库运行：CSV/Markdown 均生成，行数为 0 仍退出 0。
2. 对含一仓库运行：CSV 第一行表头 == `EXPORT_COLUMNS`，不出现 token 字段。
3. 输出为合法 JSON（agent 可直接解析）。
