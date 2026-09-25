# gcm-clone：Git-clone-Max 批量克隆技能包（G56-1 生态 agent 化）

为 agent（Claude Code / Codex）提供的**批量克隆**能力入口。所有命令都走项目
现有 CLI（含 `--json`），不直接调用 PyQt6 UI。

## 适用场景
- 从 URL 列表文件/标准输入批量克隆 GitHub/GitLab/Gitee 等仓库到指定目录。
- 需要机器可读结果（status/action/path/msg/counts）交给脚本或 agent 决策。
- 本地裸仓/仓库目录（`file://`）测试，不触网。

## 关键约束
1. 只写 `--dir` 指定的输出目录（默认 `<cwd>/clones`）；**禁止**改动项目源码/数据库/既有测试。
2. 输出统一契约：见 `docs/agent-contract.md`（顶层 `version/ok/counts/results/invalid`）。
3. URL/路径含凭据时输出自动 `redact` 打码，不要自行拼日志。
4. 退出码语义：`0` 全成功 / `1` 有失败 / `2` 无有效输入。

## 用法

```bash
# 文本进度（stderr 在 --json 模式过滤掉）
python -m gcm --cli --dir ./clones urls.txt
# JSON 输出（stdout 即 JSON；进度到 stderr）
python -m gcm --cli --json --dir ./clones urls.txt
# stdin 输入
cat urls.txt | python -m gcm --cli --json --dir ./clones -
```

## 脚本
- `scripts/clone-list.py`：读取 `urls.txt` 并调 CLI `--json`，输出汇总 + 失败清单，退出码透传（0/1/2）。
- `scripts/gcm_smoke.py`：本地裸仓 E2E 冒烟（临时目录建 2 裸仓克隆），验证 CLI --json 全链路可用。

## 验收清单（本包交付即自检）
1. `python scripts/gcm_smoke.py` 输出 `GCM-CLONE SMOKE OK`。
2. 真实 3 裸仓 E2E（`tests/test_g49_cli.py` 与 `tests/test_g56_cli_json.py` 均可跑绿）。
3. `--json` 顶层 schema 与 `docs/agent-contract.md` §2 一致。
