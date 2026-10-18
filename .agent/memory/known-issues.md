# 已知问题与边界（known-issues）

> 只记录**真实存在**且尚未解决的问题。修掉一个就删一条。
> 最后更新：2026-10-18（G69-21 独立审查整改后）

## 未闭环功能

（当前无。所有登记项均已关闭或判定不适用。）

## G69-21 独立审查整改（2026-10-18，v9.6.0）

> 对抗式审查发现 2 个阻塞项 + 8 项 Required；已全部修复并有守卫。

| # | 问题 | 修法 | 证据 |
|---|------|------|------|
| 1 | **令牌随 origin 泄漏给镜像**（CRITICAL）：镜像克隆把 origin 固定成镜像地址，之后配 token 的 fetch 会把全局 `http.extraHeader` 发给第三方 | 认证头改 URL 作用域；镜像克隆后 origin 立即回源站；旧仓库更新时自动规范化；全局/系统 `http.*extraHeader` 存在时禁用镜像 | `test_g58_mirror_failover`（origin 恢复 / 规范化 / 全局凭据门禁） |
| 2 | 「测速并排序」按钮必定 TypeError（`clicked(bool)` 传给无参槽） | 槽签名加 `checked=False` | `test_probe_button_accepts_checked_arg`（真实按钮点击） |
| 3 | 测速写回覆盖其它 host、把预设固化成用户配置 | 只写用户自定义镜像、保留其它 host、尊重开关 | 2 条 UI 测试 |
| 4 | 更新回退最坏 5×(3×300s) | 候选先短超时探测 + `retries=0` | 回退测试 |
| 5 | `--mirror` bare 仓库无回退 | 补回退 + origin 恢复 | 代码路径 |
| 6 | 探测不走应用内代理 | `probe_git_url(env=...)`，Service/UI 均传代理 | 探测签名 |
| 7 | URL 内嵌凭据绕过镜像门禁 | `build_mirror_urls` 拒绝 userinfo | `test_build_urls_rejects_embedded_credentials` |
| 8 | `mirror_fallback` 关闭仍回退（文案不符） | 语义改为「失败自动重试（含回退源站）」并写清例外 | i18n/UI |
| 9 | CLI 默认走第三方镜像且无开关 | 新增 `--no-mirror` | CLI help 测试 |
| 10 | Worker 重建 Service 丢 token/凭据/LFS 等 | 全参数透传 | `TestWorkerRebuildKeepsConfig` |

## 已关闭功能

| 项 | 结论 | 依据 |
|----|------|------|
| `G53-1` 雷达图 | **已闭环（v9.6.0）**：雷达图落地；`health_card` 列刻意不加（派生视图是唯一事实源，避免冗余漂移） | `gcm/ui/radar_chart.py` · `tests/test_g53_radar.py` |

## 已关闭（勿再当 todo）

| 项 | 结论 | 依据 |
|----|------|------|
| `G58-2` 镜像测速择优 | **已闭环（v9.5.0）**：多候选 + 内置国内镜像 + `git ls-remote` 探测择优 + 克隆/更新失败回退源站；有凭据不走镜像 | `gcm/app/mirrors.py` · `gcm/app/mirror_probe.py` · `tests/test_g58_mirror_failover.py` |

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
