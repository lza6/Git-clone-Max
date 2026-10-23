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
| `gcm/ui/typography.py` | **G75 新增：三字族 + 七级字号阶梯 + `type_font`/`apply_type`** | v9.9.0 | QSS 无 `letter-spacing`，字距只能经 QFont；`type_font` 被 `progress_delegate`/`status_pill` 直接用于 QPainter |
| `gcm/ui/palettes.py` | **G75 新增：四套色板 + cta/on_cta/glow token + 强调色深/浅成对** | v9.9.0 | 四主题键集合必须一致；改色先跑 `test_g75_visual_system.TestPaletteContrast`（正文≥4.5、hc≥7、语义色≥4.5） |
| `gcm/ui/qss_build.py` | **G75 新增：QSS 拼装（含 reduced-motion 分支与渐变底）** | v9.9.0 | `motion=False` 必须整块去掉 hover/pressed（不是改同色）；`test_g46_theme.test_motion_off_differs` |
| `gcm/ui/theme.py` | **G75：598→314 行薄壳 + 主题/强调色状态（`apply_accent_preset`）** | v9.9.0 | `current_palette()` 必须反映强调色预设，否则自绘与 QSS 分叉；22 个再导出符号一个都不能少 |
| `gcm/ui/status_strip.py` | **G75 新增：贴顶仪表齿条（并发/活跃/成功/失败/用时）** | v9.9.0 | 平台无关纯逻辑 `format_elapsed/format_count/format_metrics` 有单测；空闲不得启动 QTimer |
| `gcm/ui/status_pill.py` | **G75 新增：状态形状编码 + pill delegate** | v9.9.0 | 只绘制、不改 item 文本/UserRole（`test_g74_ui_layout` 断言状态文本保持纯文本） |
| `gcm/ui/batch_overview.py` | **G75 新增：批次完成度条（从 progress_table 抽出）** | v9.9.0 | 与齿条职责分离：本批次完成度 vs 进程运行态；`batch_total_lbl` 文本被测试与托盘复用 |
| `gcm/ui/result_labels.py` | **G75 新增：状态/动作文案唯一来源（走 tr）** | v9.9.0 | 新增状态/动作先改这里，再补 zh/en 键 |
| `gcm/ui/download_layout.py` | 新增下载页顶部控件构建（从 `_build_download_tab` 抽出） | v9.8.0 | 控件仍挂在 MainWindow；改控件名会破坏测试/兼容引用 |
| `gcm/git/service.py` | **`_update` 337→105：拆出 `_update_bare_mirror`/`_fetch_remote`/`_merge_remote`** | v9.8.0 | 镜像回退语义逐行保留；改这块先跑 `test_g58_mirror_failover` |
| `gcm/ui/event_panel.py` | 新增事件流查看器（类型过滤/自动刷新/复制 JSON） | v9.8.0 | 默认隐藏 task_progress；读取 EventBus 快照 |
| `gcm/db/settings.py` / `ui/settings_groups.py` / `ui/settings_panel.py` / `ui/main_window.py` | **`local_api_enabled` / `local_api_port` + GUI 进程内托管本地 API** | v9.8.0 | 默认关、仅回环；改端口先起新服再停旧服；关窗/关闭开关 `wait=True` 释放端口 |
| `gcm/ui/download_layout.py` | **G74：主操作区移出顶部滚动区；快捷填充横向滚动；占位 3 行；空校验标签隐藏** | v9.8.3 | `btn_start/btn_cancel/btn_diag_bundle` 不能放回 `download_top_scroll`；守卫 `test_g74_ui_layout` |
| `gcm/ui/theme.py` | **G74：QLabel/QCheckBox/QRadioButton 透明；QSplitter handle 细分隔线；SpinBox 箭头零宽兜底** | v9.8.3 | 全局透明规则；需要标签底色时必须显式覆盖；QSS 校验白名单已加 QRadioButton |
| `gcm/ui/manage_panel.py` | **G74：工具栏三行；时间/HEAD/历史/健康/大小列 Fixed（时间 150px）** | v9.8.3 | 时间列 <150 会把日期中间省略成乱码；`test_g74_ui_layout` 有断言 |
| `gcm/ui/retry_panel.py` | **G74：原因列只放 title+action，三段进 tooltip；DLQ 记录补分类** | v9.8.3 | 改单元格文案先跑 `test_g74_ui_layout.TestG74PolishRegressions` |
| `gcm/ui/statistics_dialog.py` / `ui/radar_chart.py` | **G74：雷达/评分卡上移到概览后；内容 QScrollArea；雷达 min 320×280** | v9.8.3 | 首屏必须完整显示雷达；不要移回趋势图之后 |
| `gcm/ui/settings_groups.py` | **G74：启动组 2 列网格；设置页 QSpinBox 统一 NoButtons** | v9.8.3 | 新设置分组若用 QSpinBox，`build_settings_ui` 末尾统一处理 |
| `gcm/ui/onboarding.py` / `ui/checklist.py` / `ui/repo_detail_dialog.py` | **G74：向导提示/去 emoji；清单关闭中文化；详情长路径省略+tooltip** | v9.8.3 | 新文案必须同时进 zh/en |
| `scripts/ui_screenshot_audit.py` | **G74：离屏截图审计（38 张：4 主题×5 分辨率+页签+8 对话框）** | v9.8.3 | 先建 QApplication 再注册字体；用 `QTest.qWait` 真实等待；诊断内容用 `__version__` |

## G75/G76（2026-10-23）改这块前先看

| 你改的是 | 连带影响 |
|---|---|
| 任何颜色字面量 | 只允许出现在 `gcm/ui/palettes.py`；自绘控件/日志高亮/托盘图标都读 `theme.current_palette()`（含强调色预设），否则又会出现「QSS 一套、自绘另一套」 |
| 字号 / 字族 | 只允许 `gcm/ui/typography.py`（`TYPE_SCALE`）；QSS 走 `qss_build`，QFont 路径走 `type_font`/`apply_type`（QSS 无 `letter-spacing`） |
| QSS 字符串 | 只在 `gcm/ui/qss_build.py`；新增选择器要过 `qss_validate` 白名单；`motion=False` 必须无 hover/pressed 块 |
| 状态文案 / 颜色 | 文案→`result_labels`（走 `tr`）；颜色/形状→`status_pill.color_for/shape_for`；**不要**再用 ✓✕⚠ 字符（缺字形=tofu） |
| 顶栏读数 | `status_strip`（进程运行态）与 `batch_overview`（本批次完成度）职责分离（D23），合并前先读决策 |
| 设置页字段 | 写入侧集中在 `settings_actions.SettingsWiring`（面板/分组模块都逼近 800 行）；新增字段必须同时有 `EXPECTED_WIDGETS` 条目与 `test_g76_wiring` 式「改值→重读 JSON」断言 |
| 克隆后钩子 / 数据目录迁移 | 属「执行任意命令 / 不可逆」面：必须二次确认、默认 No、无可见窗口不弹模态、拒绝在跑任务 |
| 更新检查 | 信号 `update_checked` 保持 4 参数（既有守卫按 4 参断言）；SHA256 走实例属性回传 |
