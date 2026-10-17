# 变更索引（change-index）

> 「改这块之前先看这里」。按模块记录最近一次重要变更与连带影响。

| 模块 | 最近变更 | 版本 | 连带影响 |
|------|---------|------|---------|
| `gcm/ui/main_window.py` | 引用环修复（绑定方法替代 lambda）、输入区实时校验条、审计/清单入口、**Star 导入按钮/信号/槽、crypto_warning 启动日志** | v9.4.2–v9.4.7 | 若再加 `lambda` 捕获 self，会重新成环（有守卫）；Star 拉取必须走 worker 线程 |
| `gcm/ui/manage_model.py` | **`is_visible` / `stale_days` / 过期颜色角色 / 可见性缓存** | v9.4.7 | `data()` 会为过期行返回 Background/ForegroundRole |
| `gcm/ui/manage_panel.py` | **搜索框 + `setRowHidden` + 收藏/黑名单右键菜单** | v9.4.7 | 过滤用源行号（未接排序代理）；刷新后自动重放过滤 |
| `gcm/ui/settings_panel.py` | **安装日志高亮器 + `_apply_alert_config`** | v9.4.7 | 告警间隔/IM 保存后即时推送给扫描器 |
| `gcm/app/alerts.py` | **`AlertScanner.reconfigure()`** | v9.4.7 | 循环每轮重读 `_interval`，新间隔下一轮生效 |
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
