# Git-clone-Max 黄金代码范例（v9.0.0）

> 从真实实现提炼的**可复用范式**，供后续开发/其他项目参考。每条含：位置 + 模式 + 为什么好。

## 1. 引擎单线程写库（防 SQLite 死锁范式）
- 位置：`gcm/app/engine.py::_on_result`
- 模式：worker 只回传结果；DB 写入/进度落盘全部由 engine 主线程统一执行。
- 为什么好：SQLite 跨线程写是死锁/锁错乱主因；单写者彻底规避。复用于任何"多生产者→单消费者"场景。

## 2. 原子写文件（防崩溃损坏范式）
- 位置：`gcm/db/repo_db.py::save_progress` / `gcm/db/settings.py::_save` / `gcm/app/retry_queue.py::_save`
- 模式：`tmp 文件写入 → os.replace(tmp, target)`（原子替换），损坏读取兜底空值。
- 为什么好：崩溃/断电不产生半写文件；读取侧 try/except 兜底不崩。文件类持久化的标准姿势。

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
