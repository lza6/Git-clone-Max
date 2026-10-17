# 关键决策与理由（decisions）

> 记录「为什么这样做」，避免下一个人推翻后重犯。

## D-01 引用环：改弱引用，而不是继续关 GC

**背景**：CI 连续 80+ 次红灯，Linux/macOS `exit 139`（SIGSEGV）。
**根因**：Qt 控件由**引用环**维持 → 只能等循环 GC → 循环 GC 在任意分配点
（含 `processEvents()` 内部）销毁控件与子定时器 → `activateTimers` 解引用已释放对象。
**曾用的临时处置**：`ci_run_fast.py` 整轮 `gc.disable()` —— 能绿，但是绕过。
**最终决策**：打断环本身（Panel owner 弱引用、信号改用绑定方法、
扫描/剪贴板/拖拽改弱引用、**`_layout_timer` 的 lambda 改绑定方法**），
然后**移除 `gc.disable()`**。恢复正常 GC 后 CI 三平台全绿。
**关键教训**：守卫必须覆盖**生产路径**（`show()`），不能只覆盖方便测试的路径。

## D-02 Webhook 审计只记 `scheme://host`

`redact(url)` 只按**已知参数名**打码，而 Webhook 密钥常在**路径**里
（Server酱 `/<KEY>.send`、Telegram `/bot<TOKEN>/`）或自定义参数（`?k=SECRET`）。
实测三种形态全部明文落盘。决策：审计只记 `scheme://host` —— 排障只需知道「发往哪个服务」。

## D-03 幂等用「锁内查重+占位」

「先查后写」在 40 并发下实测发了 13 次。改为锁内一次性「查重 + 占位」，
发送失败再撤销占位（否则一次失败 = 整个窗口内再也不发）。

## D-04 引导清单「只提示一次」且放在向导提前 return 之前

原实现每次启动都弹；且放在 `first_run_done` 提前 return 之后 → 老用户永远看不到。
现用 `settings.checklist_prompted` 控制，并放在提前 return 之前。
**标记在弹窗成功后才写**（原先是先写，弹窗失败就永不提示）。

## D-05 僵尸字段守卫用 AST，不用文本匹配

文本匹配会被自己的 changelog 命中（字段名就写在变更日志里）→ 假绿。
AST 只统计 `Attribute` / `Name` / `getattr(obj, "名字")`，注释与字符串不算。

## D-06 硬件敏感用例：只跳过用例，不排除模块

早期按**整模块**排除 `test_scanner_perf` / `test_mem_baseline` →
连带砍掉 13 条与硬件无关的正确性用例、覆盖率 81.26%→80.76%。
现改为模块照跑、由 `GCM_FAST_SUITE=1` 让那两个**用例自己** `skipIf`。

## D-07 不新增 `.agent/` 内容重复的文档

`.agent/` 只放**记忆、规则、技能索引**；架构/契约/数据模型等权威内容仍在
`docs/architecture.md` / `docs/agent-contract.md` / `docs/data-model.md`。
`.agent/` 里只写指针，避免同一事实存在两份而漂移。
