# Git-clone-Max 黄金代码范例（2026-09-26 初稿；2026-10-13 校准）

> 从真实实现提炼的**可复用范式**，供后续开发/其他项目参考。每条含：位置 + 模式 + 为什么好。
> 版本号不写死在本文件：唯一事实源是 `gcm/__init__.py` 的 `__version__`。

## 1. 引擎单线程写库（防 SQLite 死锁范式）
- 位置：`gcm/app/engine.py::_on_result`
- 模式：worker 只回传结果；DB 写入/进度落盘全部由 engine 主线程统一执行。
- 为什么好：SQLite 跨线程写是死锁/锁错乱主因；单写者彻底规避。复用于任何"多生产者→单消费者"场景。

## 2. 原子写文件（防崩溃损坏范式）
- 位置：`gcm/util/atomic_io.py::write_text_atomic`（progress/settings/retry/history/lists 全部接入）
- 模式：**唯一 tmp 名**（pid+tid）→ 写入 + `flush`/`fsync` → `os.replace`（带 12 次退避）
  → POSIX 再 `fsync` 目录；失败上抛不静默吞。
- 为什么好：崩溃/断电不产生半写文件；并发写不互相截断；Windows 杀软/索引器短暂占用
  也不会让整次保存静默失败。文件类持久化的标准姿势。

## 3. redact 全出口打码（防凭据泄漏范式）
- 位置：`gcm/util/redact.py` + 各出口调用（日志/CLI json/MCP/HTTP API）
- 模式：定义单一 redact()，所有可能输出 URL/凭据的出口统一过一遍。
- 为什么好：集中防御，避免漏网；测试可断言"输出无明文 token"。

## 4. 信号批聚合（防高并发 UI 卡死范式）
- 位置：`gcm/app/engine.py::_emit_line` / `_flush_line_buf`（250ms 合并窗口 + 阈值 200 冲刷）
- 模式：高频信号先入内存缓冲（锁保护），QTimer 批量 emit；finished 前强制 flush。
- 为什么好：32 并发时逐行 emit 会信号风暴；批量合并显著降 UI 事件数。复用于任何"高频率低价值事件"。

## 5. 批次流控惰性补投（防 OOM 范式）
- 位置：`gcm/app/engine.py::launch` + `_replenish`（>64 入队前 64，完成一个补一个）
- 模式：去重后限量入队，完成事件触发补投（`_pending_queue.pop(0)`）。
- 为什么好：峰值内存受控，长队列不积压。复用于"大列表 + 有限并发"的消费场景。

## 6. 注入时钟可测（防定时器不可测范式）
- 位置：`gcm/app/alerts.py::AlertScanner(now_fn=...)` / `gcm/db/progress_gc.py`
- 模式：类构造注入 `now_fn`/`clock`，测试传假时钟断言 24h 去重/退避。
- 为什么好：定时逻辑确定可测，不 sleep。所有"时间敏感"逻辑应如此设计。

## 7. JSON 契约单一事实源（防契约漂移范式）
- 位置：`docs/agent-contract.md` + `tests/test_g56_contract.py`（断言 schema 与文档一致）
- 模式：契约文档 + 测试双向锚定；CLI/MCP/HTTP 共用 schema。
- 为什么好：agent 接入方有稳定契约；改动即测即知漂移。复用于任何对外 API。

## 8. 纯函数校验器（防运行时才发现范式）
- 位置：`gcm/ui/qss_validate.py`（大括号/引号/选择器/属性白名单）+ theme 导出前调用
- 模式：无 Qt 依赖的纯函数静态校验，坏输入返回错误列表。
- 为什么好：主题改坏在测试/构建即发现，不在用户运行时炸。配置/模板类输出都应校验。

## 9. 反向自检守卫（防「守卫是空壳」范式）

- 位置：`tests/test_g66_patch.py::TestContractVersionSingleSource.test_guard_catches_evasive_spellings`、
  同文件的 `TestNoCrossThreadWidgetAccess`（取历史 commit 跑同一扫描器断言能抓到违规）
- 模式：守卫**自己也要被测试** —— 把已知的漂移/规避写法喂给守卫，断言它真的报警；
  E2E 级守卫还要拿「修复前的版本」作输入，证明它能复现旧缺陷。
- 为什么好：只跑「现在全绿」无法区分「守卫有效」与「守卫没在检查」。
  项目实测教训：G67-7 的契约文档版本漂移在**所有守卫全绿**的情况下发生，
  因为守卫只覆盖了「代码生产者」没覆盖文档；补守卫时必须同时补反向自检。

## 10. 进程内事件总线（防「状态只活在 UI 信号里」范式）

- 位置：`gcm/app/event_bus.py`（环形缓冲 500、回放、类型过滤、出站 redact）
- 模式：**发布永不阻塞、永不抛**；每个订阅者独立有界队列，慢订阅者丢最旧并计数。
  同一份事件同时供 UI/SSE/诊断包消费，新增消费者不改引擎。
- 为什么好：状态变化只写一次；agent 不必轮询数据库；出问题能导出按时间排序的事件流。

## 11. 纯状态机记账（防「计数对不上」范式）

- 位置：`gcm/app/batch_state.py`（queued→running→终态；终态不可回退）
- 模式：转换表 + 幂等 mark + `invalid_transitions` 计数；非法转换**不抛**（生产路径不能被卡住）
  但要可观测。
- 为什么好：批次完成数/成功率不再靠散落的手工 `+= 1`；漏记/迟到结果不会让 UI 永久 busy。

## 12. 一键诊断包（防「报障靠截图」范式）

- 位置：`gcm/app/diag_bundle.py` + `ui/diag_dialog.py` + 主窗口「诊断包」
- 模式：报告 + 最近事件 + 日志尾部 → **全部 redact** → zip 原子落盘 + manifest
  （版本/时间/文件清单/截断说明）。明确排除 settings/DB/仓库内容。
- 为什么好：用户只需发一个文件；不含凭据；对方能核对版本与缺失项。
