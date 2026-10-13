# Git-clone-Max 架构资产（2026-09-26 逆向生成；2026-10-13 校准）

> 依据真实代码（`gcm/` 下 60 个 `.py` 源文件）整理，供交接/审计/后续迭代定位。
> **版本号不写死在本文件**：唯一事实源是 `gcm/__init__.py` 的 `__version__`（当前值见该文件）。

## 1. 分层架构

```
┌─────────────────────────────────────────────────────────┐
│ 入口层                                                     │
│  gcm/__main__.py（GUI/CLI 分发）│ cli.py（--cli/--json/--diag/--serve）│ mcp_server.py│ http_api.py  │
└──────────────┬──────────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────────┐
│ UI 层（PyQt6，全部离屏可测）                                  │
│  main_window.py（主窗/编排）→ settings_panel/manage_panel/    │
│  progress_table/statistics_dialog/failure_panel/help_dialog  │
│  onboarding/tray/theme(qss_validate)/download_tools          │
│  log_buffer/manage_model/repo_detail_dialog/local_repos_dialog│
│  scan_worker                                                 │
└──────────────┬──────────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────────┐
│ 应用层（无 UI 依赖，可独立测试）                                │
│  engine.py（统一调度）→ worker.py（并行克隆）                    │
│  retry_queue/watchdog/runtime_sessions/progress_gc           │
│  alerts/sched_reports/host_probe/diag/gh_api                 │
│  updater/notify/im_notify/clipboard_watcher/scanner/proxy/token│
│  single_instance/autostart/exc_dump/applog/system_theme      │
└──────────────┬──────────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────────┐
│ Git 服务层                                                    │
│  gcm/git/service.py（clone/fetch/merge/mirror/LFS/SSH/弱网降级）│
│  gcm/git/archive.py（git archive 快照）                        │
└──────────────┬──────────────────────────────────────────┘
               ▼
┌─────────────────────────────────────────────────────────┐
│ 数据层（SQLite + JSON 文件）                                   │
│  repo_db.py（WAL/BEGIN IMMEDIATE/迁移链）→ settings.py（加密     │
│  落盘）/history.py/lists.py/runtime_sessions/progress_gc        │
│  gcm/util/filelock.py（跨进程写锁）                             │
└─────────────────────────────────────────────────────────┘
```

## 2. 核心数据流（下载链路）

```
用户输入 URL → url_lib.parse_urls → RepoSpec[] → engine.launch
  → 去重(folder_name) + 批次流控(≤64 惰性补投) + 断点预检
  → worker(CloneWorker) 调 GitService.sync
      ├─ 弱网降级 treeless → depth1
      ├─ 按 host 注入凭据/镜像/LFS/代理
      └─ 结果 SyncResult 回传
  → engine._on_result：DB 落库 + progress 内存态 + 重试/会话更新
  → engine._on_worker_done：GC + 并发记忆持久化 + finished 信号
  → UI 进度表/日志/统计更新
```

## 3. 关键设计决策（各 1 行，详见 docs/adr/）

| ADR | 决策 |
|-----|------|
| ADR-001 | 引擎统一调度：worker 不写 DB/不做状态，全部收敛到 engine 单线程（避免 SQLite 并发写死锁） |
| ADR-002 | 断点续传用 progress.json 原子写 + 进程级锁；批次 ≥64 惰性补投防 OOM |
| ADR-003 | 凭据加密落盘（DPAPI/Keyring/XOR 降级）+ redact 全出口打码 |
| ADR-004 | 生态 agent 化：CLI --json 契约 + MCP 只读 + HTTP API 默认只读（显式 enable 才可写） |

## 4. 模块规模基线（2026-10-13 用 `wc -l` 实测）

| 模块 | 行数 | 职责 |
|------|------|------|
| `gcm/ui/main_window.py` | 1742 | 主窗编排（G45-6 拆分后仍偏大，新 UI 逻辑落独立模块） |
| `gcm/git/service.py` | 1170 | Git 操作全链路 |
| `gcm/ui/settings_panel.py` | 899 | 设置 UI（含 G53 数据洞察组） |
| `gcm/app/engine.py` | 888 | 调度/并发/进度/重试/会话 |
| `gcm/db/repo_db.py` | 802 | SQLite 数据层 |

> 上表 5 个文件**均超出项目硬上限 800 行**（见 `CLAUDE.md` §3），已登记在
> `计划书/下一步改进指南.md` 的 G67-14。行数会随迭代漂移 —— 引用前请现测。

## 5. 可观测性现状

- 日志：app.log（RotatingFileHandler 5MB×3，redact）+ error.log（excepthook 兜底）
- 诊断：CLI --diag --json（网络+环境体检）/ help 排错地图 / G53 存储看板
- 自检：启动耗时记录 + health_check.py 产物健康
- 局限：无远程 Metrics/Tracing（桌面单机工具，无服务端）；如需 SaaS 化需引入结构化遥测
