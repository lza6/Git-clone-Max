# 四轨验收 SOP（UI 截图 / 真实调用 / 真实 E2E / 独立复审）

> 来源：v9.20.0（Rev17）四轨验收实战（2026-11-14）。全局 skill `four-track-acceptance`
> 是通用版；本文件是**本项目落地版**（脚本路径、既有轨道、踩过的坑）。

## 什么时候用

发布前（publish_release 之前）、大批次收口后、用户点名「四类验收」时。

## 四轨与本项目脚本

| 轨 | 做法 | 证据落点 |
|----|------|---------|
| UI 截图 | `scripts/ui_screenshot_audit.py <out_dir>`（34 张：4 主题×5 尺寸+8 对话框）+ **Rev17 面逐按钮真点**（QTest 真点击断言可观察结果，不是只截图） | `reports/ui-<ver>-audit/` |
| 真实调用 | `scripts/realcalls_rev17.py`（MCP stdio 三跳+preview_id）+ CLI 退出码矩阵（0/1/2/3） | `evidence/realcalls-*.json` |
| 真实 E2E | `scripts/e2e_smoke.py` + 快集九轮 + `dist` exe 隔离真克隆 + 真下载（字节数+文件签名双验证） | `evidence/rev17-fast.json` |
| 独立复审 | `claude -p --tools "" --effort low < prompt文件`（四域拆分，**stdin 文件重定向不用管道**） | `evidence/code-review-*.md` |

## 踩过的坑（每条都真实发生）

1. **复审 diff 快照可能旧于工作区** → 5 项假阳性；复审前先 `git status` 确认快照点。
2. **except OSError 放前面会吞掉子类分支**（URLError/HTTPError/TimeoutError/
   RemoteDisconnected 都是 OSError 子类）→ 网络重试变死代码。守卫：
   AST 顺序断言（test_rev17_release_fetch.TestReviewFixes）。
3. **302 跨 host 重定向复制 Authorization** → `_SafeRedirect` 剥离（G69-21 同款）。
4. **字符符号字形缺失**（▸ U+25B8 雅黑无字形）→ D22 禁令涵盖所有字符符号，
   不止 ✓✕⚠⊘；icon 替代方案要验证 sizeHint 高度变化（DownArrow +3px 裁切案例）。
5. **证据文件必须是合法 JSON**：把打印文本当证据落盘 = 假证据（realcalls-cli-matrix
   曾混入 heredoc 打印）。
6. **windows 控制台 GBK**：subprocess 显式 `encoding="utf-8"`；PYTHONIOENCODING=utf-8。
7. **真下载产物不进库**：`release-downloads/` 已 gitignore（模式化 `reports/ui-*` 同理）。

## 汇总

四轨全 PASS → `evidence/acceptance-<rev>.json`（含 findings_fixed 与
false_positives_rebutted 两个清单——**驳回也要留证据**，防止下轮复审重复报）。
