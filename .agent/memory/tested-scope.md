---
topic: tested-scope
updated: 2026-11-03
owner: Verifier
---
# 已测试范围（tested-scope）
## 0. 最新一次真实运行（2026-11-06 · Rev14 收尾轮）

| 项 | 命令 | 结果（实测） |
|---|---|---|
| 质量闸 | `ruff check gcm` / `mypy gcm` | All checks passed / Success（**140** 源文件） |
| 文档漂移 | `scripts/doc_drift_check.py` | `DRIFT 0` |
| i18n 棘轮 | `scripts/i18n_coverage.py` | `I18N OK`（未走 `tr()` 的中文字面量 1517 → **1418**） |
| 发布台账 | `scripts/changelog.py check` | 86 条历史 / 0 条待发布碎片（9.18.8 已聚合） |
| 全快集 | `scripts/ci_run_fast.py` | 2126 例 · failures=3（3 红=版本/changelog 台账）→ 聚合后 `test_g66_patch`+`test_g81_changelog` **75 例 OK** |
| 小屏几何 | 自建探针（9 尺寸 × 3 字号） | 27 组合 + 900×480 **零裁切**（含「更多 ▸」） |
| 真实 GUI E2E | 离屏真窗口点「开始」（真网络） | 第1轮 `成功/新建克隆/克隆完成/2.1s`；第2轮 `已跳过/—/已完成，跳过` + 状态栏「跳过 1」 |
| DPI 矩阵 | `scripts/check_dpi_matrix.py` | 1.25/1.5/1.75/2.0 四档 `exit 0` · **0 裁切**（`evidence/rev14-dpi.json`） |
| 像素基线 | `pixel_diff.compare_dirs` | 基线 vs 同代码第二份 → **回归 0 / 缺失 0 / 新增 0** |
| 首帧位移 | `scripts/bench_resize.py` | `first_frame_moves=**31**`（目标 0，**未达成**：来源 show 后 150ms 的布局去抖重算） |

> **别再重复跑**：上面这批就是 Rev14 收尾轮的完整口径。改 `gcm/ui/` 布局或折叠阈值时
> 只需重跑 `test_ui_compact_visibility` + `test_rev13_adapt` + `test_g70_ui_upgrade`。


## 0. 最新一次真实运行（2026-11-03 · v9.17.0 G83 批）

| 项 | 命令 | 结果（实测） |
|---|---|---|
| 质量闸 | `ruff check gcm` / `mypy gcm` | All checks passed / Success（**119** 源文件） |
| 新守卫三件套 | `vulture gcm --min-confidence 90` / `zero_assert_scan.py tests` / `memory_check.py` | **0 发现 / 0 零断言 / 0 问题**（首轮分别抓出 1 死参数、8 零断言测试、7 缺 frontmatter——全部已修） |
| 自适应间隔 | `tests.test_g83_adaptive_interval` + `test_g83_adaptive_wiring` | 13 用例全绿（纯函数 7 + 主窗接线 6） |
| 定向回归 | g79/g53/g75/g69/c1/c6/doc_drift/g374/g37/g81 | 全绿（修复了先前已存在的根目录白名单红灯） |
| 版本契约 | `test_g66_patch.TestPackagingVersionSync` + `TestContractVersionSingleSource` + `test_g81_changelog` | 20 用例 OK（9.17.0 六载体同步） |
| 变异自检 | 零断言守卫抓注入用例 / memory_check 抓坏 change-index 路径 | 两次 **MUTATION_OK** |
| 文档漂移 | `doc_drift_check.py` | **DRIFT 0** |
| 全量快集 | `coverage run --source=gcm scripts/ci_run_fast.py` | 见 evidence/G83-batch.json（后台跑，完成后回填） |
| 打包 exe E2E | 未跑 | 发布时按 RELEASE_CHECKLIST 执行 |

**G83 教训**：
①自适应退避的纯函数 `next_interval_minutes` 里 `None`（无历史）与 `0`（有变更）
语义不同，`not streak` 会把两者合并——分别判。
② MainWindow 默认 `auto_update_minutes=0`（关），接线测试必须先显式开启，
否则 `_note_batch_change` 的提前 return 会让 streak 永远是 None。
③ ruff 的 `scripts/` 检查是本地宽口径（CI 只查 gcm）——引入新脚本时别
把 CI 范围外的存量 E501 当成自己的回归。

## 历史（2026-11-02 · v9.15.0 C3 收官 + C4 检索）

| 项 | 命令 | 结果（实测） |
|---|---|---|
| 质量闸 | `ruff check gcm` / `mypy gcm` | All checks passed / Success（**114** 源文件） |
| 全量快集 | `coverage run --source=gcm scripts/ci_run_fast.py` | **Ran 1806 · 0 失败 · 0 错误 · 4 skipped** |
| 覆盖率 | `coverage report --include="gcm/*"` | **82%**（门禁 Win 81 / POSIX 80；比上版低 1 点，因新增 5 个模块的防御分支） |
| 文档漂移（7 类）/ i18n | `doc_drift_check.py` / `i18n_coverage.py` | **DRIFT 0** / **I18N OK** |
| except / pass-only | AST 统计 | **667 / 282**（pass-only 未涨） |
| C3/C4 定向 | `tests.test_c3_data`（15）· `tests.test_c4_search`（24） | 全绿 |
| 打包 | PyInstaller → zip → health_check | exe 41,842,989 B · zip 80,583,107 B · **7/7 OK** |
| 打包 exe E2E | `dist/Git-clone-Max.exe --cli --json` | exit 0 · cloned · 直连 |
| CI 三平台 | 提交 `4ccf725` | **7/7 job success** |
| Release 复验 | `gh release download v9.15.0` + sha256 + 真跑 | 逐字节一致 · 下载版 exit 0 |

**本轮教训（勿重犯）**：
①**别把 schema 版本钉死在与它无关的用例里**（`test_g81_knowledge` / `test_c3_data` 各犯一次）——
应断言「等于 `_SCHEMA_VERSION` 且 ≥N」。
②**外部内容 FTS 表的 `count(*)` 不能用来判"索引是否存在"**（它从内容表读，永远等于 repos 行数）
→ `ensure_fts()` 直接对齐（重建成本毫秒级，调用点只有两个）。
③**活文档标题里不要写版本号**（`慢测定位.md` §7 因此撞了版本守卫）。
④**新搜索功能必须同时测「两字中文」**：这是 trigram 的硬下限，不测就等于没做。

---
> 下面是历史记录（v9.14.0 / v9.13.0 / v9.12.x / v9.11.0 及更早），保留供考古；数字以本文件顶部为准。

> 下面是历史记录（v9.13.0 / v9.12.1 / v9.12.0 / v9.11.0 及更早），保留供考古；数字以本文件顶部为准。

> 下面是历史记录（v9.12.1 / v9.12.0 / v9.11.0 及更早），保留供考古；数字以本文件顶部为准。

> 下面是历史记录（v9.12.0 / v9.11.0 及更早），保留供考古；数字以本文件顶部为准。

> 下面是历史记录（v9.11.0 G82 及更早），保留供考古；数字以本文件顶部为准。

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


## 2026-10-25 · CI 三连修（413 用例的平台差异）

tag v9.10.3 push 后 CI 全平台跑，Windows/Linux/macOS 唯独 macOS 红在
`test_oversized_body_is_413_and_not_read`。三轮实测（每轮 errno 都不同）：

| 轮 | 断言 | macOS 实际 | 结果 |
|---|---|---|---|
| 1 | 只收 HTTPError(413) | URLError(Errno 32 Broken pipe) | 红 |
| 2 | 收 32 但文案 "Broken pipe" | URLError(Errno 54 Connection reset by peer) | 红 |
| 3 | 只收 54 文案 | URLError(Errno 32 Broken pipe) | 红 |
| 4 | **errno 无关**：broken pipe 或 reset by peer 皆可 | — | 待验证 |

教训：**跨平台网络测试不要锁具体 errno/文案** —— 服务器关闭连接时客户端收到
EPIPE 还是 ECONNRESET 是 OS 内核时序决定的，同一平台都会变。断言应该锁定
**行为**（服务端没有吞下超大 body），不是**表象**（哪个 errno）。


### push 前必跑守卫族（2026-10-25 补，S3 轮 CI 红两次后立规）

改 `gcm/ui/main_window.py` 或新增 handler 后，push 前必须本地跑：
`tests.test_g79_repo_hygiene`（行数/文件数/超限集合）+ `tests.test_g80_hygiene`
（except 预算）+ `tests.test_g67_docs_freshness`（版本漂移）——这三族是
「文档/基线与代码同步」的守卫，本地绿 CI 才可能绿。
（S3 轮两次 CI 红：一次版本残留、一次行数+except 基线漏同步。）
