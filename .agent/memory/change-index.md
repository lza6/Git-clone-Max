# 变更索引（change-index）

> 「改这块之前先看这里」。按模块记录最近一次重要变更与连带影响。

| 模块 | 最近变更 | 版本 | 连带影响 |
|------|---------|------|---------|
| `gcm/ui/main_window.py` | 引用环修复（绑定方法替代 lambda）、输入区实时校验条、审计/清单入口、**Star 导入按钮/信号/槽、crypto_warning 启动日志** | v9.4.2–v9.4.7 | 若再加 `lambda` 捕获 self，会重新成环（有守卫）；Star 拉取必须走 worker 线程 |
| `gcm/ui/manage_model.py` | **`is_visible` / `stale_days` / 过期颜色角色 / 可见性缓存** | v9.4.7 | `data()` 会为过期行返回 Background/ForegroundRole |
| `gcm/ui/manage_panel.py` | **搜索框 + `setRowHidden` + 收藏/黑名单右键菜单** | v9.4.7 | 过滤用源行号（未接排序代理）；刷新后自动重放过滤 |
| `gcm/ui/settings_panel.py` | **安装日志高亮器 + `_apply_alert_config`** | v9.4.7 | 告警间隔/IM 保存后即时推送给扫描器 |
| `gcm/app/alerts.py` | **`AlertScanner.reconfigure()`** | v9.4.7 | 循环每轮重读 `_interval`，新间隔下一轮生效 |
| `gcm/app/mirrors.py` / `mirror_probe.py` | **新增：镜像候选归一 + `git ls-remote` 探测** | v9.5.0 | 不要用 HTTP HEAD 判断可用性；有凭据禁止走镜像 |
| `gcm/git/service.py` | **克隆前探测择优、失败回退源站；更新失败在镜像/源站间切换** | v9.5.0 | `_mirror_url` 默认不含预设（兼容旧行为）；完整链路走 `_resolve_clone_url` |
| `gcm/ui/settings_groups.py` | 镜像行拆为 `_network_rows_mirror` + 两个开关 + 测速按钮 + 探测超时 | v9.5.0/v9.6.0 | `mirror_prefix` 值可为 str 或 list |
| `gcm/git/service.py` | **认证头 URL 作用域；镜像 origin 规范化；回退探测/retries=0/unshallow 重放** | v9.6.0 | 全局 `http.*extraHeader` 会禁用镜像 |
| `gcm/ui/radar_chart.py` | 新增 6 维雷达图（纯 Qt） | v9.6.0 | 颜色走 `current_palette()` |
| `gcm/app/engine.py` | **shutdown 断信号 + waitForDone(8000)** | v9.6.0 | 关闭时最多多等 8s，避免退出崩溃 |
| `gcm/cli.py` | 等待改轮询 processEvents；新增 `--no-mirror` | v9.6.0 | 不再依赖 QEventLoop.exec |
| `gcm/app/worker.py` | **`on_line` 真正接到 service** | v9.4.7 | git 输出按仓库 index 归属，不再全记到 0 |
| `gcm/ui/{failure,manage,progress,settings}_panel.py` | `owner` 改 **weakref 属性** | v9.4.2 | 不可再写 `self.owner = x`（只读 property） |
| `gcm/ui/checklist.py` | 新增（引导清单） | v9.4.3 | 文案走 `tr()`；键需在 zh/en 同时存在 |
| `gcm/ui/theme.py` | `maybe_validate_url` 改用 `parse_any_repo_url`（原只认 GitHub） | v9.4.4 | 校验语义支持 6 类平台 |
| `gcm/app/notify.py` | 幂等 + 审计台账 + 重试；指纹纳入 URL；审计加写锁 | v9.4.2/v9.4.6 | `maybe_notify` 新增 `data_dir` 参数 |
| `gcm/app/clipboard_watcher.py` | 绑定方法用 `WeakMethod` | v9.4.2 | 普通函数仍走强引用分支 |
| `gcm/app/url_lib.py` | `parse_urls` 跳过 `#` 注释 | v9.4.5 | 与输入区校验条语义一致 |
| `gcm/db/settings.py` | 新增 `checklist_done` / `checklist_prompted`；删除 3 个僵尸字段 | v9.4.3–v9.4.5 | `_DEFAULTS` 自动派生，旧 JSON 兼容 |
| `gcm/mcp_server.py` | stdout/stderr 强制 UTF-8；工具异常转 JSON-RPC 错误 | v9.4.0 | 非 UTF-8 控制台不再崩 |
| `scripts/ci_run_fast.py` | 逐模块落痕 + 失败点名 + faulthandler；**移除 gc.disable** | v9.4.0–v9.4.5 | 改测试面/覆盖率时先读它 |
| `.github/workflows/ci.yml` | `perf-baseline` job；覆盖率摘要步骤加 continue-on-error | v9.4.0 | 硬件敏感用例由独立 job 承接 |
| `gcm/ui/theme.py` | **语义色 on_accent/border_strong/success；QSS 缓存；字号派生；高亮器动态取色** | v9.7.0 | 改 token 会牵动全部 QSS；对比度测试在 `test_g70_ui_upgrade` |
| `gcm/ui/progress_table.py` | **进度 delegate 拆分到 `progress_delegate.py`；250ms 合批；内存字典；批量关排序** | v9.7.0 | 进度不逐行 `setData`；engine index 经列 0 `UserRole+1` 反查；过滤推迟到 `end_bulk` |
| `gcm/ui/layout.py` | 新增断点/夹取/对话框适配纯函数 | v9.7.0 | 修改窗口最小值先看 `test_g70_ui_upgrade.TestWindowMinimum` |
| `gcm/ui/main_window.py` | **顶部可滚动区、页签记忆/快捷键、设置导航、管理页延迟加载、`_rehighlight_log`** | v9.7.0 | 管理页首次激活才加载仓库；新增 UI 逻辑仍应落独立模块 |
| `gcm/db/repo_db.py` | **`list_repos_page` + `readonly=True` 只读快路径** | v9.7.0 | 只读连接会探测 `favorite` 列，旧库回退可写迁移 |
| `gcm/http_api.py` | `/api/repos` 分页 + 只读连接 + 通用 500 错误 | v9.7.0 | 返回 `count/total/limit/offset/truncated`；契约见 `docs/agent-contract.md` |
| `gcm/app/event_bus.py` | **新增：有界环形缓冲 + 回放/类型过滤 + 出站 redact + 慢订阅者丢最旧** | v9.8.0 | 发布永不阻塞/抛；SSE/诊断包共用一个进程级单例；`reset_event_bus()` 仅测试用 |
| `gcm/app/errors.py` | **新增：三段式错误分类 + retryable 判定（唯一判据）** | v9.8.0 | 改分类/文案只改这里；429/5xx 可重试，401/403/404/磁盘满/平台限制不可 |
| `gcm/app/batch_state.py` | **新增：批次状态机（终态不可回退 + 幂等计数）** | v9.8.0 | `invalid_transitions` 可观测；引擎 `_batch_counts` 由它派生 |
| `gcm/app/retry_queue.py` | **`record_failure_ex`/`retry_now`/`remove`/`snapshot`/`stats`；修复 path=None 纯内存丢失** | v9.8.0 | 旧 `record_failure()->bool` 保留；记录新增 category/code/title/cause/action/state |
| `gcm/app/diag_bundle.py` | 新增诊断 zip（报告+事件+日志尾部，全部 redact） | v9.8.0 | 明确不含 settings/DB/仓库内容；zip 原子落盘 |
| `gcm/util/atomic_io.py` | **新增 `write_text_atomic`（唯一 tmp + fsync + 目录 fsync + 退避）** | v9.8.0 | progress/history/lists/concurrency_state 全部接入；不要再自拼 `*.json.tmp` |
| `gcm/http_api.py` | **`GET /api/events` SSE + `/api/events/snapshot`；事件端点不打开 DB** | v9.8.0 | HTTP/1.0 + `Connection: close`；只允许回环；heartbeat/replay/types 有界校验 |
| `gcm/app/engine.py` | **EventBus 发布 + BatchState 记账 + `run_due_retries()`** | v9.8.0 | 事件字段是 agent 契约；改字段同步 `docs/agent-contract.md` |
| `gcm/ui/retry_panel.py` | 新增重试队列面板（立即重试/移除/全部/清空） | v9.8.0 | 队列可为 explicit/engine/读盘三源；`retry_now` 后调 `run_due_retries()` |
| `gcm/ui/engine_factory.py` | 新增引擎参数映射（从 `_launch` 抽出） | v9.8.0 | 新设置项必须在这里接线，否则「用户改了没用」 |
| `gcm/ui/download_layout.py` | 新增下载页顶部控件构建（从 `_build_download_tab` 抽出） | v9.8.0 | 控件仍挂在 MainWindow；改控件名会破坏测试/兼容引用 |
| `gcm/git/service.py` | **`_update` 337→105：拆出 `_update_bare_mirror`/`_fetch_remote`/`_merge_remote`** | v9.8.0 | 镜像回退语义逐行保留；改这块先跑 `test_g58_mirror_failover` |
| `gcm/ui/event_panel.py` | 新增事件流查看器（类型过滤/自动刷新/复制 JSON） | v9.8.0 | 默认隐藏 task_progress；读取 EventBus 快照 |
| `gcm/db/settings.py` / `ui/settings_groups.py` / `ui/settings_panel.py` / `ui/main_window.py` | **`local_api_enabled` / `local_api_port` + GUI 进程内托管本地 API** | v9.8.0 | 默认关、仅回环；改端口先起新服再停旧服；关窗/关闭开关 `wait=True` 释放端口 |
