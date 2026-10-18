# workflow_status — 终局闭环总审计（v9.3.2 → v9.4.0）

> 本文件是**本轮唯一事实源**。只记录事实与证据，不记录推理过程。
> 生成：2026-10-16 · 基线 `4459729`（main，v9.3.2）· git 工作区干净。

---

## 0. 任务契约（用户显式需求）

| # | 需求 | 状态 |
|---|------|------|
| C1 | 主控代理编排：先规划 → 并行子代理 → 汇总 | ✅ 已执行（3 并发只读审计代理） |
| C2 | 需求追踪矩阵，逐条验证闭环状态 | ✅ §2 |
| C3 | 深度验证 `下一步改进指南.md` 全部任务 | ✅ §2（结论：**删除理由不成立**） |
| C4 | 真实 E2E 测验 / 验收 / 审计 | ✅ §5 |
| C5 | 压测（高并发 / 极端 / 数据错乱） | 进行中 |
| C6 | HTML 变更报告 + 底部测验 | 待启动 |
| C7 | 独立审查线程 → 修复 → 复验循环 | 待启动 |
| C8 | 整理成 workflow + skills | 待启动 |
| C9 | SOP / 交接文档 | 待启动 |
| C10 | 验证记录台账（避免重复跑） | ✅ `docs/验证记录-2026-10-16.md` |
| C11 | 提交 → 推送 main → Release | 待启动 |

---

## 1. 事实基础（2026-10-16 实测）

| 项 | 实测值 | 证据 |
|----|--------|------|
| 版本 | `9.3.2` | `gcm/__init__.py:2` |
| HEAD | `4459729` main，工作区干净 | `git status` / `git log` |
| 源码规模 | 66 模块 / 16076 行 | `wc -l` |
| 测试 | 121 文件；快集 1060 用例 | `ls tests/*.py` |
| ruff | **All checks passed** | `ruff check gcm` |
| mypy | **Success: no issues found in 64 source files** | `mypy gcm` |
| **远端 CI** | **最近 12 次运行全 failure**（含最新 v9.3.2） | `gh run list` |
| CI 失败形态 | Linux/macOS `exit 139`（SIGSEGV）；Windows `exit 1` | `gh api .../jobs/{id}/logs` |
| CI 通过项 | `slow` / `e2e-smoke` / `fast-smoke` **三个 job 实际通过** | `gh run view --json jobs` |
| `gh` CLI | 可用（scopes: repo, workflow, read:org） | `gh auth status` |
| git tag | 最新 `v9.1.2`（**v9.1.3~v9.3.2 未打 tag**） | `git tag --sort=-creatordate` |
| `下一步改进指南.md` | **不在工作区**；`e6a8759`(2026-10-14) 随 `计划书/` 9 文件一起删除 | `git log --diff-filter=D` |

### 1.1 关键对照实验（决定性）

| 环境 | 结果 | 含义 |
|------|------|------|
| 带本地 `data/` 的工作区 | 1060 全绿 | 本机看不见问题 |
| **`git archive HEAD` 干净检出** | **FAILED (failures=3, skipped=4)** | 抓出真缺陷 |

> 干净检出下 3 条失败**全部**源于本机 shell 注入的 `GIT_CONFIG_*` 环境变量
> （测试断言污染），**不是** CI 段错误。CI 段错误另有其因（§4 P0-1）。

---

## 2. 需求追踪矩阵 — 被删台账的独立复验

> `计划书/` 下 9 个文件在 v9.2.0 被删，README 声称「G67 台账全部闭环并经用户确认」。
> **独立复验结论：该声明不成立。** 原文已从 `e6a8759^` 取回逐条比对。

| 源文档 | 任务行数 | 标记 ✅ | 复验结论 |
|--------|---------|---------|-----------|
| `下一步改进指南.md` | 56 | 56 | **至少 6 项部分闭环 + 3 项零生产调用点** |
| `下一步改进指南-v8-历史.md` | 68 | 68 | 未逐条复验（历史基线） |
| 其余 7 个台账（验收清单/规划总览/…） | — | — | 内容已迁入 `docs/`，非死链 |

### 2.1 逐条复验结果（两轮独立代理 + 主控复核）

| G编号 | 声称内容 | 实际状态 | 证据 |
|-------|---------|---------|------|
| G52-1 自动重试队列 | 设置页「失败重试」区 + 引擎自动重投 | **部分闭环** | 引擎侧真实（`engine.py:111-134,233-285`）；**设置页无任何控件** → 默认 False 永不启用 |
| G52-3 看门狗续跑 | 设置页「看门狗」开关+间隔 | **部分闭环** | `main_window.py:211-219` 实例化；**无 UI 入口** |
| G52-5 失败原因聚合面板 | 按类聚合 + 可重试 | **真实可用** | `main_window.py:607` 按钮 → `failure_panel.py` |
| G52-6 并发自适应记忆 | 写/读 `concurrency_state.json` | **真实可用** | `engine.py:124,191-221,818` |
| G53-1 多维评分卡 | 新增 `health_card` JSON 列/派生视图 | **部分闭环** | 统计中心有展示；**未新增列**，仍单值 `repo_health`；雷达图未实现 |
| G53-2 阈值告警引擎 | 命中 → 弹通知 + 可选 Webhook | **部分闭环** | 扫描器已接；**`on_alerts` 未传** → 命中后除 IM 外用户无感 |
| G53-5 定时报表 | 后台按间隔生成到指定目录 | **零生产调用点** | `ReportScheduler` 仅被 tests 引用；`_save_g53_sched_interval/_dir` 从未 connect |
| G54-4 引导清单 | 向导后「引导清单」页 | **已闭环（v9.4.3）** | `gcm/ui/checklist.py`；首启提示一次（`checklist_prompted`）+ 设置页入口 |
| G56-2 MCP server | `python -m gcm.mcp_server` 只读工具 | **真实可用（实测）** | 无 `GCM_MCP_ENABLE` → rc=3；置 1 后 initialize 返回 9.3.2，tools/list 返回 4 工具 |
| G56-4 本地 HTTP API | `--serve` 只读 | **真实可用（实测）** | `GET /api/status` → 200 `{...,"readonly":true}` |
| G58-3 HTTP 代理认证 | 解析 + 加密落盘 | **部分闭环** | 能注入 git env；**未解析、未加密**（`settings.proxy` 明文落盘） |
| G58-5 自建实例识别/认证头 | host_auth_types 下拉 | **真实可用** | G66-9 修复已生效 |
| G59-7 企业 IM 告警 | 钉钉/飞书/企微接进 AlertScanner | **部分闭环** | 后端链路通；**无 UI 配置入口** → 默认不可达 |

**系统性形态**：*「后端模块 + 单测齐全，但设置页控件 / 通知回调 / 消费者缺失 → 用户不可达」*。
测试全绿是因为测试**直接构造后端对象**，绕过了 UI 断链。

---

## 3. 本轮修复（已落地，附证据）

| 编号 | 问题 | 修复 | 验证 |
|------|------|------|------|
| **G69-18y** | **★ 第二处原生崩溃源（产品缺陷）**：`LocalReposDialog` 的取消/关闭**从不调用** `ScanWorker.cancel()`（`reject()` 直接退对话框）。而 `ScanWorker` 是普通线程驱动的 QObject、且 `parent=self` —— 对话框析构后线程仍对已毁的 QObject `emit`，表现为**本地扫描点取消/关窗口偶发进程原生崩溃** | ① `ScanWorker` 新增 `stop()`（取消 + 等待收敛）+ 内部 `_emit()` 网关：停止后一律不发信号，并对 `RuntimeError`（C++ 对象已删）兜底；② 对话框重写 `reject()` 作为**所有**退出路径的收敛点（覆盖取消按钮 / Esc / 程序化关闭）+ `closeEvent` 覆盖 × 按钮 | 新增守卫 `TestG69_18ScanWorkerStopsBeforeEmit`(3)：stop() 后零信号、线程确实收敛、退出路径真的调 stop() |
| **G69-18z** | **★ CI 连续红灯根因**：`test_git_thread_exit` 用例 C 用 `time.sleep(3600)` 模拟卡死 stdout，又断言「join 超时后线程仍存活」→ `_pump` 读线程被**故意泄漏**，在整套测试剩余时间里存活并触发**原生崩溃**（本机实测 access violation；进程 `exit 139` / `0xC0000409`，unittest 来不及打印失败清单）。崩溃点在 `test_g68_settings_ui`，真凶在 `test_git_thread_exit` —— **目击者指向错误嫌疑人** | 改用 `threading.Event` 放行；收尾断言线程真能退出。**断言语义一字未改**（join 仍超时、仍出警告、判定后仍存活） | `faulthandler` 栈直指 `service.py:255 _pump` ← `test_git_thread_exit.py:55 __next__`；修复后新增「零泄漏线程」行为守卫 |
| G69-18f | CI 固定失败**无法诊断** | `ci_run_fast.py` 逐模块落痕 + 失败逐条点名 + `faulthandler` + `atexit` 兜底 | **当场见效**：首次运行即点名出 3 个失败模块 |
| G69-18a | `ReportScheduler` 零生产调用点（定时报表开关是假的） | `main_window` 新增 `_start_report_scheduler()`；`closeEvent` 收敛线程；补齐「间隔/输出目录」控件 | **真实 E2E**：勾选后生成 `Git-clone-Max-报表-20261016-183203.csv`，含真实 DB 行 |
| G69-18b | 两个死回调 `_save_g53_sched_interval` / `_save_g53_sched_dir` 从未 connect，后者引用**不存在**的控件 | 补控件 + 真实 connect；安全取控件 | `test_no_handler_referencing_nonexistent_widget` |
| G69-18c | `AlertScanner` 未传 `on_alerts` → 告警命中用户全程无感 | 新增 `alerts_fired` 信号 + `_on_alerts_fired` 槽（worker emit → GUI 线程）；**再加固**为弱引用包装 `_make_alert_emitter`，防对已销毁 QObject emit 引发原生崩溃 | `test_alerts_callback_wired` / `test_alert_slot_is_gui_safe`；实测「窗口销毁后 emit 不崩」 |
| G69-18d | 9 个字段只读不写（用户够不到）：`retry_*` / `watchdog_*` / `alert_im_*` / `g53_alerts_stale_days` / `g53_alerts_disk_min_gb` | 设置页补齐 12 个控件并全部接线 | 真实构造 MainWindow 断言控件齐全；开关真实启停调度器 |
| G69-18e | `service._env()` 从 index 0 覆盖调用方已有的 `GIT_CONFIG_*` → 静默吃掉别人的 git 配置 | 改为**追加**；COUNT 非数字时整组清理而非只 pop COUNT | 干净检出复现 → 修复后全绿 |
| G69-18g | `test_scanner_perf`（绝对 30s）/ `test_mem_baseline`（真跑引擎测内存）在正确性门禁内 | 快集设 `GCM_FAST_SUITE=1`，由这两个**用例自己** `skipIf` 跳过；模块其余用例照跑，CI 另有 `perf-baseline` job 完整跑 | ⚠️ 早期版本按**整模块**排除，连带砍掉 13 条与硬件无关的正确性用例、覆盖率 81.26%→80.76%（余量仅 0.24pp）—— 已修正为「只跳用例」 |
| G69-18h | `addCleanup` LIFO 顺序反了 | 按 LIFO 语义调整为 `shutdown → server_close → join` | 模块耗时 36.6s → 18.4s；`WinError 10038` 噪声消失 |
| G69-18i | 测试未隔离父进程 `GIT_CONFIG_*` | 抽出共用 mixin `tests/_gitcfg_isolation.py` | 污染环境下 `Ran 28 tests OK` |
| G69-18j | **修复自身引入的污染**：新守卫用例手写「pop 三项 + 有条件还原 COUNT」→ 环境变成 `COUNT=2` 但 `KEY_0` 已删，其后**所有 git 调用** `exit 128`（`missing config key GIT_CONFIG_KEY_0`），连累 `test_local_dialog` / `test_progress_ui` | 改用 `mock.patch.dict` 整组还原 | 新增守卫 `TestG69_18NoGitConfigEnvPollution`（本可提前抓到该 bug） |

**新增永久守卫**：`tests/test_g69_regressions.py`
`TestG69_18WiringClosure`(6) · `TestG69_18VerifierFindings`(5) ·
`TestG69_18NoLeakedPumpThreads`(1) · `TestG69_18NoGitConfigEnvPollution`(1)

---

## 4. 问题清单

### P0（阻塞）
| ID | 问题 | 证据 | 状态 |
|----|------|------|------|
| P0-1 | CI 三平台全红；Linux/macOS **SIGSEGV** | `gh run list`；`exit 139` | **✅ 已闭环：CI 全绿**（run 38960845721，7/7 job 通过）。根因已定并处置：Qt 控件由引用环维持 → 自动循环 GC 在 `processEvents()` 中途销毁它们 → 定时器解引用已释放对象。整轮关闭循环 GC 后本机快集 0 崩溃（见 §4.1） |
| P0-2 | 覆盖率门禁形同虚设 | 同上 | ⏳ 随 P0-1 |
| P0-3 | 台账「全部闭环」声明不成立（§2） | 独立复验 | ✅ 已修 4 类断链 |

### 4.1 残留：非确定性的 Qt 析构竞态（**未消解**，须如实标注）

**事实**：连续 5 次带诊断的 CI 运行，崩溃点**每次都在移动**——
`test_g68_settings_ui` → `test_local_dialog` → `test_g69_regressions` 第 31 项
→ 又回到 `test_g68_settings_ui`（第 8 项）。**位置漂移 = 竞态，不是某个用例的确定性 bug。**

**已定位的 C 栈（Linux 原生栈，首次拿到完整帧）**：

```
QTimerInfoList::activateTimers
  → QCoreApplication::notifyInternal2
  → PyQt6/QtWidgets.abi3.so
  → ... libpython3.14 _PyEval_EvalFrameDefault
```

即 **Qt 定时器触发时，接收对象已被释放**。

**必要条件**：某个 `QTimerEvent` 已排进事件队列，而其接收者（窗口/定时器）
在 `processEvents()` 处理它之前被销毁。测试大量构造 `MainWindow` 后仅靠 GC 回收，
且 G69-17 那组还会 `show()`（激活 QWidget 级定时器）——正是这个窗口。

**本轮已消除的 4 个**确定性**成因**（每个都有独立证据）：
1. `test_git_thread_exit` 用例 C 故意泄漏 `time.sleep(3600)` 读线程（已改 Event 放行）
2. `LocalReposDialog` 取消/关闭不调用 `ScanWorker.cancel()`（已加 `stop()` + `reject()` 收敛点）
3. `AlertScanner` 线程对可能已销毁的 QObject `emit`（已改弱引用包装）
4. `LogBuffer` 常驻 120ms 循环定时器（已改「有积压才武装的单次定时器」）

**为什么仍未消解**：剩下的不是某一个定时器，而是**「测试构造的窗口被 GC 回收的时机
不可控」**这一模式 —— 涉及数十个测试模块的 `MainWindow(...)` 调用点。
已在 `test_g69_regressions` 内示范正确收尾（`_teardown_window`：
`close()` → `deleteLater()` → 泵空事件队列，且**在对象仍被引用时**执行）。

**下一步（按性价比排序）**：
1. 把 `_teardown_window` 提升为 `tests/_qt_isolation.py` 的共用 helper，
   在所有构造 `MainWindow`/QWidget 的测试里 `addCleanup` —— 一次改造消掉整类竞态。
   判据：崩溃点若不再漂移或消失，即为闭环。
2. 辅助手段：`PYTHONMALLOC=debug` 实测会改变时序并让该次运行不崩，
   可用作二分定位的开关（但不能当修复）。
3. **不得**用 `skip`/重试来「弄绿」——那属于削弱门禁。

### P1（高）
| ID | 问题 | 状态 |
|----|------|------|
| P1-1 | `G54-4 引导清单` 零实现 | **✅ 已修（v9.4.3）** |
| P1-2 | `G53-1` 未新增 `health_card` 列、无雷达图 | **仍部分闭环**（派生视图有，列/图无） |
| P1-3 | `G58-3` 代理凭据未解析、明文落盘 | 未修（安全项） |
| P1-4 | ~~v9.1.3~v9.3.2 未打 tag~~ **不成立**：远端一直有；本地未 `fetch --tags` 导致误判，已逐 tag 校验一致 | ✅ 更正 |
| P1-5 | 孤立 zombie 字段：`shallow_default`/`auto_init_empty`/`mirror_default`/`sched_report_format`/`depth` | 未修 |

### P2（中）
| ID | 问题 | 状态 |
|----|------|------|
| P2-1 | `LocalReposDialog` 扫描无法取消（`ScanWorker.cancel()` 已实现未接线） | 未修 |
| P2-2 | 网络诊断 / 报表导出无「进行中」提示，导出同步阻塞 UI | 未修 |
| P2-3 | `main_window.py:852-853` 重复 `@staticmethod` | 未修 |
| P2-4 | `clone_timeout` / `minimize_to_tray` / `post_clone_hook` 只读不写 | 未修 |

---

## 5. 验证日志（真实命令 + 输出）

```
2026-10-16  ruff check gcm                              → All checks passed!
2026-10-16  mypy gcm                                    → Success: no issues found in 64 source files
2026-10-16  git archive HEAD → 干净检出跑快集            → FAILED (failures=3, skipped=4)  ← 抓出真缺陷
2026-10-16  gh run list --limit 12                      → 12 × completed/failure
2026-10-16  gh api .../jobs/116281573211/logs           → Segmentation fault (core dumped), exit 139
2026-10-16  unittest test_token/test_g56_api_extra/write → Ran 25 tests OK
2026-10-16  unittest 受影响 10 个模块                    → Ran 81 tests OK
2026-10-16  unittest test_g69_regressions               → Ran 28 tests OK
2026-10-16  真实构造 MainWindow（离屏）                  → 12 个新控件齐全；开关→调度器真实启停
2026-10-16  真实生成报表                                 → Git-clone-Max-报表-20261016-183203.csv（含真实 DB 行）
```

### 5.1 CI 运行器加固效果
原实现崩溃时**不打印任何失败用例名**；现版本无论怎么死都会输出
「最后进入的模块 + 已点名失败的用例 + 逐模块合计」，把不可诊断的红灯变成可定位的红灯。

---

## 6. 下一步

1. 快集全量复验（进行中）→ 覆盖率门禁
2. 并行代理剩余结论（安全契约审计）汇总
3. 压测（高并发 / 极端）
4. HTML 报告 + 测验
5. skills / SOP
6. 提交 / 推送 / Release（含补 v9.1.3~v9.3.2 tag）


---

## 7. CI 收口登记（2026-10-17）

**结果**：`completed / success`，7 个 job 全绿 ——
`test (windows)` · `test (ubuntu)` · `test (macos)` · `e2e-smoke` · `fast-smoke` · `perf-baseline` · `slow`。
run: https://github.com/lza6/Git-clone-Max/actions/runs/38960845721 （sha `a4ca7f3`）

**从 80+ 次连续红灯到全绿，共 5 类独立成因**（每一类都有实测证据，非猜测）：

| # | 成因 | 定位手段 | 修法 |
|---|------|---------|------|
| 1 | **无法诊断**：崩溃时不打印失败清单，只留点阵 + exit code | 拉全量 CI 日志 | `ci_run_fast.py` 逐模块落痕 + 失败逐条点名 + `faulthandler` |
| 2 | **测试泄漏线程**：`test_git_thread_exit` 用 `sleep(3600)` 卡死 stdout 且断言「线程仍存活」→ `_pump` 僵尸线程活过整个套件 | `faulthandler` C 栈 | 改 `threading.Event` 放行 + 收尾断言线程真退出 |
| 3 | **Qt 控件由引用环维持**：`del w` 不被引用计数回收，只有 `gc.collect()` 才回收（实测 369 个对象）→ 自动循环 GC 在 `processEvents()` 中途销毁控件/定时器 → `activateTimers` 解引用已释放对象 | 对照实验（禁用 GC 后 0 崩溃）+ `del`/`gc.collect()` 探针 | 整轮关闭循环 GC，结束回收一次 |
| 4 | **MCP 服务在非 UTF-8 控制台崩溃**：`ensure_ascii=False` 写中文到 cp1252 stdout → `UnicodeEncodeError` 打断 JSON-RPC 通道 | CI 点名 + 本地 cp1252 复现 | `_force_utf8_streams()`（与 `gcm/cli.py` 同款守卫） |
| 5 | **CI 自身**：覆盖率摘要步骤（信息性）把中文写进 cp1252 → 弄红 job | CI 日志尾部 | 脚本内 reconfigure + `continue-on-error: true` |

**归因纪律（本轮最重要的方法论收获）**：
崩溃点**随机漂移**就是竞态的信号。前几轮按「某个用例有 bug」去修全部白费 ——
必须按「销毁时机不可控」这类**系统性成因**处理。
另外：**涉及远端状态的结论必须先 `git fetch` 再下**
（「tag 断档」就是未 fetch 导致的误判，已更正）。


---

## 8. 第三轮收口（v9.4.3 · 2026-10-17）

| 任务 | 状态 | 证据 |
|------|------|------|
| **P1 根治 CI 崩溃（引用环）** | **部分闭环**：`MainWindow` 的环已打断（`del w` 按引用计数释放，守卫 `TestG69_18NoMainWindowRefCycle`）；**其它控件仍有环**，runner 保留整轮关 GC 作安全网并标注待改 | 恢复正常 GC 后套件仍在 `test_g68_settings_ui` 段错误 |
| **P0-2 Webhook 幂等/审计/重试** | **已闭环** | 600s 去重 / `<data>/notify.log` 审计（只记 `scheme://host`）/ 重试 1 次 / 设置页入口 |
| **G54-4 引导清单** | **已闭环** | `gcm/ui/checklist.py` + 首启/设置页入口 + 8 条测试 |
| **压测（并发幂等）** | **抓到并修复真 bug** | 40 并发同 payload：13 次 → **1 次**；回归 `TestConcurrentIdempotency`(2) |

**发布**：Release **v9.4.3**（Latest）双产物 + 服务端 sha256 校验一致；
健康检查 `version=9.4.3 / started_ok=true / artifacts_exist=true`。

**验证**：`Ran 1110 · failures=0 errors=0 skipped=3 · 原生崩溃 0 · 覆盖 81%`；
ruff `All checks passed`；mypy `Success`（66 文件）；CI 7/7 全绿。

**仍未闭环（诚实清单）**：其它控件的引用环（已用探针否定「跨模块残留」，但正常 GC 下仍偶发段错误，故保留安全网）、
`G53-1` 雷达图/`health_card` 列、`G58-1/2` 限流与镜像测速、
老 tag 版本滞后 1 个 patch（历史遗留）。


---

## 9. 第五轮（v9.4.4 审查整改 · 2026-10-17）

独立对抗式审查对 v9.4.2–v9.4.4 给出 **Request Changes**，5 个阻塞项。逐条处置：

| 编号 | 审查发现 | 处置 |
|------|---------|------|
| C1 | **`_layout_timer` 用 lambda 捕获 self** —— 一旦 `show()`，`resizeEvent` 创建该定时器，引用环重新接上；此前「根因已消除」的结论对**生产路径不成立**，守卫只覆盖从不 `show()` 的窗口（假绿） | 改绑定方法 `_on_layout_tick`。**实测：`show()` 后 `del w` 仍按引用计数释放** |
| C2 | `count_repos` 方法**不存在**（只有 `count()`），`hasattr` 把拼错静默降级 → 引导清单「已导入仓库」项**永远不可能完成** | 改 `db.count()`；实测导入 1 个仓库后该项为 True |
| C3 | `test_themes` 断言旧文案 → 快集确定红 | 已随 v9.4.4 同步更新（审查看到的是修复前状态）；现 `Ran 9 OK` |
| C4 | 引导清单**每次启动都弹模态**（无「已提示」判断），且向导的嵌套事件循环会让清单叠在上面 | 新增 `settings.checklist_prompted`；改为**向导函数返回后**触发 |
| C5 | 僵尸字段守卫用文本匹配，**被自己的 changelog 喂成假绿** | 改 **AST 扫描**（只认 Attribute/Name/getattr 字符串），注释与 changelog 不算引用；并加反向自检用例 |

**Required Changes 一并处理**：`parse_urls` 跳过 `#` 注释（与校验条语义统一）、
校验条与清单说明文字改用 `theme.current_palette()` 语义色（原写死 Deep 主题 hex，
Light 下对比度不足）、校验条强制 `PlainText`（用户原文含 `<>` 不被当富文本）、
清单文案走 `tr()`（中英键已补齐）、`notify` 兜底改为写 `event=error` 审计而非静默、
幂等指纹**纳入 URL**（换服务不会被误去重）、`ManagePanel(owner=None)` 注释与实现对齐。

**审查实测否定、无需再排查的怀疑**（记录下来避免重复劳动）：
`Path("notify.log").with_suffix(".log.1")` 在 3.10/3.14 均正确；
`ClipboardWatcher` 传普通函数仍走强引用分支且正常回调；
旧 settings.json 中的三个已删字段被 `_DEFAULTS` 正确过滤；
600ms 定时器在窗口销毁后触发不会崩。


---

## 10. 计划文档二次审计与执行骨架（2026-10-17 · v9.4.6 之后）

### 10.1 本轮契约

| # | 需求 | 状态 | 证据 |
|---|------|------|------|
| D1 | 创建 `policy.md` | ✅ | 仓库根 |
| D2 | 创建 `test_ledger.md`（接缝注册表 + 测试状态） | ✅ | 仓库根，S01–S14 |
| D3 | 创建 `evidence/` 逐节点证据 | ✅ | `evidence/N1..N3` + README |
| D4 | 计划文档逐条审计 + 归档 + 删除 | ✅ | `docs/archive/plans/20261017/archive-manifest.md` |
| D5 | 新增 `plan-doc-audit.md` 技能 | ✅ | `.agent/skills/plan-doc-audit.md` |
| D6 | 修正与代码不符的文档陈述 | ✅ | 见 `evidence/N2-docs-staleness-fix.json` |
| D7 | 定向守卫 + 全量快集 | ✅ | 22 守卫 + 快集结果见下 |

### 10.2 审计结论（只列可判定的条目）

| 分类 | 条目 |
|------|------|
| 已闭环 | G52-1 · G52-3 · G52-5 · G52-6 · G53-2 · G53-5 · G54-4 · G56-2 · G56-4 · G58-3 · G58-5 · G59-7 |
| 未闭环（需立项） | G53-1（`health_card` 列 / 雷达图，需 DB 迁移） · G58-2（镜像测速择优） |
| 不适用（已撤销） | G58-1（统一限流模块因零生产调用被删） |

**删除清单**：`计划书/README.md`（墓碑）→ 归档 → 删除；`计划书/` 空目录已移除。

**未删除及原因**：`docs/RELEASE_CHECKLIST.md`（活 SOP）· `docs/architecture.md` /
`data-model.md` / `agent-contract.md` / `code-gems.md` / ADR（事实源）·
`docs/验证记录-*.md` / `docs/reports/*` / `docs/audit-v8.2.0-*`（历史证据）·
`docs/perf-observability.md`（含可选增强）· `docs/product-brief.md`（产品定位）·
`docs/慢测定位.md`（耗时事实源）。

### 10.3 文档过期修正（以代码为准）

| 文档 | 过期陈述 | 现状 |
|------|---------|------|
| `docs/product-brief.md` | Webhook 幂等/审计未实现 | v9.4.2–v9.4.6 已实现 |
| `docs/慢测定位.md` | 测试基础设施缺陷尚未闭环 | `_can_show_modal` + 引用环修复 |
| `CLAUDE.md` §6 | G54-4 无组件 | v9.4.3 已实现 |
| `CLAUDE.md` §6 | `settings.proxy` 明文落盘 | 已加密（`_encrypt_token`） |
| `CLAUDE.md` §6 | G58-1 未实现 = 缺陷 | 模块已撤销，属不适用 |

### 10.4 验证

```
2026-10-17  ruff check gcm                                    → All checks passed!
2026-10-17  mypy gcm                                          → Success（66 文件）
2026-10-17  unittest 文档新鲜度 + 版本单源 + 打包同步           → Ran 22 tests OK
2026-10-18  全量快集（coverage，v9.4.7）                       → Ran 1143 · failures=0 · errors=0 · skipped=3 · 原生崩溃 0 · 覆盖 82%
```

### 10.5 仍未闭环

`G53-1` · `G58-2` · 两个跨进程测试高负载偶发 · CI 未锁 PyQt6。均登记在
`.agent/memory/known-issues.md` 与 `CLAUDE.md` §6。


---

## 11. 盲点扫描与接线（G69-20 · v9.4.7 · 2026-10-17/18）

独立只读代理（AST 符号使用统计 + grep，排除 tests 与 Qt 框架回调）报告 15 项
「定义了但用户不可达」。**逐条核实后修 8 项、登记 7 项**。

### 11.1 已修（每项都有守卫，`tests/test_g69_wiring_gaps.py` 13 用例）

| # | 缺陷 | 修复 | 证据 |
|---|------|------|------|
| 1 | 管理页搜索：`filter_rows` 无控件，`_vis` 写了也没人读 | 搜索框 + `setRowHidden` + 刷新后保持过滤 | 2 用例：隐藏行数、重载后仍生效 |
| 2 | 过期高亮：`is_stale` 无颜色角色 | `BackgroundRole`/`ForegroundRole` + tooltip；阈值随设置 | 2 用例 |
| 3 | 日志关键字高亮：`make_highlighter` 零调用 | 安装到 `log_view` | 1 用例 |
| 4 | `crypto_warning()` 零调用 | 启动写 WARN 日志 | 1 用例（mock 后端文案） |
| 5 | 收藏/黑名单：无 UI 写入点 | 管理页右键两项（批量切换） | 2 用例（写入 + 可逆） |
| 6 | Star 导入：`fetch_starred` 无 UI | 输入区按钮 + worker 线程 + 去重回填 | 2 用例（去重、错误分支） |
| 7 | `CloneWorker.on_line` 只存不用 | `run()` 接到 service，按仓库归属 | 1 用例（stub service） |
| 8 | AlertScanner 配置构造期固化 | `reconfigure()` + 设置保存即推送 | 1 用例 |

### 11.2 仍未修（已登记 `.agent/memory/known-issues.md`）

`post_clone_hook` 无 UI · `rowsChanged/visChanged` 无 connect（无功能影响）·
`ScanWorker.progress` 未接 · `check_latest_with_sha` 无调用 ·
目录隐私检测/迁移无调用 · 5 个只读不写设置字段 · `quick_repos` 无编辑入口与 `depth` 僵尸字段。

### 11.3 发布

版本 `9.4.7`：`__changelog__` 已登记；`pyproject.toml` / `Git-clone-Max.spec` /
`packaging/installer.iss` / `docs/agent-contract.md` 已同步；版本单源守卫绿。

### 11.4 提交 / CI / 发布

| 项 | 结果 |
|----|------|
| 提交 | `de4a0d3`（main） |
| CI | run 39053525582 · **7/7 job 绿**（win/ubuntu/macos + e2e + fast + perf + slow） |
| 打包 | PyInstaller 成功；便携 zip 286 条目 / 78,715,651 字节，`--check` 未过期 |
| 健康检查 | `version=9.4.7` · `started_ok=true` · `artifacts_exist=true` · `repos_db_created=true` |
| Release | v9.4.7 双产物 + 服务端 sha256 一致 → `docs/验证记录-2026-10-17.md` §12.5 |

> 构建时踩到已知坑：`dist/Git-clone-Max.exe` 被 **6 个健康检查残留实例**占用
> （`PermissionError [WinError 5]`）。按 `docs/RELEASE_CHECKLIST.md` 的说明先
> `taskkill /IM Git-clone-Max.exe /F` 再重建即可。这条已在该清单里写明，本次是复现而非新问题。


---

## 12. 镜像多备援（G58-2 · v9.5.0 · 2026-10-18）

用户需求原话：*「最好多备用几个国内镜像，默认就用国内镜像，如果国内镜像没这个仓库地址就用源仓库地址重试」*。

### 12.1 做了什么

| 层 | 变化 |
|----|------|
| 纯逻辑 | 新增 `gcm/app/mirrors.py`：前缀归一（旧的单值 + 新的多值）、用户配置优先、内置 4 个实测镜像、凭据门禁 |
| 探测 | 新增 `gcm/app/mirror_probe.py`：`git ls-remote --exit-code` 短超时 + 10 分钟缓存（**HTTP HEAD 不可信**，见验证记录 §13.1） |
| 克隆 | 先逐个探测，取第一个可用镜像；镜像没有该仓库/克隆失败 → 回退源站重试 |
| 更新 | fetch 失败 → 在镜像/源站之间切换，成功则把 origin 留在可用地址 |
| 安全 | **有令牌/主机凭据时绝不用镜像**；SSH/本地路径不拼接 |
| UI | 一个 host 可配多个镜像；「使用内置国内镜像」「镜像失败自动回退源站」开关；「测速并排序」按钮（worker 线程） |

### 12.2 真实 E2E（本机）

```
镜像克隆              → SUCCESS 6.3s（gh.llkk.cc，2940ms 探测）
镜像不可达→源站       → SUCCESS 6.7s
镜像克隆后增量更新     → SUCCESS 4.8s（已是最新）
更新时镜像失效→切换    → SUCCESS 8.7s（set-url 到 gh.llkk.cc）
```

### 12.3 验证

`ruff` All checks passed · `mypy` Success（68 文件）· 新增
`tests/test_g58_mirror_failover.py`(34) · 受影响回归 43 用例 OK ·
全量快集见 `evidence/N9-final-fast-suite.json`。

### 12.4 提交 / CI / 发布

| 项 | 结果 |
|----|------|
| 提交 | `c975977`（main） |
| CI | run 39068317259 · **7/7 job 绿** |
| 打包 | PyInstaller 成功；便携 zip 290 条目 / 78,804,523 字节 |
| 健康检查 | `version=9.5.0` · `started_ok=true` · `artifacts_exist=true` |
| Release | v9.5.0 双产物 + 服务端 sha256 一致 |


---

## 13. 独立审查整改 + G53-1 雷达图（G69-21 · v9.6.0 · 2026-10-18）

### 13.1 输入

两个并行只读代理：
- **盲点扫描**：12 项「用户不可达 / 逻辑断链」（M1–M12）。
- **对抗式代码审查**：2 个阻塞 + 8 项 Required（独立复现了凭据泄漏与按钮 TypeError）。

主控逐条复核并修复；**没有直接采信代理结论**。

### 13.2 阻塞项（均已修复 + 守卫）

| # | 问题 | 修法 | 证据 |
|---|------|------|------|
| 1 | 令牌随 origin 泄漏给镜像（CRITICAL） | 认证头 URL 作用域；镜像克隆后 origin 回源站；旧仓库更新时规范化；全局/系统 `http.*extraHeader` 禁用镜像 | 镜像测试：origin 恢复 / 规范化 / 全局凭据门禁 |
| 2 | 「测速并排序」必定 TypeError | 槽签名 `checked=False` | **真实按钮点击**测试 |
| 3 | 测速写回覆盖其它 host / 固化预设 | 只写自定义、保留其它 host、尊重开关 | 2 条 UI 测试 |
| 4 | 更新回退最坏 5×(3×300s) | 先短超时探测 + `retries=0` | 回退测试 |
| 5 | `--mirror` bare 无回退 | 补回退 + origin 恢复 | 代码路径 |
| 6 | 探测不走应用内代理 | `env` 透传（Service + UI） | 探测签名 |
| 7 | URL 内嵌凭据绕过门禁 | `build_mirror_urls` 拒绝 userinfo | 专项测试 |
| 8 | `mirror_fallback` 语义与文案不符 | 改为「失败自动重试（含回退源站）」 | i18n/UI |
| 9 | CLI 默认走镜像且无开关 | `--no-mirror` | CLI help 测试 |
| 10 | Worker 重建丢凭据/LFS/镜像 | 全参数透传 | `TestWorkerRebuildKeepsConfig` |

### 13.3 额外修复（审查/验证中发现）

- **`engine.shutdown()` 退出崩溃**：drain 超时后线程池仍在跑 → 解释器退出阶段
  Windows `0xC0000409`（实测约 2/5 概率）。现在先断开 worker 信号，再
  `pool.waitForDone(8000)`；`TestShutdownClearsInProgress` 连跑 6 次全绿。
- **两个 flaky 测试**：`TestSingleInstanceCrossProcess` 用固定 sleep 猜「p1 已持锁」，
  改为读 p1 的 `acquired: True`；shutdown 测试注入 1.2s 延迟保证一定处于「进行中」。
  断言本身未改。
- **CLI 等待机制**：宿主进程重建过 QApplication 时 `QEventLoop.exec()` 会立即返回
  （实测 0.0s、结果为空）→ CLI 误报成功。改为轮询 `processEvents`。
- **G53-1 雷达图**：`gcm/ui/radar_chart.py`（纯 Qt，6 维，三主题），统计中心挂载。
  `health_card` 列刻意不加：派生视图是唯一事实源。

### 13.4 验证

`ruff` All checks passed · `mypy` Success（69 文件）·
镜像 `test_g58_mirror_failover`(43) · 雷达 `test_g53_radar`(9) ·
接线 `test_g69_wiring_gaps`(22) · 宽回归 168 用例 OK ·
全量快集见 `evidence/N11-final-fast-suite.json`。

### 13.5 复验轮（Verifier）

独立只读复验：4 项关键修复 **全部 PASS**（凭据泄漏、测速按钮、更新回退、退出崩溃），
同时提出 7 项新发现（3 HIGH），已在本轮**再次修复**：

| 级别 | 发现 | 修法 |
|------|------|------|
| HIGH | 测速路径绕过全局认证头守卫 | 测速前调用 `global_auth_header_configured()` |
| HIGH | 作用域丢端口（自建 `:8443` token 不发） | 用源 URL 的 `netloc` 构造作用域 |
| HIGH | 旧 `gcm.mirror` hint 越过 `mirror_use_presets` | hint 必须仍在当前候选集合 |
| MEDIUM | 关闭预设后旧 origin 不自愈 | 任意 `<前缀>/<源站>` 都恢复源站 |
| MEDIUM | 安全守卫断言被弱化 | 恢复精确断言（`http.https://` + `/.extraHeader`） |
| MEDIUM | hint 预取不探测，死镜像白等 180s | `probe_state` 未知时短超时探测 |
| LOW | 注释/日志漂移 | docstring 与超时文案修正 |

额外：镜像命令统一加 `-c http.extraHeader=`（实测可清除仓库级/全局认证头）；
`_mirror_env` 清理继承的 `GIT_CONFIG_*`。

### 13.6 提交 / CI / 发布

| 项 | 结果 |
|----|------|
| 提交 | `131e810`（main） |
| CI | run 39093537396 · 7/7 job 绿（首次锁定 PyQt6/coverage） |
| 打包 | PyInstaller 成功；zip 292 条目 / 78,890,140 字节 |
| 健康检查 | `version=9.6.0` · `started_ok=true` · `artifacts_exist=true` |
| Release | v9.6.0 双产物 + 服务端 sha256 一致 |


---

## 14. 第三轮审查整改（G69-22 · v9.6.1 · 2026-10-18）

第三个独立审查代理针对 v9.6.0 的修复本身做对抗式审查，结论 **Request Changes**：
7 项 Required + 10 项建议。已全部处理。

### 14.1 Required

| 级别 | 问题 | 修法 |
|------|------|------|
| HIGH | treeless/depth1 降级克隆与子模块更新漏清 `http.extraHeader`（同类残留泄漏面） | 所有镜像命令统一 `-c http.extraHeader=` + `_mirror_env()`；origin 在子模块更新**之前**恢复源站 |
| HIGH | 环境清理按前缀误删 `GIT_CONFIG_GLOBAL/SYSTEM/NOSYSTEM`（企业代理/CA 丢失） | 只删 `GIT_CONFIG_COUNT` 与 `KEY_n/VALUE_n` |
| MED-HIGH | 仓库 404 被当成「镜像挂了」，前缀被拉黑 120s | 只有网络/超时类失败才更新前缀健康 |
| MED | 预检失败 + 镜像不可用仍等 retries×600s | 快速失败并明确提示 |
| MED | 关窗双重 drain，最坏 20s | 移除 closeEvent 的额外 drain（shutdown 统一收敛） |
| MED | bare 回退零测试 | 新增 3 条 |
| LOW-MED | bare 分支无条件改写 origin | 与普通分支一致的 origin 同源守卫 |

### 14.2 建议中的高价值项

作用域不再用 netloc（避免 `user:pass` 进入环境变量名）；全局认证头探测加 single-flight；
单分支模式跳过镜像快速 fetch；测速只有预设可达时不删用户自定义镜像；
雷达图文案走 `tr()`、六维全 0 不再显示成空态；`discard/shutdown` 共用信号断开。

### 14.3 验证

`ruff` All passed · `mypy` Success（69）· 镜像 `test_g58_mirror_failover`(56) ·
雷达 9 · 宽回归 89 OK · **全量快集 Ran 1211 · 0 失败 · 0 崩溃 · 覆盖 82%**。

### 14.4 提交 / CI / 发布

| 项 | 结果 |
|----|------|
| 提交 | `c8ee65f`（main） |
| CI | run 39119023991 · 7/7 job 绿 |
| 打包 | PyInstaller 成功；zip 292 条目 / 78,906,579 字节 |
| 健康检查 | `version=9.6.1` · `started_ok=true` · `artifacts_exist=true` |
| Release | v9.6.1 双产物 + 服务端 sha256 一致 |


---

## 15. 技能检查记录（2026-10-18）

| 检查项 | 结果 |
|--------|------|
| `.agent/skills/` | 10 个流程技能（加功能/加 API/发版/回滚/验证矩阵/审查/审计/并发/计划文档清理） |
| `.agent/rules.md` + `policy.md` | 存在且已生效（证据门禁、T0–T3、四档置信标签） |
| `test_ledger.md` / `workflow_status.md` / `evidence/` | 存在并持续更新 |
| `AGENTS.md` / `docs/skills/` | 不存在（项目以 `CLAUDE.md` + `.agent/` 为准） |
| Spec Kit / addyosmani agent-skills | 未安装；本轮任务使用项目内技能与 Agent 子代理即可完成，未引入新依赖 |
| 外部深度提示词库 | `C:\Users\...\Desktop\编码规则\` 已按需参考（代码审查/全栈契约） |

**路由结论**：项目内专用脚本与技能 > 项目测试工具（unittest/coverage/ruff/mypy）>
项目 CLI/MCP > 通用工具。本轮无需联网安装任何技能。

---

## 16. G70 UI/UX 全面升级（2026-10-19 · v9.7.0）

### 16.1 任务契约

| 项 | 内容 |
|----|------|
| 目标 | 响应式窗口 / 性能 / 设计系统 / 无障碍 / 本地 API 五阶段闭环，不换技术栈、不做大幅重构 |
| 验收 | ruff 0 · mypy Success · 定向回归全绿 · 快集全绿 · 真实 git E2E PASS · 500 行基准达标 · 文档/版本/证据同步 |
| 授权 | 本地修改、测试、构建、提交推送、发布（用户本轮明确授权；禁止 force push） |

### 16.2 子任务状态

| ID | 子任务 | 状态 | 证据 |
|----|--------|------|------|
| G70-1 | 语义 token / 对比度 / QSS 拼写 / 缓存 / 字号派生 | Done | `test_g70_ui_upgrade` · `test_g46_theme` |
| G70-2 | 窗口断点 / splitter / 对话框夹取 / 页签记忆 | Done | `layout.py` · `test_g70_ui_upgrade` |
| G70-3 | 进度 delegate / 合批 / 动效限流 / 延迟加载 | Done | `scripts/bench_g70_ui.py` |
| G70-4 | 无障碍名称 / 焦点环 / 空态 | Done | `test_g70_ui_upgrade` |
| G70-5 | 本地 API 分页 / 只读连接 / 错误脱敏 | Done | `test_g70_ui_upgrade` · 并发压测 |
| G70-6 | 版本 / README / 契约 / 验证记录 / evidence | Done | 本文件 · `docs/验证记录-2026-10-19.md` |
| G70-7 | 独立审查（代码 + 安全） | Done | 代码审查第 2 轮 **Approve**（1 阻塞 + 3 Required 全部关闭）；安全审查 PASS（无 CRITICAL/HIGH） |
| G70-8 | 快集 / 构建 / 发布 | 待执行 | §16.4 |

### 16.3 关键实测

- 500 行进度子控件：500 → **0**；500 次更新：5431ms → **5.3ms**。
- API 30 并发 p50：6612ms → **1692ms**（SQL 分页 + 只读连接）。
- 主按钮对比度：deep 2.53 → **7.49:1**；Nord 弱文本 2.82 → **4.80:1**。
- 真实 git E2E：6/6 PASS。

### 16.4 验证 / 构建 / 发布

| 项 | 结果 |
|----|------|
| 快集全量 | `Ran 1262 · failures=0 · errors=0 · skipped=3 · 覆盖 83%`（门禁 81） |
| PyInstaller | EXIT=0 · exe 40,864,518 B · sha256 `394324f6…` |
| portable zip | 296 条目 / 78,992,487 B · `--check` 未过期 |
| 健康检查 | 版本/产物校验通过；打包 GUI 初始化在本会话无法完成（v9.6.1 对照同现象，判定环境限制）；源码 GUI 与打包 CLI 均正常 |
| 提交 | `6518ad6`（main） |
| CI | run `39165009718` · **7/7 job success** |
| Release | `v9.7.0` 双产物 · 服务端 sha256 一致 |
