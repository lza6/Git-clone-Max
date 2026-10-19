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

## G71（v9.8.0）队列可观测性 / 事件流 / 诊断包

- **D7 事件总线采用进程内单例 + 有界环形缓冲**，不引入消息队列/文件日志作为主通道：
  桌面单机工具没有常驻服务端；跨重启审计继续用 `app.log` / `progress.json`。
  事件发布永不阻塞/抛出，慢订阅者丢最旧并计数（`stats.dropped`）。
- **D8 SSE 由 GUI 进程托管**（设置页「本地 API / 事件流」，默认关、仅回环）：
  独立 `--serve` 是另一个进程，EventBus 不互通；若强行让 `--serve` 读 GUI 事件，
  需要文件桥/DB 表，复杂度和一致性风险高于收益。文档必须写明这一边界。
- **D9 错误是否可自动重试只由 `app/errors.classify_error` 判定**：
  429/5xx/网络 → true；401/403/404/磁盘满/平台限制 → false。
  UI 分类、重试队列、SSE `task_error` 共用，禁止再写第二份关键字表。
- **D10 所有 JSON 持久化统一 `util/atomic_io.write_text_atomic`**：
  唯一 tmp（pid+tid）+ flush/fsync + 目录 fsync + 替换退避；失败上抛。
  旧模块的固定 `*.json.tmp` 写法视为缺陷，不得新增。
- **D11 诊断包只含已打码文本**（报告/事件/日志尾部），明确排除
  `settings.json`、数据库、仓库内容；zip 用唯一临时文件 + `os.replace`。
- **D12 长函数拆分按「纯映射/纯构建」优先**：
  `_update` 拆出 fetch/merge/bare 三段；`_launch` 引擎参数映射拆到
  `engine_factory`；下载页控件搭建拆到 `download_layout`。
  不拆高度耦合的控制流，避免为了行数制造回归面。

## G72（v9.8.1）加固决策

- **D13 日志打码放在 Formatter 层**：覆盖 args/异常堆栈与所有 applog 出口；
  用廉价子串预判避免每条日志跑全部正则。
- **D14 原子写用 mkstemp O_EXCL + 白名单 winerror 重试**：只对瞬时占用退避，
  其它权限错误立即失败，避免把真错误拖成数秒。
- **D15 SSE 续传优先 Last-Event-ID**：replay 仅兜底；历史缺口发
  `history-truncated` 注释帧；并发上限 16 + 30s 写超时防线程堆积。
- **D16 重试 jitter 可注入**：生产默认 50%–100% 抖动；测试注入 identity 保证确定性；
  单 tick 上限 50，剩余下轮。

## G73（v9.8.2）DLQ 决策
- **D17 重新入队必须保留完整 spec**：dropped 存 url/folder/ref/local_path；
  否则 @v1/@v2 会克隆到同一目录。
- **D18 attempts 重置为 0**：下一次失败从 1 开始，避免入队即超限。
- **D19 不提供批量 redrive**：防止一次点击造成重试风暴；单条操作。
