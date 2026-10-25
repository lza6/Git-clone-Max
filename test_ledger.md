# test_ledger — 接缝注册表与测试状态

> 目的：回答两个问题 ——**「这条链路哪个测试证明它通」** 与 **「哪个接缝没被覆盖」**。
> 与 `.agent/memory/tested-scope.md` 分工：那边是**怎么跑**（命令/耗时/非缺陷），
> 这边是**跑了证明什么**（接缝 ↔ 用例 ↔ 最后结果）。
> 只记录本轮真实运行过的结果；引用历史结果必须标日期。

**最后更新**：2026-10-24（v9.10.3 G80 七批收口；v9.10.2 为 G79b）· 版本见 `gcm/__init__.py:__version__`（本文件不写死版本号）

---

## 1. Seam Registry（接缝注册表）

接缝 = 两个模块/进程/格式之间的边界。**断链几乎总是发生在这里。**

| ID | 接缝 | 生产侧 | 消费侧 | 契约要点 | 守卫用例 |
|----|------|--------|--------|---------|---------|
| S01 | 原始输入 → URL 解析 | `ui/main_window` 输入区、文件导入 | `app/url_lib.parse_urls` | 跳过 `#` 注释；支持 6 平台 + SSH + 短格式；错误逐条回显 | `test_themes.TestThemeLogAndHighlight.test_maybe_validate_url` · `test_url_lib*` |
| S02 | 引擎 → git 子进程 | `app/engine` | `git/service.GitService` | `encoding="utf-8", errors="replace"`；`GIT_CONFIG_*` **追加**不覆盖；代理/令牌注入 | `test_g66_patch.TestSubprocessTextDecoding` · `TestG69_18NoGitConfigEnvPollution` |
| S03 | worker 结果 → SQLite | `app/engine._on_result`（单线程写） | `db/repo_db` | `BEGIN IMMEDIATE` + `busy_timeout=30000`；参数化查询 | `test_g67_closure` · `test_db*` |
| S04 | DB → UI 模型 | `db/repo_db` | `ui/progress_table` · `ui/manage_model` | 行数/列语义一致；越界补行；**过滤条件必须被视图消费**；过期行有颜色角色 | `test_progress_ui` · `test_g67_closure`（重投复用批次上下文）· `test_g69_wiring_gaps`（过滤/高亮/收藏黑名单）· `test_g53_radar`（雷达图+统计中心挂载） |
| S05 | 设置控件 → 落盘 | `ui/settings_panel` | `db/settings.SettingsStore` | 每个控件都有保存回调；敏感字段加密（token/host_tokens/ssh_key/**proxy**）；跨进程锁；pid/tid 唯一 tmp 名 | `test_g68_settings_ui` · `test_g67_closure.TestCrossProcessLock` · `TestG69_18WiringClosure` |
| S06 | 设置字段 → 消费方 | `db/settings.Settings` | 引擎/扫描器/调度器 | **只写不读 = 用户改了没用**；只读不写 = 用户够不到；告警间隔/IM 改完必须即时生效 | `TestG69_18WiringClosure` · `TestG69_18NoZombieSettingsFields`（AST）· `test_g69_wiring_gaps.TestAlertScannerReconfigure` |
| S07 | 后台线程 → GUI | `AlertScanner`/`ReportScheduler`/扫描线程/Star 拉取 | `ui/main_window` 槽 | 只能 `emit`，槽回主线程；对已销毁 QObject emit 必须被弱引用包装挡住 | `TestG69_18ScanWorkerStopsBeforeEmit` · `TestG69_18LogBufferTimerIsActivityArmed` · `test_g69_wiring_gaps.TestStarredImport` |
| S08 | Webhook 发送 | `app/notify.maybe_notify` | 外部 HTTP | 600s 幂等（锁内查重+占位，失败撤销）；审计只记 `scheme://host`；重试 1 次 | `test_g69_notify_hardening`（16，含 40 线程并发） |
| S09 | 进度持久化 / 续跑 | `app/engine` | `db/progress_file` + `util/filelock` | 原子写（tmp+replace）+ **跨进程**锁；`shutdown` 后不残留 in_progress | `test_g67_closure.TestShutdownClearsInProgress`（高负载偶发） |
| S10 | 数据目录解析 | `gcm/__main__.get_data_dir()` | 所有模块 | **唯一来源**；安装版必须读 `%APPDATA%`，不得自拼 `<exe>/data` | `TestG69_18DiagnosticsWiring` · `test_g67_closure.TestExtraHostsUsesRealDataDir` |
| S11 | Agent 接入面 | `gcm/cli.py` / `mcp_server.py` / `http_api.py` | 外部 agent | 退出码 0/1/2（MCP 未启用=3）；`--json` schema 稳定；MCP 默认只读；HTTP 只绑回环 | `test_g56_mcp*` · `test_g56_contract` · `test_g56_cli_json` · `test_g56_api*` |
| S12 | 打包 → 发布 | `Git-clone-Max.spec` → PyInstaller | `scripts/publish_release.py` / Release | 版本常量单源；双产物；服务端 sha256 一致；健康检查真启动 | `test_g66_patch.TestPackagingVersionSync` · `scripts/health_check.py`（人工） |
| S13 | 归档 | `git/archive.py` | 文件系统 | 失败不静默；路径不越界 | `test_g56*` / `test_archive*` |
| S14 | i18n | `gcm/i18n/zh.py` / `en.py` | `tr()` | 新增 key 必须两边都有；缺失回退中文 | `test_g67_docs_freshness` 相关用例 |
| S15 | 镜像备援 → git | `app/mirrors.py` · `app/mirror_probe.py` | `git/service.py` 的 clone/fetch | 仅 HTTPS + 无凭据（含全局 `http.*extraHeader`）；探测用 `git ls-remote`（**不能用 HTTP HEAD**）；源站永远最后；**origin 永远是源站**（镜像只记 `gcm.mirror`）；认证头 URL 作用域；更新回退先探测、`retries=0` | `test_g58_mirror_failover`（43，含真实 git E2E） |
| S16 | 主题 token → QSS/自绘控件 | `ui/theme.py` | 全部 UI | 语义色 `on_accent`/`border_strong`/`success`；文本 ≥4.5、边界 ≥3；自绘控件取 `current_palette()` | `test_g70_ui_upgrade.TestSemanticContrast/TestQssContract` · `test_g46_theme` |
| S17 | 进度数据 → delegate | `ui/progress_table.py` | `ProgressDelegate` | engine index 经列 0 `UserRole+1` 反查；250ms 合批；不逐行 `setData`；0 个子控件 | `test_g70_ui_upgrade.TestProgressDelegate` · `test_progress_ui` · `test_g69_regressions.TestProgressRowsMatchRepoWhileSorting` |
| S18 | 窗口尺寸 → 布局 | `ui/layout.py` | `MainWindow` / 8 个对话框 | 逻辑像素断点；按 `availableGeometry` 夹取；Compact 折叠；对话框 90%×85% | `test_g70_ui_upgrade.TestLayoutBreakpoints` |
| S19 | HTTP 分页 → SQLite | `http_api.py` | `db/repo_db.list_repos_page` | `limit` 1..5000、`offset≥0`；`total/truncated`；只读连接跳过建表/迁移；错误不回显路径 | `test_g70_ui_upgrade.TestRepoDbPaging/TestReadonlyDatabase/TestHttpApiPaging` · `test_g56_api*` |
| S20 | 引擎 → EventBus → SSE | `app/engine.py` | `app/event_bus.py` · `http_api._stream_events` | 发布不阻塞/不抛；有界缓冲 + 回放 + 类型过滤；出站打码；慢订阅者丢最旧并计数；SSE 心跳/断开清理 | `test_g71_event_stream.TestEventBus/TestSseEndpoint/TestEnginePublishesEvents` |
| S21 | 错误分类 → 重试/UI | `app/errors.classify_error` | `app/retry_queue.py` · `ui/failure_panel.py` · `ui/retry_panel.py` | 唯一判据：429/5xx 可重试；401/403/404/磁盘满/平台限制不可；三段式文案 | `test_g71_event_stream.TestErrorModel/TestRetryQueueVisibility` · `test_g52_failure_panel` |
| S22 | 原子写 → 所有 JSON 持久化 | `util/atomic_io.write_text_atomic` | progress/history/lists/concurrency_state | 唯一 tmp + flush/fsync + 目录 fsync + 替换退避；并发不丢更新、无 tmp 残留 | `test_g71_event_stream.TestAtomicWrites` · `test_g67_closure.TestCrossProcessLock` |
| S23 | 诊断包 | `app/diag_bundle.py` | 主窗口/诊断对话框 | 只含已打码文本；manifest 版本/时间/文件清单；zip 原子落盘 | `test_g71_event_stream.TestDiagBundle` |
| S24 | 批次状态机 | `app/batch_state.py` | `app/engine.py` | queued→running→终态；终态不可回退；幂等计数；非法转换计数可观测 | `test_g71_event_stream.TestBatchState/TestEnginePublishesEvents` |
| S25 | 设置 → GUI 内置本地 API 生命周期 | `db/settings.local_api_*` · `ui/settings_panel` | `ui/main_window._apply_local_api_settings` | 改开关/端口立即启停；只绑回环；关窗释放端口；失败只日志不拖垮 GUI | `test_g71_event_stream.TestLocalApiSettings` · `test_g68_settings_ui` |
| S26 | EventBus 快照 → 事件流 UI | `app/event_bus.snapshot` | `ui/event_panel` | 默认隐藏进度事件；类型过滤纯函数；刷新/复制 JSON；写入与消费都接线 | `test_g71_event_stream.TestEventPanel` |
| S27 | 日志出口 → app.log 文件 | `app/applog.py` | 磁盘文件 | Formatter 层统一 redact；token/URL 凭据/路径变体不落明文；普通日志不变 | `test_g72_hardening.TestLogRedaction` |
| S28 | SSE 续传/背压 | `http_api._stream_events` | `app/event_bus.since/oldest_seq` | `Last-Event-ID` 优先、精确 seq 补发、`history-truncated`；并发上限 503；30s 写超时；订阅者 dropped/qsize | `test_g72_hardening.TestSseG72/TestEventBusG72` |
| S29 | 原子替换 → Windows 瞬时占用 | `util/atomic_io.replace_with_retry` | progress/settings/history/lists/diag | mkstemp O_EXCL；winerror 5/32/33/1224 退避；非瞬时错误立即上抛并清理 tmp | `test_g72_hardening.TestAtomicHardening` |
| S30 | DLQ → 重新入队 | `app/retry_queue.requeue_dropped` | `ui/retry_panel` | dropped 记录保留 spec 字段；重新入队 attempts=0、立即到期、不丢 @tag/folder | `test_g73_dlq` |
| S32 | 设置写侧 → 字段（G76 收口） | `ui/settings_groups` + `ui/settings_actions` | `db/settings.SettingsStore` → 引擎/调度器/关窗行为 | 控件 → 保存回调 → **落盘** → 消费方四段都要通；需要执行命令的字段（克隆后钩子）必须二次确认且默认 No；不可逆操作（数据目录迁移）在任务运行中必须拒绝；设置模块行数 ≤800 | `test_g76_wiring`（23）· `test_g68_settings_ui`（含 `EXPECTED_WIDGETS`） |
| S33 | 视觉系统 token → 渲染（G75 收口） | `ui/palettes` + `ui/typography` + `ui/qss_build` + `ui/status_pill` + `progress_delegate` | QSS 字符串 / QPainter 自绘 / 托盘图标 / 日志高亮 | 颜色只有一个来源（`current_palette()` 含强调色预设）；字号阶梯严格递减；四主题对比度达标；状态形状互不相同且能真画出像素；进度刻度坐标可断言 | `test_g75_visual_system`（46）· `test_g46_theme` · `test_themes` · `test_g70_ui_upgrade` |
| S34 | **可重建产物 → git 索引**（G79 收口） | `scripts/ui_screenshot_audit.py <dir>` · `PyInstaller` · 本地运行时目录 | `.gitignore` · `git ls-files` | ①截图/构建产物一律不入库（白名单 `docs/screenshots/`）；②根目录顶层条目必须登记（新增即红）；③超 800 行模块集合必须**等于**登记集合（拦「悄悄多出第 4 个」）；④README/CLAUDE.md 声称的测试文件数必须等于实测 | `test_g79_repo_hygiene`（9 项，含白名单未空/无反查空转的变体断言） |

---

## 2. Test Status（本轮真实结果）

### 2.1 门禁

| 项 | 命令 | 结果 | 时间 |
|----|------|------|------|
| ruff | `python -m ruff check gcm` | **All checks passed** | 2026-10-19 |
| mypy | `python -m mypy gcm` | **Success**（70 文件） | 2026-10-19 |
| 快集全量 | `coverage run --source=gcm scripts/ci_run_fast.py` | `Ran 1316 · failures=0 · errors=0 · skipped=3 · 原生崩溃 0 · 覆盖 81%` | 2026-10-19（G71 后，Windows 门禁 81） |
| G71 定向 | `unittest tests.test_g71_event_stream` | `Ran 54 · OK`（事件总线/SSE/错误模型/原子写/诊断包/状态机/引擎发布） | 2026-10-19 |
| G71 引擎回归 | `unittest tests.test_engine tests.test_g43_engine` 等 | `Ran 71 · OK`（含真实 git 本地仓 E2E） | 2026-10-19 |
| G71 UI 回归 | `unittest tests.test_g70_ui_upgrade tests.test_progress_ui tests.test_onboarding tests.test_g46_tray_help tests.test_download_tools tests.test_g52_mainwindow_methods` | `Ran 85 · OK` | 2026-10-19 |
| G72 定向 | `unittest tests.test_g72_hardening` | `Ran 16 · OK`（日志打码/原子写/诊断包/jitter/SSE 续传/背压） | 2026-10-19 |
| G72 回归 | `unittest tests.test_g72_hardening tests.test_g71_event_stream tests.test_applog tests.test_redact tests.test_g52_retry_queue_extra` | `Ran 107 · OK` | 2026-10-19 |
| G72 快集 | `coverage run --source=gcm scripts/ci_run_fast.py` | `Ran 1332 · failures=0 · errors=0 · skipped=3 · 覆盖 81%` | 2026-10-19（G72 后） |
| G73 定向 | `unittest tests.test_g73_dlq` | `Ran 3 · OK`（DLQ 字段/重新入队/面板切换） | 2026-10-20 |
| G73 快集 | `coverage run --source=gcm scripts/ci_run_fast.py` | `Ran 1335 · failures=0 · errors=0 · skipped=3 · 覆盖 82%` | 2026-10-20（G73 后） |
| G74 定向 | `unittest tests.test_g74_ui_layout -v` | `Ran 14 · OK`（布局/主题/文案 14 项） | 2026-10-20（G74） |
| G74 截图审计 | `scripts/ui_screenshot_audit.py reports/ui-audit-final` | 38 张 PNG：4 主题 × 5 分辨率 + 页签 + 8 个对话框 | 2026-10-20（G74） |
| G74 快集 | `coverage run --source=gcm scripts/ci_run_fast.py` | `Ran 1349 · failures=0 · errors=0 · skipped=3 · 覆盖 82%` | 2026-10-20（G74） |
| 连跑稳定性 | 同一命令 ×3 | `1115 / 1115 / 1122`，0 崩溃 | 2026-10-17（已移除 GC 安全网） |
| CI | GitHub Actions | 7/7 job success（run 39165009718）；发布后 flake 修复 run 39171656327 success | 2026-10-19 |
| 9.9.3 那轮快集 | `coverage run --source=gcm scripts/ci_run_fast.py` | `Ran 1421 · 0 fail · 4 skip · 覆盖 82%` | 2026-10-23（审查整改轮） |
| 9.9.3 那轮定向 | 7 个相关模块 | `Ran 196 · OK`（2 skipped） | 2026-10-23（审查整改轮） |
| G75 收口快集 | `coverage run --source=gcm scripts/ci_run_fast.py` | `Ran 1421 · failures=0 · errors=0 · skipped=4 · 覆盖 82%` | 2026-10-23（9.9.2 那轮） |
| CI（9.9.2 那轮） | GitHub Actions run `39905267408` | **7/7 job success**（3 OS + e2e-smoke + fast-smoke + slow + perf） | 2026-10-23 |
| G75 定向 | `unittest tests.test_g75_visual_system` | `Ran 46 · OK`（1 skipped：字体存在性按平台条件化） | 2026-10-23（G75 收口） |
| G75 慢集（Windows） | 10 个慢模块（真实 git E2E） | `Ran 129 · OK`（65.9s） | 2026-10-23（G75 收口） |
| G75 基准 | `scripts/bench_g70_ui.py` | 500 行 add 27.85ms/update 2.87ms/0 子控件；QSS 1.989→0.016ms；启动 101.93ms | 2026-10-23 |
| 契约实测 | CLI 4 种输入 + `--help` + MCP 未启用 | 退出码 0/1/2/2 · 0 · 3（与 `docs/agent-contract.md` 一致） | 2026-10-23 |
| flake 复核 | `test_g67_closure.TestShutdownClearsInProgress` ×10 | 10/10 OK | 2026-10-23 |
| 9.9.4 那轮快集 | `coverage run --source=gcm scripts/ci_run_fast.py` | `Ran 1429 · failures=0 · errors=0 · skipped=4 · 覆盖 82%` | 2026-10-23 |
| **CI（已恢复）** | GitHub Actions run `39922315053` on `7097209` | **7/7 job success**（slow/e2e-smoke/perf-baseline/test×3 OS/fast-smoke） | 2026-10-23T11:35:03Z |
| **G79 卫生守卫（红→绿）** | `unittest tests.test_g79_repo_hygiene` | 首跑 **FAILED (failures=4)**（38 PNG 入库 / cli.py+z.png / README 135≠137 / CLAUDE 113≠137）→ 修复后 **Ran 9 · OK** | 2026-10-24 |
| **G79 僵尸字段（红→绿）** | `unittest tests.test_g69_regressions.TestG69_18NoZombieSettingsFields` | 收紧口径后 **FAILED** 并精确点名 `['depth']` → 删除字段后 **Ran 2 · OK** | 2026-10-24 |
| G79 版本单源 + 打包同步 | `unittest tests.test_g66_patch.TestContractVersionSingleSource TestPackagingVersionSync` | `Ran 11 · OK`（9.10.0 已同步 spec filevers/prodvers/installer.iss/pyproject/agent-contract/README） | 2026-10-24 |

### 2.2 按接缝的覆盖状态

| 接缝 | 状态 | 说明 |
|------|------|------|
| S01 | ✅ 覆盖 | 7 种 URL 形态逐一断言 |
| S02 | ✅ 覆盖 | 含「父进程已带 `GIT_CONFIG_*`」污染场景 |
| S03 | ✅ 覆盖 | 并发写 + 跨进程锁对照组 |
| S04 | ✅ 覆盖 | 重投上下文复用有专项守卫 |
| S05 | ✅ 覆盖 | 控件的写侧与消费侧都断言 |
| S06 | ✅ 覆盖 | AST 扫描，注释/changelog 不算引用 |
| S07 | ✅ 覆盖 | 含「停止后零 emit」「销毁后 emit 不崩」 |
| S08 | ✅ 覆盖 | 40 并发只发 1 次；审计并发 30/30 稳定 |
| S09 | ⚠️ 部分 | 功能正确；高负载下 `TestShutdownClearsInProgress` 偶发（约 1/3 全量），单独跑稳定 |
| S10 | ✅ 覆盖 | 安装版路径由守卫断言 |
| S11 | ✅ 覆盖 | 退出码 / schema / 回环绑定 / 只读默认 |
| S12 | ⚠️ 部分 | 版本常量有静态守卫；**exe 构建与健康检查是人工步骤，不在 CI** |
| S13 | ✅ 覆盖 | — |
| S14 | ✅ 覆盖 | — |
| S31 | ✅ 覆盖 | **G74 UI 布局契约**：主操作区不在顶部滚动区内且 1024 可见；compact 隐藏快捷填充；管理页日期列定宽；设置导航短标题/无横向滚动；对话框继承主窗 QSS；统计内容可滚动；空校验标签不占位；数字框隐藏原生箭头；标签/复选框透明；仓库详情长路径省略。守卫 `tests/test_g74_ui_layout.py`（14）+ 截图审计脚本。 |
| S32 | ✅ 覆盖 | **G76 接线收口**：6 个只读不写的设置字段 + 扫描进度 + sha256 提示 + 数据目录入口 + 死信号全部补上写入侧；每条都断言「值变化 + 重读 settings.json + 消费方存在」。 |

### 2.3 未覆盖 / 无法自动化

| 项 | 原因 | 补偿 |
|----|------|------|
| 真实 GUI 交互（点击/拖拽/托盘） | offscreen 无法模拟真实输入 | 用 `isVisible()/isEnabled()` 断言 + 真机冒烟（`docs/RELEASE_CHECKLIST.md` §2） |
| 真实网络克隆全平台 | 外部依赖不稳定 | 本地裸仓 E2E + 少量真实远端（慢集） |
| 打包产物启动 | 需 PyInstaller（约 10min） | `scripts/health_check.py` 人工跑，结果写 `evidence/` |
| 付费外部 API | `paid_api_real_call = false` | 全部 mock（success/timeout/rate_limit/malformed/retry_exhausted） |

---

## 3. 维护规则

1. **新接缝必须登记**：两个模块第一次通过新契约通信时，加一行 + 至少一个守卫用例。
2. **改契约必须回填**：本表与 `docs/agent-contract.md` 同一次提交更新。
3. **结果只写真实运行值**：没跑就写「未运行」，不搬运历史数字。
4. **偶发失败不隐藏**：标 ⚠️ + 频率 + 是否单独稳定；不得用重试或 skip 掩盖。
