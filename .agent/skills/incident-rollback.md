# 故障止损与回滚 SOP

## 触发
发布后核心链路异常（启动失败 / 崩溃 / 数据异常 / 关键接口不可用）。

## 第一步：止损（先于定位）
1. **停止扩散**：不下发新版本；必要时撤回 Release（GitHub: 删除或标 pre-release）。
2. **保留现场**：`error.log` / `app.log` / `notify.log` / 崩溃栈（`faulthandler`）。
3. **记录时间线**：何时发现、影响版本、影响面、已做动作。

## 第二步：定位（用证据，不猜）
- 桌面工具优先看：`data/error.log`（全局兜底）、`data/app.log`（结构化）、
  `data/notify.log`（通知审计）。
- 是否与最近一次变更相关？查 `.agent/memory/change-index.md`。
- 能否在干净检出复现？`git archive` 到临时目录跑。
- 崩溃类：`-X faulthandler` 拿 C 栈；`PYTHONMALLOC=debug` 改变时序以二分。

## 第三步：回滚
| 对象 | 回滚方式 |
|------|---------|
| 代码 | `git revert <sha>`（**不** reset / 不 force push） |
| 发行版 | 在 GitHub 把上一个稳定 tag 标为 Latest |
| 用户数据 | 不自动回滚；`settings.json.bak` 由程序维护，DB 迁移只加列不删列 |

## 第四步：复验
按 `verification-matrix.md` 跑最小相关集，附命令+输出。

## 第五步：留痕
在 `.agent/memory/audit-history.md` 追加：时间、影响版本、根因、修复、证据、后续动作。

## 常见坑
- **只修表面**：如 CI 红灯靠关 GC 绕过 —— 根因还在。必须打断因果链。
- 崩溃点随机漂移 = 竞态；不要按「某个用例有 bug」修。
- 涉及远端状态的结论先 `git fetch` 再下。
