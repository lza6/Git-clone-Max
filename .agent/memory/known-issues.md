# 已知问题与边界（known-issues）

> 只记录**真实存在**且尚未解决的问题。修掉一个就删一条。
> 最后更新：2026-10-17（计划文档二次审计后）

## 未闭环功能

| 项 | 现状 | 影响 | 建议 |
|----|------|------|------|
| `G53-1` 雷达图 / `health_card` 列 | 统计中心有**派生视图**（6 维），但未落 `health_card` 列、无雷达图 | 用户能看到评分，但拿不到可查询的历史列 | 若要落列，需 DB 迁移 + 回填；先确认产品是否需要（T3） |
| `G58-2` 镜像测速择优 | 未实现（只有静态「镜像前缀」配置） | 用户需自己选镜像 | 可做：对 `mirror_prefix` 里的候选做 HEAD 测速排序 |

## 已撤销 / 不适用（不是缺陷，别再当 todo）

| 项 | 结论 | 依据 |
|----|------|------|
| `G58-1` 统一限流 | **不适用**：模块在 v9.2.0 因零生产调用点被删除；git 侧限速由 `settings.rate_limit_kbps` → `http.lowSpeedLimit` 承担 | `gcm/__init__.py` changelog 9.2.0；`docs/perf-observability.md` §3 |
| `G58-3` 代理凭据 | **已闭环**：`proxy` 已加密落盘（`_encrypt_token` / `_decrypt_or_keep`）；完整代理 URL 直接注入 `http_proxy`，git 原生支持 `user:pass@host` | `gcm/db/settings.py:366,407-409`；`gcm/git/service.py:492-494` |
| `G54-4` 引导清单 | **已闭环**（v9.4.3） | `gcm/ui/checklist.py`；`tests/test_g69_checklist.py` |

## G69-20 盲点扫描（2026-10-17）

> 独立只读代理做 AST + grep 全量扫描，找出 15 项「定义了但用户不可达」。
> **已修 8 项**（有 `tests/test_g69_wiring_gaps.py` 守卫），**仍在 7 项**。

### 已修

| # | 问题 | 修法 |
|---|------|------|
| 1 | `filter_rows` 有定义、无控件，且 `_vis` 从未被视图消费 | 管理页搜索框 + `setRowHidden` + 刷新后保持过滤 |
| 2 | `is_stale` 有定义，`data()` 无颜色角色 | `BackgroundRole`/`ForegroundRole` + tooltip |
| 3 | 日志「关键字高亮」未安装 | 设置页安装 `make_highlighter` |
| 4 | `crypto_warning()` 从不调用 | 启动写 WARN 日志 |
| 5 | `set_favorite`/`set_excluded` 无 UI 写入点 | 管理页右键菜单两项（批量切换） |
| 6 | `fetch_starred` 无 UI 入口 | 输入区「⭐ 导入 Star」+ 工作线程 + 去重回填 |
| 7 | `CloneWorker.on_line` 从未使用（所有输出记到 index=0） | `run()` 把外部回调接到 service |
| 8 | AlertScanner 间隔/IM 构造期固化，改完要重启 | `reconfigure()` + 设置保存即推送 |

### 仍未修（已登记）

| # | 问题 | 影响 | 建议 |
|---|------|------|------|
| 9 | `post_clone_hook` 只读不写（无 UI） | 正常 UI 无法启用克隆后钩子 | 加设置项前先确认是否允许执行任意 shell |
| 10 | `manage_model.rowsChanged/visChanged` 无 connect | 无功能影响（视图自己刷新） | 可删信号或接状态栏 |
| 11 | `ScanWorker.progress` 已 emit 但对话框未接 | 本地扫描进度条是不确定态 | 接 `progress` → 确定进度 |
| 12 | `check_latest_with_sha` 无调用 | 更新提示不展示 sha256 | 低价值，可删 |
| 13 | `is_data_dir_private` / `migrate_data_dir` 无调用 | 目录隐私提示与迁移不可达 | 需产品决策 |
| 14 | 只读不写字段：`clone_timeout` / `minimize_to_tray` / `watchdog_silent` / `sched_report_format` / `retry_backoff_base_sec` | 只能手改 settings.json | 逐项补 UI 或删字段 |
| 15 | `quick_repos` 无编辑入口；`depth` 字段完全无引用 | 只能手改 settings.json | 补编辑对话框 / 删 `depth` |

其余仅测试引用的内部函数（`token.py`、`slow_remote_hint`、`backup_to`、`TaskLists.delete` 等）
记录在 `workflow_status.md` §11，未删除 —— 删代码前需确认没有外部脚本依赖。

## 已知不稳定

| 项 | 现象 | 频率 | 说明 |
|----|------|------|------|
| `test_g67_closure.TestShutdownClearsInProgress` | 高负载下偶发失败 | 3 次全量里约 1 次 | 子进程 + 立刻 shutdown 的竞态；单独跑稳定 |
| `test_single_instance.TestSingleInstanceCrossProcess` | 同上 | 同上 | 跨进程锁 + 端口时序 |
| `test_g66_patch` 的 schtasks 用例 | 已改为与语言无关判据，但**外部工具行为**仍受环境影响 | 低 | 只断言「无 U+FFFD + 可无损回编码」 |

## 环境边界

- **CI 主矩阵用 `pip install PyQt6`（未锁版本）**；`requirements-build.txt` 锁了 ruff/mypy/coverage/pyinstaller。上游发新版会让 CI 行为漂移。
- **CI 只在 Linux 跑慢集**；Windows 特有的编码/路径问题必须本机验证。
- **打包构建未纳入 CI**：exe 需人工重建 + `health_check.py` 验证。

## 历史遗留（不建议动）

- 老 tag（`v1.0.1` / `v1.1.0` / `v2.0.1` / `v2.0.2` / `v4.3.1`）指向的提交 `__version__` 落后一个 patch。改历史 tag 风险大于收益。
