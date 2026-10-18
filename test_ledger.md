# test_ledger — 接缝注册表与测试状态

> 目的：回答两个问题 ——**「这条链路哪个测试证明它通」** 与 **「哪个接缝没被覆盖」**。
> 与 `.agent/memory/tested-scope.md` 分工：那边是**怎么跑**（命令/耗时/非缺陷），
> 这边是**跑了证明什么**（接缝 ↔ 用例 ↔ 最后结果）。
> 只记录本轮真实运行过的结果；引用历史结果必须标日期。

**最后更新**：2026-10-17 · 版本见 `gcm/__init__.py:__version__`（本文件不写死版本号）

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

---

## 2. Test Status（本轮真实结果）

### 2.1 门禁

| 项 | 命令 | 结果 | 时间 |
|----|------|------|------|
| ruff | `python -m ruff check gcm` | **All checks passed** | 2026-10-19 |
| mypy | `python -m mypy gcm` | **Success**（70 文件） | 2026-10-19 |
| 快集全量 | `coverage run --source=gcm scripts/ci_run_fast.py` | `Ran 1262 · failures=0 · errors=0 · skipped=3 · 原生崩溃 0 · 覆盖 83%` | 2026-10-19（v9.7.0） |
| 连跑稳定性 | 同一命令 ×3 | `1115 / 1115 / 1122`，0 崩溃 | 2026-10-17（已移除 GC 安全网） |
| CI | GitHub Actions | 7/7 job success（v9.7.0 代码 run 39165009718）；发布后 flake 修复 run 39171656327 success | 2026-10-19 |

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
