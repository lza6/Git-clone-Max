# 已测试范围（tested-scope）

> **下次改代码前先读这里**，避免重复跑同一批验证。
> 时间戳 = 最后一次真实运行；命令可直接复制。

---

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
