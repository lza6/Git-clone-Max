---
topic: known-issues
updated: 2026-11-03
owner: memory-keeper
---
# 已知问题与边界（known-issues）

> 只记录**真实存在**且尚未解决的问题。修掉一个就删一条。
> 最后更新：2026-11-01（v9.12.0 / Rev7 C1 批：12 项接线与门禁缺口收口；剩余项见文末「仍然开放」）

## v9.12.0 已收口（2026-11-01，Rev7 C1 批）

| # | 原问题 | 结论 |
|---|--------|------|
| 1 | `host_concurrency` / `time_budget_min` **无设置页 UI 控件**（用户够不到） | **已闭环**：新增 `ui/settings_network.py`（两控件 + 说明），三段接线有 `test_c1_wiring.TestSettingsNetworkWiring`（4 例） |
| 2 | `host_concurrency` 构造参数**被 `__init__` 硬编码覆盖**（传 9 得 4、传 0 也得 4） | **已闭环**：删掉覆盖行；`TestHostConcurrencyInjection`（3 例）含「0=不限流」端到端 |
| 3 | `set_host_concurrency` 运行中切换时新旧两代闸门短暂并存 | **保留**（docstring 已写明的工程取舍）：并发可能瞬时略超新上限；建议启动前改 |
| 4 | `cancel_stuck_tasks()` **零生产调用点** | **已闭环**：失败汇总面板新增「疑似卡死」条 + 取消按钮（`test_c1_wiring.TestStuckTaskUiEntry` 3 例，含 AST 接线断言） |
| 5 | `discard()` 只停 `_retry_timer`，progress/stuck 定时器继续跑（C-6） | **已闭环**：`TestDiscardStopsAllTimers`（带 QApplication 真实断言） |
| 6 | 用户改并发被自适应顶回旧值（C-25） | **已闭环**：`set_concurrency(by_user=)`；`TestSetConcurrencyUpdatesAdaptMax`（3 例） |
| 7 | 流控上限恒 64 > 池上限 32（C-12） | **已闭环**：`batch_start_limit()` 派生；`TestBatchStartLimitDerived`（3 例） |
| 8 | 并发记忆恢复被 `retry_queue` 短路（C-13） | **已闭环**：`TestConcurrencyMemoryNotShortCircuited`（2 例） |
| 9 | `_FLUSH_INTERVAL` 死常量（C-22）/ `wal_checkpoint` 零生产调用（C-24） | **已闭环**：删常量；批次收尾调 `wal_checkpoint(PASSIVE)`（各 2 例） |
| 10 | 文档数字六处同时过期、无守卫 | **已闭环**：`scripts/doc_drift_check.py`（首跑报 18 处，现已 DRIFT 0）+ `tests/test_doc_drift_guard`（8 例含反向自检） |
| 11 | 函数规模无守卫（文档称 14 个，实测 20 个） | **已闭环**：`tests/test_function_size_budget.py`（集合相等 + 反向自检） |
| 12 | `requirements.txt` 未锁版本；CI 裸装 ruff/mypy | **已闭环**：`PyQt6==6.11.0`；CI 改读 `requirements-build.txt`；`TestDependencyPinning`（4 例） |
| 13 | **启动路径漏接线**：`MainWindow.__init__` 手写第二份 settings→engine 列表，漏 `host_concurrency`/`time_budget`/`concurrency_state_path` → 改设置重启后引擎仍读默认值（控件回填却是对的） | **已闭环**：启动期改走 `build_sync_engine`（唯一映射点），`TestStartupEngineReflectsSettings`（4 例）；空闲引擎 `enable_retry_queue=False` 防启动即自动重投污染 host 列 |

## v9.12.1 已收口（2026-11-01）

| 项 | 原问题 | 结论 |
|---|--------|------|
| 1 | **内置国内镜像默认开启，实测比直连慢 8 倍**（24.8s vs 3.0s，同一仓库各两轮），且每仓克隆前还要探测候选 | **已闭环**：`DEFAULT_GITHUB_MIRRORS` 清空 + 默认值改 False + UI 开关移除；守卫 `test_no_builtin_presets_at_all`（钉死"必须为空"） |
| 2 | **X-4**：1366×768 笔记本上「保存清单/载入/导入 Star」被顶部滚动区裁到 10px（`visibleRegion` 86×10/86×42），1080p@150% 下 0×0 | **已闭环**：清单行搬出滚动区（`build_task_list_row`）+ 动作区不可压扁 + 矮窗口让可滚动区让位；守卫 `test_ui_compact_visibility`（9 个尺寸 + 变异自检） |
| 3 | **X-5**：72 个 `tr()` 键不在字典里 → 英文模式原样显示中文 | **已闭环**：补齐 72 键（含英文翻译）+ `scripts/i18n_coverage.py` + `tests/test_i18n_coverage.py`（三条断言） |

## 仍然开放（2026-11-04 G84 逐条复核后）

> 复核纪律：每条都必须能指到**现在的代码**。已完成的三条曾在这里挂了两个版本
> （C2/C3/C4/C5 早已交付），因为没有任何守卫盯「这条还成立吗」—— 现在由
> `tests/test_g84_wiring.py` 守「写好的必须有调用点」这一类，但**叙事型条目仍需人复核**。

| # | 事项 | 状态 |
|---|------|------|
| 1 | ~~1366×768「保存清单/载入/导入 Star」被裁到 10px~~ | **已闭环**（v9.12.1 X-4：清单行搬出滚动区 + `test_ui_compact_visibility` 9 尺寸） |
| 2 | ~~管理页「仓库大小」列永久空白（600ms 定时器猜算完时间）~~ | **已闭环**：C2-2 改「线程算完 emit 回填」+ C3-1 两级缓存；**G84 补上最后一环**——`invalidate_size_cache`/`clear_dir_sizes` 此前零生产调用点（刷新按钮没接），现已接到「刷新列表」并有守卫 |
| 3 | ~~`prune_history` 全表窗口函数跑在 GUI 线程；无分阶段耗时埋点~~ | **已闭环**：C3-2 改为批次收尾 `prune_history_if_due`；C3-4 `stage_json` 四段埋点（v9.14/v9.15） |
| 4 | ~~检索 LIKE 单路；trigram 对两字中文零命中~~ | **已闭环**：C4 双路（≥3 字 FTS5 bm25 / <3 字 LIKE），`test_c4_search` 24 例 |
| 5 | ~~MCP 仅 4 个只读工具；事件不落盘；本地 API 无 token~~ | **已全部闭环（2026-11-13 复核）**：`clone_batch`（`--allow-mutate`）+ `api_token` Bearer + G84 `event_sink` 接生产入口 + **G84 已实现 `resources/list`、`resources/read`、`prompts/get`**（`mcp_server.py:595-616`，复制资源 3 个 + 提示词 3 个 + diagnose-failure）。MCP 长任务 progress/cancel 已由 mcp_jobs 异步作业承担（job_status/job_cancel）。**无剩余缺口** |
| 6 | 英文模式 tr 键回退中文（历史登记 58 键） | **已复核（2026-11-05 Rev14 收尾轮，真实运行）**：`set_language("en")` + 真实建窗，页签已显示 `Download Center / Repos / Settings & Log`，窗口标题已英文；**已全部闭环（2026-11-08 复核）**：按钮 0 残留（Rev14 已迁 tr()）；菜单项 11 条缺 EN 字典 key 已补齐（带 `…` 后缀的 key 与字典不带后缀的旧 key 不匹配是根因）。EN 模式真实建窗扫描：按钮/标签/复选框/菜单**零中文残留** |
| 7 | ~~MCP `clone_batch` 在 stdio 循环里同步跑子进程（timeout=3600），最长 1 小时无 progress、不可取消~~ | **已闭环（Rev14 复核，代码为准）**：`gcm/mcp_jobs.py` 已存在，`clone_batch(async=true)` + `job_status` 轮询 + `job_cancel` 杀进程树（`mcp_server.py:108,119,219` 有工具声明与使用指引）。原登记指向的 `mcp_server.py:288-294` 已不是同步长跑路径 |
| 8 | ~~`backup_to()` 备份非原子：`wal_checkpoint(TRUNCATE)` + `shutil.copy2`，无 tmp+replace/无覆盖保护/无 `quick_check`~~ | **已闭环（Rev14 复核，代码为准）**：实现已抽到 `gcm/db/backup.py`（G84-10 强化），顺序是 checkpoint → tmp → fsync → **先 `quick_check` 再 `os.replace`**，失败清理 tmp 并上抛。UI 文案「原子写 + quick_check 校验」与实现一致（`settings_actions.py:186-189`） |
| 9 | ~~管理页「健康 5/5」无解释（ToolTip 只挂 col 0/2/3/4）~~ | **已闭环（Rev14 复核）**：`manage_model.py:216-221` 已给 col 6（健康分口径：近 20 次成功率 50% + 陈旧度 25% + 冲突惩罚 25%）与 col 7（大小未算出的原因）挂 `ToolTipRole`。**仍未做**：`mirror_used`/`degrade_path` 只到 CLI、GUI 看不到 |

## Rev14 收尾轮新发现（2026-11-05，全部有真实运行证据）

| # | 事项 | 状态 |
|---|------|------|
| 10 | **断点续传跳过的仓库不产生终态**：用户把已下过的仓库再贴一次点「开始」→ 行永远停在「等待中」、`本次/详情/耗时` 被抹成「—」、通知「成功 0」、状态栏一直「并行同步中」（GUI 实测复现）。根因 `engine.launch()` 的 `_precheck_skip` 命中后只 `_emit_line` + `_mark_done` + `continue`，不 emit result；全跳过时 `finished.emit()` 但零结果 | **已闭环（Rev14 F2，同日）**：引擎新增 `skipped` 信号 + `launch(report_skipped=)`；GUI 行到终态、小结报「跳过 N」；守卫 `tests/test_rev14_skipped`（3 例） |
| 11 | **小屏「下载位置 / 克隆模式 / 层数」被裁到 6–23px**（几何断言：1024×768 下 `target_edit` 可见 352×6/352×34；1366×768 同样；1440×900 无裁切） | **已闭环（Rev14 F1，同日）**：三个控件移出滚动区成常驻行；契约清单已补入，27 组合 + 900×480 **零裁切**；折叠阈值重扫为 720/740/760 |
| 12 | ~~验收工具的数据洞：种子不建真实目录/不给 sha/耗时~~ | **已闭环（Rev14 V3 复核 2026-11-08）**：`ui_screenshot_audit.py:91` `_seed` 已为每列造真实数据（真实目录 + `head_sha` + `duration_ms`），docstring 明写「每一列都要有数据」 |
| 13 | ~~**`--diag` 单独用会静默走 GUI 单实例路径**~~ | **已闭环（Rev18 B3-1，2026-11-14）**：help 早已写明（cli.py:368-369）；GUI 路径新增 `_warn_diag_without_cli` —— 单独 `--diag` 打 applog WARN + stderr 双通道告警；守卫 `tests/test_g85_diag_alone`（4 例） |

## 2026-11-08 收口（Rev10 稳定性批次 + Rev11-C 悬空项）

| 项 | 原状态 | 结论 |
|----|--------|------|
| S-01 app.log 生产环境从未生成 | 开放（P0） | **已闭环**：`applog.set_default_log_path` + `__main__._init_applog`；真机子进程生成 app.log，引擎级 error 真实落盘 |
| S-02 写库异常无 rollback | 开放（P0） | **已闭环**：6 个裸写方法迁 `write_txn`；触发器注入失败后事务已回滚 |
| S-03 迁移失败强推版本号 | 开放（P0） | **已闭环**：只有 v8 允许降级，其余上抛；`user_version` 分段成功后写；重开库自愈 |
| S-04 `tag:` 搜索永久零命中 | 未登记 | **已闭环**：三口径统一到竖线 + 历史逗号兼容 + `-tag:` 早退修复 |
| S-05 `sanitize_name` 污染网络 URL | 未登记 | **已闭环**：URL 保原始段，仅 `folder_name` 清洗（`geocar/con` 不再变成 `con_.git`） |
| S-06 定时队列窗口内重复启动 | 未登记 | **已闭环**：`already_ran_in_window_today` 闸；真实时钟同窗二次 tick=0 |
| S-07 `update_status` 读改写分两临界区 | 未登记 | **已闭环**：走 `_rmw`；20 线程并发零丢失 + AST 守卫 |
| S-08 GUI 线程 38ms/仓 | 未登记 | **部分闭环**：`update_results` 批量 789.6ms→3.0ms（263x）+ 帧缓冲 + 丢帧还原；DB 写仍在 GUI 线程（实测 3ms/仓可接受），后台执行器未做 |
| S-09 看门狗一次派发失败即永久失效 | 未登记 | **已闭环**：派发闸门 TTL 300s 自动复位 |
| S-10 错误分类两处误判 | 未登记 | **已闭环**：publickey→auth；状态码需 HTTP 语境词 |
| R-01 结论条就绪态（Rev11-C，悬空 4 轮） | 开放 | **已闭环**：`start_all` → `plan_batch` → `set_ready`；假注释已删；守卫 4 例 |
| R-05 裸空态未清零 | 开放（部分） | **已闭环**：四处裸 `QLabel` 全走 `empty_state` 工厂，复扫 0 命中 |
| R-06 大小列空串 | 开放（半做） | **已闭环**：空值显示 `—` |
| P-01 管理页按钮权重 | 未做 | **已闭环**：QSS 增 secondary/ghost 两档 + 9 按钮按语义赋权（`ui/button_weights.py`） |
| P-03 事件流未中文化 | 未做 | **已闭环**：类型/状态走 i18n key；真对话框实测显示「任务结果 / 成功」 |
| P-04 失败面板排版 | 未做 | **已闭环**：次数列右对齐 + mono |
| Rev12 缺 evidence/rev12-*.json | 违反自定门禁 | **已补**：`evidence/rev12-usability.json`；Rev12 记录追加 §4 更正（U-13 已被 Rev14 focus_order 覆盖） |
| R14-18 i18n 长尾 | DOING | **批次 3 完成**：棘轮 1288→1128（实迁 160 条），EN 真窗零残留；批次 4–5 余量以日志/协议层文案为主，明确不再推进（棘轮防恶化） |

## CI 现状（2026-11-08 v9.19.0 推送实测）

| Job | 结果 | 说明 |
|-----|------|------|
| test (windows-latest) | ✅ | 主目标平台 |
| test (ubuntu-latest) | ✅ | — |
| fast-smoke / e2e-smoke / perf-baseline | ✅ | — |
| packaging | skipped | 仅 tag 触发（见下） |
| **test (macos-latest)** | ❌ **预存失败（环境阻塞）** | `test_ui_compact_visibility` 两条：(1240,729) 裁切断言 + 其反向自检。**在 `0890f0e`（本轮改动之前）逐字相同地红**（同测试名、同尺寸），属 macOS 字体度量差异；本机无 macOS 环境无法复现与验证修法，盲改会危及 Windows（主目标平台）—— 标为环境阻塞，非本轮回归 |
| **slow** | ✅ 已修 | `tests/test_scanner.py::test_rows_and_data` 断言大小列为空串；R-06 改显示 `—` 后该断言过期 → 已更新断言，慢集 129 例 OK，CI slow job **success** |

**教训（加入 D 系列候选）**：改「用户可见的默认值」时，要 grep **慢集**里的对应断言 ——
快集口径覆盖不到 `tests/test_scanner.py`，只跑快集就会漏。

## 未闭环功能

（当前无。所有登记项均已关闭或判定不适用。）

## Rev16 B4 复核（2026-11-13）

- **repo_input maxHeight 96 分支为真机-only 路径**（Rev16 复审 P3）：`_h0 <= 560 and folded`
  在 offscreen/本机因 `effective_minimum` 夹取（实际最小高 584）不可达；真机
  1366×768@150% 夹出 900×504 时可达。行为已实测正确（压 96 / 恢复 112），
  属屏幕夹取逻辑耦合的盲区，无自动化覆盖。
- **`test_g76_wiring` 单模块 exit 127 已更正登记**：本机实测（venv 解释器，bash 与
  PowerShell 双通道）26 例全绿、退出码 0；`exit 127` 的历史现象是调用方用了
  **裸 `python`**（系统 3.14，无项目依赖）——命令解析/依赖导入失败所致，
  与测试隔离无关。守卫命令必须用 `./.venv/Scripts/python.exe`。
- **固定像素基线已入库**：`docs/screenshots/`（34 张，B3 布局改完后整集刷新），
  `scripts/update_ui_baseline.py --verify/--update` 是唯一更新入口；2px 平移
  反向自检实测报警（10.8% 超容差）。

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

## G69-22 第三轮审查整改（2026-10-18，v9.6.1）

| 级别 | 问题 | 修法 |
|------|------|------|
| HIGH | treeless/depth1 降级克隆与子模块更新漏清 `http.extraHeader`（同类残留泄漏面） | 所有镜像命令统一 `-c http.extraHeader=` + 镜像环境；origin 在子模块更新前恢复 |
| HIGH | 环境清理误删 `GIT_CONFIG_GLOBAL/SYSTEM/NOSYSTEM`（企业代理/CA 丢失） | 只删 COUNT 与 KEY/VALUE_n |
| MED-HIGH | 仓库 404 被当成「镜像挂了」，整批拉黑 120s | 只有网络/超时类失败才拉黑前缀 |
| MED | 预检失败 + 镜像不可用仍等 retries×600s | 快速失败并明确提示 |
| MED | 关窗双重 drain，最坏 20s | 移除 closeEvent 的额外 drain |
| MED | bare 回退零测试 | 新增 3 条 |
| LOW-MED | bare 分支无条件改写 origin | 与普通分支一致的 origin 同源守卫 |
| — | 作用域含 userinfo、auth 探测无 single-flight、单分支拉全部分支、测速写回删自定义前缀、雷达图未 i18n | 均已修 |

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

### G69-20 盲点清单（2026-10-24 G80-B2 **逐条复核后**更新）

> 复核纪律：**不要相信本表的旧结论**，每条都以「生产调用点 / UI 三段（控件→保存回调→消费方）」
> 重新验一遍再改状态（`docs/计划书/README.md` §1.3 同款口径）。

| # | 问题 | 现状（G80-B2 实测） | 证据 |
|---|------|------|------|
| 9 | `post_clone_hook` 只读不写 | **已闭环（G76）**：设置页有输入框 + 保存时二次确认 | `ui/settings_groups.py:250-257` · `_save_post_clone_hook` |
| 10 | `manage_model.rowsChanged/visChanged` 无 connect | **已闭环**：管理页已连 `rowsChanged` | `ui/manage_panel.py:139` |
| 11 | `ScanWorker.progress` 已 emit 未 connect | **已闭环（G76）**：对话框已连并改确定态 | `ui/local_repos_dialog.py:166-168` |
| 12 | `check_latest_with_sha` 无调用 | **已闭环（G76）**：更新检查已用它拿 sha256 | `ui/update_flow.py:56` |
| 13 | `is_data_dir_private` / `migrate_data_dir` 无调用 | **已闭环（G76）**：数据目录私密性提示 + 迁移入口 | `ui/settings_actions.py:97-112` |
| 14 | 5 个只读不写字段 | **4 个已接线**（`clone_timeout` / `minimize_to_tray` / `watchdog_silent` / `sched_report_format`），`retry_backoff_base_sec` **已闭环（v9.10.3，A-2-6 修正）**：设置页已有写入控件（`ui/settings_groups.py`），三段全通 | `ui/settings_actions.py:64-87`（4 个 save）· `ui/settings_panel.py:245`（仅读） |
| 15 | ~~`quick_repos` 无编辑入口；`depth` 字段完全无引用~~ **已闭环（v9.10.0）** | — | `quick_repos` 已由 G76 接写入侧；`depth` **已删除**（G79：它是唯一零读取的僵尸字段，随 `asdict` 落盘但永不生效）；顺带修掉 `TestG69_18NoZombieSettingsFields` 的同名局部变量盲区 |

其余仅测试引用的内部函数（`token.py`、`slow_remote_hint`、`backup_to`、`TaskLists.delete` 等）
记录在 `workflow_status.md` §11，未删除 —— 删代码前需确认没有外部脚本依赖。

## 已知不稳定

| 项 | 现象 | 频率 | 说明 |
|----|------|------|------|
| `test_g67_closure.TestShutdownClearsInProgress` | 高负载下偶发失败 | 3 次全量里约 1 次 | 子进程 + 立刻 shutdown 的竞态；单独跑稳定 |
| `test_single_instance.TestSingleInstanceCrossProcess` | 同上 | 同上 | 跨进程锁 + 端口时序 |
| `test_g66_patch` 的 schtasks 用例 | 已改为与语言无关判据，但**外部工具行为**仍受环境影响 | 低 | 只断言「无 U+FFFD + 可无损回编码」 |
| ~~`test_manage_ui.test_check_update_new_version_asks_and_opens_browser`~~ | ~~快集全量下偶发~~ | **已根治（Rev18，2026-11-14）**：根因不是线程时序而是**测试隔离缺陷** —— `__init__` 的 `QTimer.singleShot(2500, self._check_update_silent)` 捕获绑定方法，实例属性置换拦不住；定时器到点后走真网络（`check_latest_with_sha` 未 mock 的 urlopen），慢于泵窗口即偶发红。修复：setUp 类级短路该定时器注册 + 补 mock `check_latest_with_sha`。修复后连跑 10/10 全绿 |

## 环境边界

- ~~**CI 主矩阵用 `pip install PyQt6`（未锁版本）**~~ **已过期（2026-10-24 G79 更正）**：`.github/workflows/ci.yml` 4 处均为 `PyQt6==6.11.0`，覆盖率工具为 `coverage==7.16.0`；`requirements-build.txt` 另锁 ruff/mypy/coverage/pyinstaller。CI 依赖已完全锁定。
- **CI 只在 Linux 跑慢集**；Windows 特有的编码/路径问题必须本机验证。
- **打包构建未纳入 CI**：exe 需人工重建 + `health_check.py` 验证。

## 历史遗留（不建议动）

- 老 tag（`v1.0.1` / `v1.1.0` / `v2.0.1` / `v2.0.2` / `v4.3.1`）指向的提交 `__version__` 落后一个 patch。改历史 tag 风险大于收益。

## G71（v9.8.0）新增边界

1. **EventBus 是进程内的**：`--serve` 独立进程看不到 GUI 引擎事件。要让 agent
   看到真实事件，需在设置页开启「本地 API / 事件流」（GUI 进程内托管）。
   这是设计边界，不是 bug；文件桥/DB 桥未实现。
2. **事件不持久**：环形缓冲 500 条，退出即丢；跨重启审计用 `app.log` /
   `progress.json` / `notify.log`。
3. **SSE 无鉴权**：与既有 HTTP API 同边界（只绑回环 + Host/Origin 校验）。
   事件已打码，但不得经无鉴权反代暴露公网。
4. **重试判据唯一来源**：`app/errors.classify_error`。新增错误文案时不要在 UI
   再写关键字表，否则会出现「队列认为可重试、UI 显示另一类」。
5. **`git/service._update` 拆分后的回归面**：`_fetch_remote` 仍 144 行，镜像回退
   语义虽逐行保留，但改动该函数必须跑 `tests.test_g58_mirror_failover`。
6. **`path=None` 的 RetryQueue 是纯内存**：G71 修复了「记什么丢什么」；但进程
   退出仍不保留（这是预期语义，不是持久队列）。

## G75 收口轮新发现的**外观**边界（2026-10-23，非阻塞）

1. ~~**1024×768 @125% 缩放下「快速填充」行被切成半截**~~ **已修（v9.9.4），且病因登记有误，特此更正**：
   真实病因是 `quick_scroll.setFixedHeight(38)` 写死，而 chip 按钮 `sizeHint().height() == 42`
   → **每颗 chip 底部 4px 被裁**，**与缩放无关**（1440×900 标准模式就能看到）。
   改法：`sync_quick_scroll_height()` 按 chip 实际高度推导 + 在 `rebuild_quick_fill()` 与
   `_apply_layout_mode()` 两处重同步；守卫 `tests/test_g78_quick_scroll`（3 项几何断言）。
   教训：**"看起来像缩放问题"要先量尺寸**（`sizeHint` vs `fixedHeight`），别把像素常量与
   设备缩放混为一谈。
2. **更新检查的 `urllib` 直连路径在本机无法实测**：本机 TLS 代理会打断 Python `urllib`
   与 git schannel（`SSL: UNEXPECTED_EOF_WHILE_READING`），而 `requests`/PyGithub 正常。
   验证改用 `requests` 取真实 Release JSON 再喂生产解析逻辑（只换传输层）。换机器/CI 网络可直测。
