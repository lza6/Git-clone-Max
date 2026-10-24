# 已测试范围（tested-scope）

> **下次改代码前先读这里**，避免重复跑同一批验证。
> 时间戳 = 最后一次真实运行；命令可直接复制。

---

---

## 0. 最新一次真实运行（2026-10-23 · G75+G76 收口，v9.9.0）

> 下次改这几块前先看这一节，别重复跑。

| 项 | 命令 | 结果（实测） |
|---|---|---|
| 质量闸 | `ruff check gcm` / `mypy gcm` | All checks passed / Success（**86 文件**） |
| 快集全量 | `coverage run --source=gcm scripts/ci_run_fast.py` | **Ran 1415 · failures=0 · errors=0 · skipped=4** |
| 覆盖率 | `coverage report --include="gcm/*"` | **TOTAL 82%**（门禁 Windows 81 / POSIX 80） |
| G76 新增守卫 | `unittest tests.test_g76_wiring` | Ran 23 · OK |
| 设置/进度/管理/更新定向 | 11 个模块合集 | Ran 234 · OK |
| 视觉系统 | `unittest tests.test_g75_visual_system` | Ran 43 · OK（skipped=1：字族存在性按平台条件化） |
| 源码级 git E2E | `scripts/e2e_smoke.py` | `E2E_SMOKE_RESULT: PASS`（clone/ff 拒绝/冲突/abort/HEAD） |
| 打包 E2E | `dist/Git-clone-Max-portable/Git-clone-Max.exe --cli --json urls.txt` | `action=cloned` → 二次 `action=fetched / 已是最新`，exit 0 |
| 性能基准 | `scripts/bench_g70_ui.py` | 500 行插入 26.82ms · delegate 0 子控件 · 启动 97.84ms · QSS 缓存 2.39→0.02ms |
| 截图审计 | `scripts/ui_screenshot_audit.py reports/ui-g75` | 38 PNG（基线 `reports/ui-baseline-983` 同为 38） |
| 构建 + 健康 | `PyInstaller` + `build_portable_zip.py` + `health_check.py` | exe 41,340,204 B · zip 79,756,248 B · version_match 9.9.0 · started_ok=true |
| 文档守卫 | `unittest tests.test_g67_docs_freshness` | Ran 11 · OK |

**本轮没跑（别当成已覆盖）**：本机 Windows 慢集、CI 三平台、真机 125% DPI 目检、
数据目录真实搬移（不可逆，自动化里只断言「拒绝在跑任务」与「调用既有实现」）。

## 1. 质量闸（每次改 `gcm/` 必跑，~30s）

```bash
./.venv/Scripts/python.exe -m ruff check gcm     # 必须 All checks passed
./.venv/Scripts/python.exe -m mypy gcm           # 必须 Success
```

| 时间 | ruff | mypy |
|------|------|------|
| 2026-10-17 | All checks passed | Success（66 文件） |

---

## 2. 快集全量（改 `gcm/` 或 `tests/` 必跑，~13min）

```bash
rm -f .coverage
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m coverage run --source=gcm scripts/ci_run_fast.py
./.venv/Scripts/python.exe -m coverage report --include="gcm/*"
```

| 时间 | 结果 |
|------|------|
| 2026-10-17 | `Ran 1122 · failures=0 errors=0 skipped=3 · 原生崩溃 0 · 覆盖 81%` |
| 2026-10-18 v9.5.0 | `Ran 1177 · failures=0 errors=0 skipped=3 · 原生崩溃 0 · 覆盖 82%`（镜像多备援后） |
| 2026-10-18 v9.4.7 | `Ran 1143 · failures=0 errors=0 skipped=3 · 原生崩溃 0 · 覆盖 82%` |
| 2026-10-17 v9.4.5 | 连跑 3 次：1115 / 1115 / 1122，0 崩溃（**已移除 GC 安全网**） |

---

## 3. 定向回归（改对应区域时跑，秒级~分钟级）

| 区域 | 命令 |
|------|------|
| 接线/生命周期/环境守卫 | `python -m unittest tests.test_g69_regressions` |
| Webhook 幂等/审计/重试 | `python -m unittest tests.test_g69_notify_hardening` |
| 引导清单 | `python -m unittest tests.test_g69_checklist` |
| **接线缺口（搜索/高亮/收藏/Star/告警配置）** | `python -m unittest tests.test_g69_wiring_gaps` |
| **镜像多备援/安全** | `python -m unittest tests.test_g58_mirror_failover`（43，含真实 git E2E） |
| **评分卡雷达图** | `python -m unittest tests.test_g53_radar`（9） |
| 版本单源 + 打包同步 + 文档新鲜度 | `python -m unittest tests.test_g66_patch.TestContractVersionSingleSource tests.test_g66_patch.TestPackagingVersionSync tests.test_g67_docs_freshness` |
| MCP / HTTP / CLI 契约 | `python -m unittest tests.test_g56_mcp tests.test_g56_mcp_extra tests.test_g56_contract tests.test_g56_cli_json tests.test_g56_api_extra tests.test_g56_api_write` |
| 凭据/环境隔离 | `python -m unittest tests.test_token tests.test_rate_limit tests.test_security_hardening` |
| 设置页控件契约 | `python -m unittest tests.test_g68_settings_ui` |

---

## 4. 干净检出对照（改数据目录 / 测试环境逻辑后必做）

本机有 `data/` 目录，干净检出没有 —— 这个差异会改变若干测试路径。

```bash
rm -rf /d/tmp/gcm_clean && mkdir -p /d/tmp/gcm_clean
git archive HEAD | tar -x -C /d/tmp/gcm_clean
cd /d/tmp/gcm_clean
QT_QPA_PLATFORM=offscreen "<项目>/.venv/Scripts/python.exe" -m coverage run --source=gcm scripts/ci_run_fast.py
```

---

## 5. 污染的 GIT_CONFIG_* 环境（改 `service._env` 后必跑）

```bash
QT_QPA_PLATFORM=offscreen \
GIT_CONFIG_COUNT=2 GIT_CONFIG_KEY_0=credential.interactive GIT_CONFIG_VALUE_0=false \
GIT_CONFIG_KEY_1=credential.guiPrompt GIT_CONFIG_VALUE_1=false \
  ./.venv/Scripts/python.exe -m unittest tests.test_rate_limit tests.test_security_hardening tests.test_token
```

---

## 6. 真机受限控制台编码（改 MCP/CLI 输出后必跑）

```bash
printf '{"jsonrpc":"2.0","id":1,"method":"initialize"}\n' | \
  GCM_MCP_ENABLE=1 PYTHONIOENCODING=cp1252 ./.venv/Scripts/python.exe -m gcm.mcp_server
# 期望：完整 JSON 响应、rc=0、无 UnicodeEncodeError
```

---

## 7. 压测（改幂等/并发/锁后必跑）

`tests/test_g69_notify_hardening.py::TestConcurrentIdempotency`
40 线程 + Barrier，同 payload 只允许发 1 次。**连续跑 30 次必须全绿**（曾因审计无写锁在 25 次里挂 3 次）。

---

## 8. 打包健康检查（发版前必跑，~1min）

```bash
./.venv/Scripts/python.exe -m PyInstaller --noconfirm Git-clone-Max.spec   # ~10min
./.venv/Scripts/python.exe scripts/build_portable_zip.py
./.venv/Scripts/python.exe scripts/health_check.py --json dist/health_check.json
```
期望：`version` 与 `__version__` 一致、`started_ok: true`、`artifacts_exist: true`。

---

## 9. 已确认**非缺陷**（别重复排查）

| 项 | 结论 | 证据 |
|----|------|------|
| `addCleanup(shutdown)` 后 `addCleanup(server_close)` | 只产生 `WinError 10038` 噪声，**不导致测试失败** | 单测复现 errors=0 |
| `test_g38_network` / `test_g381_host_tokens` 遍历 `GIT_CONFIG_*` | 按**键名**查找而非断言 index 0 → 环境无关 | grep 确认 |
| SQL 注入 | 除 PRAGMA 常量外全参数化，**无注入面** | 复查 |
| `Path("notify.log").with_suffix(".log.1")` | 3.10/3.14 均得到 `notify.log.1` | 实测 |
| `ClipboardWatcher` 传普通函数 | 走强引用分支并正常回调 | 实测 |
| 旧 settings.json 中已删字段 | 被 `_DEFAULTS` 正确过滤 | 实测 |
| 本机快集全绿（带 `data/`） | 不是「CI 说谎」，是环境差异 | 干净检出对照 |

---

## 10. G70 UI/UX 升级（2026-10-19 · v9.7.0）

```bash
# 质量闸
./.venv/Scripts/python.exe -m ruff check gcm       # All checks passed
./.venv/Scripts/python.exe -m mypy gcm             # Success（71 文件）

# 快集全量（最终）
rm -f .coverage
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m coverage run --source=gcm scripts/ci_run_fast.py
# Ran 1262 · failures=0 · errors=0 · skipped=3 · 覆盖 83%

# 本轮新增/重点
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest tests.test_g70_ui_upgrade   # 34
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest tests.test_g70_coverage     # 17
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest tests.test_progress_ui tests.test_progress_search tests.test_g52_progress_table_extra
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest tests.test_g69_regressions
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest tests.test_g56_api_extra tests.test_g56_api_write tests.test_g56_contract

# 性能基准
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe scripts/bench_g70_ui.py
# 500 行：add 299ms · update 5.3ms · 子控件 0 · QSS 缓存 0.19ms

# 真实 git E2E
./.venv/Scripts/python.exe scripts/e2e_smoke.py    # E2E_SMOKE_RESULT: PASS
```

不再需要重复跑：以上覆盖 UI 主题/断点/进度 delegate/只读 DB/API 分页。

## 11. G71 队列可观测性 / 事件流 / 诊断包（2026-10-19 · v9.8.0）

命令与结果（详见 `docs/验证记录-2026-10-19-G71事件流.md`）：

- `python -m ruff check gcm` → All checks passed（78 文件）
- `python -m mypy gcm` → Success（78 文件）
- `unittest tests.test_g71_event_stream` → **37 OK**
- 重试/失败面板/首启/HTTP/跨进程锁/进度 → **82 OK**（113s）
- 新增测试 + 引擎/E2E/帮助/文档守卫 → **71 OK**（295s）
- G70 UI/进度/首启/托盘/下载工具/主窗方法 → **85 OK**（36s）
- 快集全量 `coverage run --source=gcm scripts/ci_run_fast.py` → **Ran 1316 · failures=0 · errors=0 · skipped=3 · 覆盖 81%**（Windows 门禁 81）

未跑：快集全量、打包 exe、真实浏览器 EventSource、真机按钮点击。
原因与补偿见验证记录 §4。改以下模块时**至少**跑对应定向：

| 改动 | 必跑 |
|------|------|
| `app/event_bus.py` / `http_api.py` / `app/engine.py` 事件 | `tests.test_g71_event_stream` |
| `app/errors.py` / `app/retry_queue.py` / `ui/failure_panel.py` | `tests.test_g71_event_stream` + `tests.test_g52_failure_panel` + `tests.test_g52_retry_queue_extra` |
| `util/atomic_io.py` / `db/progress_file.py` | `tests.test_g71_event_stream.TestAtomicWrites` + `tests.test_g67_closure` |
| `git/service.py::_update` 拆分块 | `tests.test_g58_mirror_failover` + `tests.test_engine` |
| `ui/download_layout.py` / `ui/engine_factory.py` | `tests.test_g70_ui_upgrade` + `tests.test_download_tools` + `tests.test_g52_mainwindow_methods` |
| i18n 键 | `tests.test_g67_docs_freshness`（zh/en 同步） |

- G72 快集全量 → **Ran 1332 · failures=0 · errors=0 · skipped=3 · 覆盖 81%**（2026-10-19）

- G73 快集全量 → **Ran 1335 · failures=0 · errors=0 · skipped=3 · 覆盖 82%**（2026-10-20）

## G74 UI（2026-10-20 · v9.8.3）
- `tests/test_g74_ui_layout.py`（14）：主操作常驻/compact 快捷填充/管理日期列/设置导航/SpinBox 无箭头/
  标签透明/进度详情列/诊断 QSS/统计滚动/空校验隐藏/DLQ 建议/清单关闭/详情长路径。
- `scripts/ui_screenshot_audit.py`：38 张截图（`reports/ui-audit-final/`），改布局后**人工看关键图**。
- 本区域改动必跑：`test_g74_ui_layout` + `test_g70_ui_upgrade` + `test_g69_regressions`
  + `test_g36_ui` + `test_g49_i18n`。

## G75 UI 视觉系统（2026-10-23 · v9.9.0）
- 已跑：`ruff check gcm`(0) · `mypy gcm`(Success/85) · `tests.test_g75_visual_system`(43 OK)
  · UI/主题/i18n 17 模块(172 OK) · `scripts/ci_run_fast.py`(1392 tests / 0 fail / 4 skip / 82%)
  · `scripts/ui_screenshot_audit.py`（baseline 38 + 改后 38）· PyInstaller + portable zip + `health_check.py`
  · 打包 exe 真实 E2E（clone→fetched，HEAD 7fd1a60）。
- **不必重复跑**（本轮已证）：上述全部；改 UI/主题/字号/色板时只需跑
  `test_g75_visual_system + test_g74_ui_layout + test_g46_theme + test_themes + test_g70_ui_upgrade`（约 5s）。
- 仍需真机/CI：真机 DPI 视觉点检、CI 全矩阵、慢集、perf-baseline（本轮未触发）。

## G79b 终局审计（2026-10-24 · v9.10.2）

- 已跑（**含此前从未真跑过的项**）：`ruff`(0) · `mypy`(Success/87) ·
  `scripts/ci_run_fast.py`（**Ran 1439 · 0 fail · 0 error · skip 4 · 覆盖 82%**）·
  **慢集 `Ran 129 in 63.586s OK`** · `scripts/e2e_smoke.py`(PASS) ·
  `scripts/health_check.py`(version_match + started_ok 60s + repos.db) ·
  **打包 exe 真实 E2E**（cloned→fetched→无效输入 exit 2）·
  **本地 API/SSE 长连接**（`retry:` 帧 + 事件送达 + token/URL 凭据打码 + 快照 + 非回环拒绝 + 端口释放）·
  `scripts/bench_g70_ui.py`（500 行 add **15.56ms** · delegate **0** 子控件 · QSS 1.767→0.019ms · 启动 81.63ms）·
  `ui_screenshot_audit.py` **38/38 @100% 与 38/38 @125%** + 人工目检（1440×900 / 1024×768）·
  守卫族 `Ran 37 · OK`（含两次变异实验先红后绿）。
- **不必重复跑**：以上全部 —— 除非再改 `gcm/ui/main_window.py::_apply_layout_mode`、
  `gcm/ui/download_layout.py`、`tests/test_extra.py::test_check_latest_no_exe_asset`。
- 改这三处的**最小回归**（秒级）：
  `tests.test_g78_quick_scroll`（紧凑布局不变量）· `tests.test_extra.TestUpdaterExtra`（版本语义）。
- **新纪律**：改动 `_apply_layout_mode` 的隐藏清单后，**必须重渲染 1024×768 截图**目检 ——
  紧凑模式的问题在标准尺寸上完全看不见（本轮 F1 就是这么漏掉的）。

## G79 工程治理与配置真实性（2026-10-24 · v9.10.0）

- 已跑：`ruff check gcm`(0) · `mypy gcm`(Success/87) · `scripts/ci_run_fast.py`
  （**Ran 1438 · failures=0 · errors=0 · skipped=4 · 覆盖 82%**）
  · `tests.test_g79_repo_hygiene`（9 OK；首跑 4 fail）· `TestG69_18NoZombieSettingsFields`（2 OK；收紧后先红点名 depth）
  · 版本单源 11 OK · 旧 settings.json 兼容实测。
- **不必重复跑**：以上。改 `gcm/db/settings.py` 的字段集合时，只跑
  `tests.test_g69_regressions.TestG69_18NoZombieSettingsFields` + `tests.test_g79_repo_hygiene` + `tests.test_extra`（秒级）。
- **新纪律（写进守则）**：**新增/删除 Settings 字段后必须跑僵尸字段守卫**。它现在只认
  「从 `settings`/`s`/`owner.settings` 取的属性」与 `getattr`/`setattr`/`_save_field` 的字符串键 —— 
  同名局部变量不再能让字段蒙混过关。
- **发版即回归**：把版本写死进断言的地方会在下一个 minor 版炸一次（本轮 `test_g44_url_updater` 实证）。
  写「更新版本」类断言请用 `_newer_tag()` 这类动态推导。


## G80-B1（2026-10-24 00:12，批次 B1）— 已跑的验证，勿重复跑

| 命令 | 结果 |
|---|---|
| `QT_QPA_PLATFORM=offscreen python -m unittest tests.test_g80_contract -v` | 12 OK（改代码前 failures=5/errors=4） |
| `python -m unittest tests.test_g49_cli tests.test_g56_mcp tests.test_g56_mcp_extra tests.test_g44_secure tests.test_g44_secure_mainside tests.test_g71_event_stream` | 91 tests OK（9.7s） |
| `python -m ruff check gcm` / `python -m mypy gcm` | All checks passed / Success 87 files |
| MCP stdio 真机（空数据目录） | 4 个 tools/call 正常，且**未创建 repos.db** |
| 本地 HTTP API 出站打码（真起 `--serve`） | 3 个只读端点 0 明文凭据 |
| CLI 全局标志（3 组 + 对照） | `--cli FILE` / `--cli --portable FILE` / `--portable --cli FILE` / `--cli --minimized -` 全部「未提供仓库地址」exit 2 |

**改这块必跑**：`tests.test_g80_contract`、`tests.test_g49_cli`、`tests.test_g56_mcp*`、`tests.test_g44_secure*`、`tests.test_g71_event_stream`。


## 2026-10-25 · G80-B2/B3 全量快集（**已跑过，勿重复**）

| 命令 | 结果 |
|---|---|
| `coverage run --source=gcm scripts/ci_run_fast.py` | **Ran 1461 · failures=0 · errors=0 · skipped=4** |
| `coverage report --include="gcm/*"` | **TOTAL 82%**（门禁 Windows 81 / POSIX 80 → 达标） |
| `python scripts/health_check.py` | 7 项全 `[OK]`，elapsed 1661ms（exe/zip sha256、版本一致、离屏存活、建库） |
| `unittest tests.test_g80_hygiene` / `test_g80_contract` | 10 / 12 例 OK |

**注意**：快集第一轮曾红（`test_repo_detail.test_open_dir_posix_opener_via_mock`），
根因是「打开目录」实现收敛后补丁点失效（不是行为回归），已在 `741706f` 修掉；
若你看到旧日志里的 failures=1，那是修复前那一轮。


## 2026-10-25 · B8 之后全量快集（**已跑过，勿重复**）

| 命令 | 结果 |
|---|---|
| `coverage run --source=gcm scripts/ci_run_fast.py` | **Ran 1480 · failures=0 · errors=0 · skipped=4** |
| `coverage report --include="gcm/*"` | **TOTAL 82%** |

过程（两轮红→第三轮绿，均为机械副作用，非行为回归）：
1. 第一轮：`test_repo_detail`（open_dir 补丁点随实现收敛而失效）；
2. 第二轮：`test_g79_repo_hygiene`（`repo_db.py` 799→818 超 800 线 + 测试文件数 139→140 未同步）；
3. 第三轮绿。教训：**不要在快集跑的过程中改代码**（第二轮里 `test_g66_patch` 的 error 就是这样来的）。


## 2026-10-25 · G80 第四/五轮全量快集（**已跑过，勿重复**）

| 轮次 | 触发 | 结果 |
|---|---|---|
| 第 4 轮（B4-1/B4-3 后） | 检出校验 + HTTP 硬化 | **Ran 1495 · failures=0 · errors=0 · skipped=4** · 82% |
| 第 5 轮（B4-2 后） | 安装包校验 | **Ran 1507 · failures=0 · errors=0 · skipped=4** · 82% |

累计教训不变：改代码别和跑测并行；守卫红先查是不是自己这轮的机械副作用。
