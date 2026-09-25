# gcm-diag：Git-clone-Max 网络/环境诊断技能包（G56-1 生态 agent 化）

为 agent 提供的**环境诊断**入口。脚本复用 `gcm/app/diag.py`
（git 存在性 / TCP 443 / GitHub API / 代理）与 CLI `--diag`，输出分级报告
（绿=全部正常 / 黄=部分缓慢 / 红=存在不可用项）。

## 适用场景
- 克隆失败前先诊断网络/工具链：git 是否可用、github.com:443 是否可达、
  GitHub API 是否响应、是否配置代理。
- agent 在触发真实克隆前，用 `--diag --json` 快速判断「红」则先不克隆。

## 关键约束
1. **只读**：不写盘、不克隆；仅发起轻量探测（每项自带超时 ≤8s）。
2. 诊断会**触网**（TCP/GitHub API）；无网环境下 items 显示 err，属预期。
3. 输出打码：探测 detail 中的路径/异常经 `redact`。

## 用法

```bash
# 文本分级报告
python -m gcm --cli --diag
# JSON 分级报告（agent 解析 grade + items）
python -m gcm --cli --diag --json
# 技能脚本封装（等价上面，输出统一 JSON）
python skills/gcm-diag/scripts/diag.py [--json]
```

## 脚本
- `scripts/diag.py`：包装 `--diag`，输出 `{grade, items:[{name,status,detail}]}`，
  退出码 0=绿 / 1=黄或红（文本模式）。

## 验收清单（本包交付即自检）
1. 无网环境运行：git 项 ok，TCP/API 项 err，总评红，退出码 1（不卡死 ≤10s）。
2. 有网环境运行：总评绿/黄，退出码 0/1 与 grade 一致。
3. `--json` 输出可被 agent 直接解析（schema 与契约 §2 兼容）。
