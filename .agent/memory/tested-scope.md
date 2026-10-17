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
| 2026-10-18 v9.4.7 | 见 `evidence/N6-final-fast-suite.json`（盲点接线后最终运行） |
| 2026-10-17 v9.4.5 | 连跑 3 次：1115 / 1115 / 1122，0 崩溃（**已移除 GC 安全网**） |

---

## 3. 定向回归（改对应区域时跑，秒级~分钟级）

| 区域 | 命令 |
|------|------|
| 接线/生命周期/环境守卫 | `python -m unittest tests.test_g69_regressions` |
| Webhook 幂等/审计/重试 | `python -m unittest tests.test_g69_notify_hardening` |
| 引导清单 | `python -m unittest tests.test_g69_checklist` |
| **接线缺口（搜索/高亮/收藏/Star/告警配置）** | `python -m unittest tests.test_g69_wiring_gaps` |
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
