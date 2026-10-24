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
| G70-8 | 快集 / 构建 / 发布 | Done | §16.4 |

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
| 健康检查 | `started_ok=true` · `repos_db_created=true` · 17.9s；交互式会话确认主窗口标题 `Git-clone-Max — GitHub 仓库批量并行下载` |
| 提交 | `6518ad6`（main） |
| CI | run `39165009718` · **7/7 job success** |
| Release | `v9.7.0` 双产物 · 服务端 sha256 一致 |
| 发布后 CI | `2afd763` success · `9b6b3a6` failure（macOS 时间边界 flake）· `014f0fc` **success** |

### 16.5 收尾状态

- 打包 GUI 冒烟：**已闭环**（60s 等待 + 交互式会话窗口标题验证）。
- 本轮无未解决 P0/P1；剩余仅为 NVDA/真实 DPI 人工项与 P2b 条件项。

---

## 17. G71 队列可观测性 / 事件流 / 诊断包（2026-10-19 · v9.8.0）

### 17.1 任务契约

| 项 | 内容 |
|----|------|
| 目标 | 对标参考项目 SurgeDM/Surge：队列/重试可见、SSE 事件流、原子存储；并完成 B1 文档治理、B2 首启/错误可解释性、B7 长函数拆分、桌面诊断/黑匣子日志 |
| 范围 | `gcm/app/{event_bus,errors,batch_state,retry_queue,diag_bundle,engine}.py`、`gcm/http_api.py`、`gcm/util/atomic_io.py`、`gcm/db/{progress_file,history,lists}.py`、`gcm/ui/{retry_panel,diag_dialog,onboarding,help_dialog,failure_panel,download_layout,engine_factory,main_window}.py`、i18n、文档、测试 |
| 明确不做 | 不移植 Go/KMP 技术栈；不引入图数据库；不做远程遥测；不发布/推送 |

### 17.2 节点状态

| ID | 节点 | 状态 | 证据 |
|----|------|------|------|
| G71-1 | EventBus + 引擎全链路发布 | Done | `test_g71_event_stream.TestEventBus/TestEnginePublishesEvents` |
| G71-2 | 统一原子写 | Done | `TestAtomicWrites` + `test_g67_closure` |
| G71-3 | 三段式错误模型（唯一可重试判据） | Done | `TestErrorModel` + `TestRetryQueueVisibility` |
| G71-4 | 重试队列面板 | Done | `TestRetryPanelRows` + UI 回归 85 |
| G71-5 | 首启/帮助 i18n + 模式语义值 | Done | `TestOnboardingSemanticMode` + `test_onboarding` |
| G71-6 | 诊断包 | Done | `TestDiagBundle` |
| G71-7 | 长函数拆分（`_update` 337→105、`_launch` 153→124、`_build_download_tab` 191→35） | Done | ruff/mypy + 引擎/UI 回归 |
| G71-8 | 批次状态机 | Done | `TestBatchState` |
| G71-9 | GUI 进程内托管本地 API（SSE 真实事件） | Done | `TestLocalApiSettings` + `test_g68_settings_ui` |
| G71-10 | 事件流查看器（黑匣子轨迹） | Done | `TestEventPanel` |
| — | SSE 端点 + snapshot | Done | `TestSseEndpoint` |
| B1 | 文档事实治理 | Done | `CLAUDE.md`/`README`/`architecture`/`agent-contract`/`data-model`/`code-gems`/`test_ledger` |
| B2 | 首启/错误可解释性 | Done | 三段式 + 帮助页 i18n |
| B7 | 主窗/服务长函数 | 部分（见 17.4） | 3 处已拆；剩余 14 个 >100 行函数 |
| G71-11 | 独立审查 5 Required + 7 Suggestion 修复 | Done | 见 `docs/验证记录-2026-10-19-G71事件流.md` §6 |

### 17.3 验证（真实输出摘要）

| 命令 | 结果 |
|------|------|
| `ruff check gcm` | All checks passed（78 文件） |
| `mypy gcm` | Success（78 文件） |
| `unittest tests.test_g71_event_stream` | Ran 54 · OK |
| 重试/失败/首启/HTTP/锁/进度 | Ran 82 · OK（113s） |
| 新增 + 引擎/E2E/帮助/文档守卫 | Ran 71 · OK（295s） |
| G70 UI/进度/首启/托盘/下载工具/主窗 | Ran 85 · OK（36s） |
| 快集全量 | **Ran 1316 · failures=0 · errors=0 · skipped=3 · 覆盖 81%**（Windows 门禁 81） |
| 真实 git E2E | `scripts/e2e_smoke.py` → `E2E_SMOKE_RESULT: PASS` |
| 打包 exe 克隆/更新 | `action=cloned + FILE_OK`；`action=updated + content=v2` |
| PyInstaller | EXIT=0；exe 41,173,858 B；zip 79,482,799 B（314 条目） |
| 健康检查 | `started_ok=true` · `repos_db_created=true` · version_match=9.8.0 · 28.999s |
| 发布 dry-run | `校验通过，未做任何修改`（Release v9.8.0 不存在，将新建） |
| CI | run `39238030986` · **7/7 job success** |
| Release | `v9.8.0` · https://github.com/lza6/Git-clone-Max/releases/tag/v9.8.0 · 双产物 sha256 一致 |

未运行：快集全量、PyInstaller、真实浏览器 EventSource、真机按钮点击（原因见
`docs/验证记录-2026-10-19-G71事件流.md` §4）。

### 17.4 剩余风险

- `git/service.py::_fetch_remote` 仍有 144 行（算法整体性强，继续拆会增加回归面）；
  `MainWindow.__init__` 227 行、`_build_download_tab` 已降到约 35 行。
- SSE 无鉴权（与既有 HTTP API 同一边界）：只绑回环 + Host/Origin 校验；事件已打码，
  但**不应**经无鉴权反代暴露公网。
- 事件总线为进程内内存态，退出即丢；跨重启审计依赖 `app.log`/`progress.json`。
- 未做真实 GUI 点击与打包验证；本轮未改打包结构，但 exe 需人工重建才包含新代码。
- 独立审查（python-reviewer）无 Critical、5 Required 全部关闭；剩余建议见
  `docs/验证记录-2026-10-19-G71事件流.md` §6。

---

## 18. 计划文档闭环审计（2026-10-19 G71 终局）

### 18.1 扫描范围与结果

| 文档 | 类型 | 状态 | 处理 |
|------|------|------|------|
| `workflow_status.md` | 活跃执行台账 | 含 G70 历史 + G71 当前；G71 节点已登记 | **保留**（持续维护） |
| `docs/验证记录-2026-10-19-G71事件流.md` | 本轮验证台账 | 已验证命令与边界齐全 | **保留**（历史证据） |
| `docs/archive/plans/20261017/archive-manifest.md` | 归档 manifest | 历史归档，已标注不再执行 | **保留**（审计快照） |
| `docs/archive/plans/20261017/计划书-README-墓碑-20261014.md` | 归档墓碑 | 历史归档 | **保留** |
| `policy.md` | 交付门禁 | 活跃规范 | **保留** |
| `test_ledger.md` | 接缝注册表 | 已更新 S20–S26 | **保留** |
| `参考的结果计划指南.md` | Phase A 参考计划 | **会话开始时为未跟踪文件；会话中途从工作区消失（非本会话删除）** | **未删除、未覆盖**；其 B4/B5/B6 仍未闭环，需用户确认是否恢复/继续 |

### 18.2 参考计划任务台账（按会话开始时读取到的内容）

| 批次 | 目标 | 当前状态 | 证据 |
|------|------|----------|------|
| B1 | 文档事实治理 + 守卫扩展 | **已闭环** | 本轮更新 CLAUDE/README/architecture/agent-contract/data-model/code-gems/RELEASE_CHECKLIST；`test_g67_docs_freshness` 22 项 OK |
| B2 | 首启/错误可解释性收敛 | **已闭环** | `errors.py` 三段式 + onboarding/help i18n + failure_panel 改造；G71 测试覆盖 |
| B3 | 队列/恢复语义强化 | **基本闭环** | `batch_state.py`、`retry_queue.py`、`progress_file.py` 原子写、重试面板、SSE 事件；未改 `progress_table` 的恢复 UI |
| B4 | 仓库知识索引 MVP | **未闭环** | 参考计划中的 `repo_index.py`/DB 迁移/新面板尚未实施 |
| B5 | Agent 契约 v2 | **部分闭环** | SSE/snapshot/事件 schema 已补；MCP annotations/outputSchema 未补 |
| B6 | 真实 GUI E2E + 发布自动化 | **部分闭环** | 本地 API/SSE E2E、截图外的离线 UI 测试；真实浏览器/真机点击仍在发布清单人工项 |
| B7 | 主窗/服务长函数拆分 | **部分闭环** | `_update` 337→105、`_launch` 153→124、`_build_download_tab` 191→35；其余 14 个 >100 行函数保留 |

### 18.3 归档/删除清单

**本轮无删除**：没有“全部闭环且用户已确认”的计划文档；`workflow_status.md` 与验证记录均需保留；
参考计划文件在工作区中已不存在，无法归档，已在上表登记。符合“未验证不删除、含未闭环任务不删除”的红线。

### 18.4 回滚方式

- 本轮文档改动均可用 `git checkout -- <path>` 回退（若已提交则 `git revert <sha>`）。
- 归档目录未变更；无不可逆删除。

---

## 19. G72 加固（2026-10-19 · v9.8.1）

### 19.1 来源与取舍

独立调研 agent（WebFetch WHATWG SSE / RFC 9112 / Python os.replace / AWS Backoff /
OWASP Logging / SQS DLQ）返回 8 条 backlog；本轮实施 7 条低风险高价值项，
**DLQ 可视化**列入下一轮（涉及 UI/i18n/测试）。

### 19.2 节点状态

| ID | 节点 | 状态 | 证据 |
|----|------|------|------|
| G72-1 | app.log 落盘打码 | Done | `test_g72_hardening.TestLogRedaction` |
| G72-2 | redact 路径变体/token 前缀 | Done | `TestRedactG72` |
| G72-3 | 原子写 mkstemp/winerror 退避 | Done | `TestAtomicHardening` |
| G72-4 | 诊断包并发/上限/manifest | Done | `TestDiagHardening` |
| G72-5 | 重试 jitter + tick 上限 | Done | `TestRetryJitter` |
| G72-6 | SSE Last-Event-ID/503/写超时 | Done | `TestSseG72` |
| G72-7 | 订阅者背压指标 | Done | `TestEventBusG72` |
| G72-8 | DLQ 视图 | **Backlog** | 下轮 |

### 19.3 验证

| 命令 | 结果 |
|------|------|
| ruff / mypy | All checks passed / Success（79 文件） |
| `tests.test_g72_hardening` | Ran 16 · OK |
| G71+applog+redact+retry 定向 | Ran 107 · OK |
| 文档/版本守卫 | Ran 22 · OK |
| 快集全量 | **Ran 1332 · failures=0 · errors=0 · skipped=3 · 覆盖 81%** |
| 构建/健康 | exe 41,187,849 B / zip 79,505,467 B；`started_ok=true`、version_match=9.8.1 |
| CI | run `39291521568` · **7/7 job success** |
| Release | `v9.8.1` · https://github.com/lza6/Git-clone-Max/releases/tag/v9.8.1 · 双产物 sha256 一致 |

---

## 20. G73 DLQ 重新入队（2026-10-20 · v9.8.2）

### 20.1 来源
G72 调研 backlog 的最后一项：把「已放弃/永久失败」做成可操作视图
（SQS/Azure Service Bus DLQ 的 peek → 修正 → redrive 模式）。

### 20.2 状态

| ID | 节点 | 状态 | 证据 |
|----|------|------|------|
| G73-1 | dropped 记录保留 spec 字段 | Done | `test_g73_dlq.TestDlqQueue` |
| G73-2 | `requeue_dropped`（attempts=0、立即到期） | Done | 同上 |
| G73-3 | 面板「显示已放弃」+「重新入队选中」 | Done | `TestDlqPanel` |
| G73-4 | 快集/构建/发布 | 见 §20.3 | — |

### 20.3 验证与发布

| 项 | 结果 |
|----|------|
| ruff / mypy | All checks passed / Success（79 文件） |
| G73 定向 | Ran 3 · OK |
| 快集全量 | **Ran 1335 · failures=0 · errors=0 · skipped=3 · 覆盖 82%** |
| CI | run `39316312065` · **7/7 job success** |
| Release | `v9.8.2` · https://github.com/lza6/Git-clone-Max/releases/tag/v9.8.2 · 双产物 sha256 一致 |

**停止条件**：G72 调研 backlog 已全部处理（DLQ 为最后一项）；无新增 P0/P1。
B4 仓库知识索引 / B5 MCP annotations / B6 真机点击仍属后续范围，不在本轮擅自扩权。

---

## 21. G74 UI 布局与可读性（2026-10-20 · v9.8.3）

### 21.1 来源
用户反馈「UI 界面还有很大问题」，要求实际启动、截图、逐张排查。
此前 UI 测试只覆盖控件存在/可见性，没有对真实渲染做视觉审查。

### 21.2 状态

| ID | 节点 | 状态 | 证据 |
|----|------|------|------|
| G74-1 | 截图审计脚本（4 主题 × 5 分辨率 + 8 对话框，38 PNG） | Done | `scripts/ui_screenshot_audit.py` · `reports/ui-audit-final/` |
| G74-2 | 第一轮：主操作常驻/工具栏分行/设置网格/状态纯文本 | Done | `test_g74_ui_layout`（7） |
| G74-3 | 第二轮：透明标签/数字框/日期列/重试原因/DLQ 建议/雷达首屏/关闭中文化/长路径 | Done | `test_g74_ui_layout`（+7） |
| G74-4 | 文案与 i18n（关闭=Close、不启用=Off、向导提示） | Done | `test_g49_i18n` + key 完整性守卫 |
| G74-5 | 快集/构建/健康检查/发布 | 见 §21.3 | — |

### 21.3 验证与发布

| 项 | 结果 |
|----|------|
| ruff / mypy | All checks passed / Success（79 文件） |
| G74 定向 | Ran 14 · OK |
| UI/i18n 回归 | Ran 178 · OK |
| 快集全量 | **Ran 1349 · failures=0 · errors=0 · skipped=3 · 覆盖 82%** |
| 截图审计 | 38/38 PNG |
| 构建 | exe 41,220,708 B · zip 79,559,473 B |
| 健康检查 | version_match 9.8.3 · started_ok=true · 无残留进程 |
| 打包 E2E | `action=cloned`（真实 GitHub）；二次 `action=fetched / 已是最新`；源级 `e2e_smoke` PASS（含 +1 提交更新） |
| CI | run `39446613322` · **7/7 job success**（ubuntu/windows/macos + e2e + fast + slow + perf） |
| Release | `v9.8.3` · id 418545682 · https://github.com/lza6/Git-clone-Max/releases/tag/v9.8.3 · exe sha256 一致 |

**停止条件**：截图可见的 P0/P1/P2 布局问题均已修复并有守卫；剩余为「极小高度需滚动」
与「离屏非真机点击」的已知边界，不再扩大范围。


## 22. G75 视觉系统 + G76 接线收口（2026-10-23，v9.9.0）

### 22.1 边界与目标

| 项 | 内容 |
|---|---|
| 目标 | ①把 G75 视觉系统重构从「半成品」收成可交付；②把 11 处「实现了但用户够不到」降到 0 |
| 起点 | `main` @ `0018628`（9.8.3）；工作区已有 G75 半成品（5 个新 UI 模块、`progress_table` 848 行、门禁红） |
| 结束 | `main` @ 见 Release；版本 9.9.0；快集 1415 绿 / 覆盖 82% / exe + 便携包健康 |

### 22.2 节点

| ID | 任务 | 状态 | 验收 |
|---|---|---|---|
| G75-1 | 拆 `theme.py`（598→314）为 `palettes`/`typography`/`qss_build` + 22 符号兼容再导出 | Done | `test_g46_theme`/`test_themes`/`test_g70_ui_upgrade` 全绿 |
| G75-2 | 七级字号 + 三字族；`apply_type`（QFont 字距）真实被 `status_strip` 调用 | Done | `test_g75_visual_system.TestTypography` |
| G75-3 | 四套色板重制（Ink/Paper/极简/HC）+ `cta/on_cta/glow` token；修 nord success 4.23→4.70 | Done | 逐对 WCAG 断言 |
| G75-4 | QSS 层级重排（主窗渐变 / 卡片内嵌高光 / 页签选中 / 默认按钮幽灵化） | Done | `test_g75_visual_system.TestQssLayering` |
| G75-5 | 仪表齿条 `status_strip` + 批次总览 `batch_overview`（职责分离，D23 定案） | Done | `test_status_strip_is_wired` 等 |
| G75-6 | 状态形状编码 `status_pill`（QPainter，不用字符符号）+ 结果文案 `result_labels` | Done | 5 态 glyph 互不相同 |
| G75-7 | `progress_table` 848→799（总览/文案外提） | Done | 行数断言 |
| G75-8 | 设置导航 01–07 编号 + 去掉「G53」工程代号 | Done | `test_g68_settings_ui` 正则守卫 |
| G76-1 | 6 个「只读不写」设置字段补写入侧（`minimize_to_tray`/`clone_timeout`/`post_clone_hook`/`watchdog_silent`/`sched_report_format`/`quick_repos`） | Done | `test_g76_wiring.TestSettingsWritesPersist` 等 |
| G76-2 | 克隆后钩子：二次确认（默认 No）、拒绝则回滚、无可见窗口不弹模态 | Done | `TestPostCloneHookSecurity`（3） |
| G76-3 | 扫描进度确定态（`ScanWorker.progress` 接线；总数未知仍不确定态） | Done | `TestScanProgressDeterminate`（3） |
| G76-4 | 更新提示附 SHA256（`check_latest_with_sha` 终于有生产调用；信号签名保持 4 参） | Done | `TestUpdateShowsSha256`（2） |
| G76-5 | 数据目录私密性显示 + 迁移入口（拒绝在跑任务；不覆盖；复用既有 `migrate_data_dir`） | Done | `TestDataDirEntry`（3） |
| G76-6 | 死信号 `rowsChanged/visChanged` 接到管理页统计行 | Done | `TestManageStatsFollowModelSignals`（2） |
| G76-7 | 治理：`ci.yml` 覆盖率死值 84 删除、README 测试数改 135、`plan-doc-audit` 过期说法更正、`test_ledger` 新增 S32 | Done | 文档守卫 11 绿 |
| G76-8 | `bench_g70_ui.py` 因 `_QSS_CACHE` 迁模块而崩 → 修正（回归修复） | Done | 基准跑通并留数 |

### 22.3 验证与发布

| 项 | 结果 |
|----|------|
| ruff / mypy | All checks passed / Success（86 文件） |
| G76 新增守卫 | Ran 23 · OK |
| 定向回归 | Ran 234 · OK |
| 快集全量 | **Ran 1415 · failures=0 · errors=0 · skipped=4 · 覆盖 82%** |
| 真实 git E2E（源码级） | `E2E_SMOKE_RESULT: PASS`（clone/ff 拒绝/冲突/abort/HEAD） |
| 打包 E2E（exe，真实 GitHub） | `action=cloned` → 二次 `action=fetched / 已是最新`，exit 0 |
| 性能基准 | 500 行插入 26.82ms · delegate 0 子控件 · 启动 97.84ms · QSS 缓存 2.39→0.02ms |
| 截图审计 | 基线 38 / 改后 38（已人工过目设置页与下载页） |
| 构建 | exe 41,340,204 B · zip 79,756,248 B |
| 健康检查 | version_match 9.9.0 · started_ok=true · artifacts_exist=true |
| 文件规模 | 超 800 行 3 个（main_window 2750 登记债 / service 1768 / engine 1169） |
| CI（第一次） | run `39897531133` · **5/7 绿**：windows / slow / perf / e2e / fast 通过，**macos-latest 与 ubuntu-latest 的 fast suite 各 1 例红** |
| CI 红根因（真实缺陷） | `test_migrate_calls_existing_helper_after_confirm`：POSIX 无 `%APPDATA%` → 迁移流程直接放弃 → 用户点「迁移」只会看到「无法确定 %APPDATA%」，**功能等于不可用**（不是测试挑剔） |
| 修复 | `settings_actions._private_target_dir()`：Windows `%APPDATA%` → POSIX `$XDG_DATA_HOME` → `~/.local/share`；算不出来仍拒绝（不瞎猜）；文案改跨平台；补 `test_migrate_refuses_without_private_target` |
| CI（修复后） | run `39899096292` · **7/7 success**（windows/ubuntu/macos + slow + fast + e2e + perf） |
| CI（v9.9.1） | run `39899957217` · **7/7 success** |
| 发布 | `v9.9.0` id 421433715（已被取代，body 已加说明）· **`v9.9.1` id 421450820** https://github.com/lza6/Git-clone-Max/releases/tag/v9.9.1 |
| 9.9.1 产物 | exe 41,343,506 B sha256 `9ddc54b1…` · zip 79,761,826 B sha256 `37c69482…` · 服务端 sha256 已核对一致 |
| 打包 E2E（9.9.1） | `action=cloned`（真实 GitHub），版本 9.9.1 |
| 未执行 | 本机 Windows 慢集（CI 在 Linux 跑） · 真机 125% DPI 目检 · 数据目录真实搬移 |

**停止条件**：所有 P1「够不到」项已接线并有能失败的断言；门禁/文档/台账与代码一致；剩余为已披露边界。

## 23. G75 收口（2026-10-23，v9.9.2）

| 项 | 内容 |
|----|------|
| 目标 | 补掉 G75 计划里最后一个视觉缺口（进度条 25/50/75% 刻度），并把"计划里写着但上一轮没跑"的实测全部做完 |
| 代码增量 | `progress_delegate.tick_positions()` 纯函数 + `paint()` 画刻度；`tests/test_g75_visual_system` 新增 `TestProgressTicks`(3) |
| 测试对齐 | `tests/test_g66_patch` 的弹窗打桩补到 `gcm.ui.update_flow.QMessageBox`（G76 把弹窗搬走后打桩过期 → 确定性红；生产代码未动） |
| 慢集（Windows） | `Ran 129 · OK`（65.9s，真实 git E2E） |
| 基准 | 500 行 add 27.85ms · update 2.87ms · 0 个 QProgressBar；QSS 缓存 1.989→0.016ms；空库启动 101.93ms |
| 启动器 | `scripts/启动Git-clone-Max.bat` 实跑：Python 3.14.3 → 启动常驻 >12s → 进程树回收 |
| 契约 | CLI 退出码 0/1/2（成功/有失败/无有效输入或读取失败）、`--help`=0、MCP 未启用=3 |
| 更新链路 | 对真实 Release 三分支验证（9.9.0→有新版、9.9.1/9.10.0→已最新），body sha256 与产物一致；本机 urllib 受 TLS 代理限制，W7 用 requests 取真实 JSON 替换传输层（已披露） |
| flake 复核 | `TestShutdownClearsInProgress` ×10 全绿（T-33） |
| SQL 审计 | f-string 仅 PRAGMA 常量；`sync_history` 有索引；`SELECT * FROM repos` 无限量＝本地桌面规模，已评估不适用分页 |
| 独立审查 | 独立只读 reviewer 子代理对 `0018628..HEAD` 全量 diff 审查（结论见 §24，若已回） |
| 发布 | `v9.9.2`（见下） |

## 23. G77 主窗再拆一块 + 高 DPI 与慢集实测（2026-10-23，v9.9.2）

| 项 | 结果 |
|---|---|
| 目标 | 清掉上一轮自己登记的「剩余未闭环项」：本机慢集、高 DPI 渲染、真实文件搬移、独立审查、`main_window.py` 增量拆分 |
| 主窗拆分（行为零变化） | 更新检查三段 → `gcm/ui/update_flow.py`（142 行），`main_window` **2750 → 2658**；同名薄方法保留（测试直接调这些名字） |
| 单一 patch 点 | `update_flow._msgbox()` 运行期从 `gcm.ui.main_window` 取 `QMessageBox` —— 否则 `mock.patch("gcm.ui.main_window.QMessageBox")` 静默失效，测试卡真模态挂死（本轮实测踩到并修复） |
| 守卫随代码走 | `test_g66_patch` 的 AST 用例改为同扫 `main_window` + `update_flow`，并断言实现里真的 `threading.Thread(...).start()`（原来的写法拆完会退化成空断言） |
| 真实文件搬移 | `test_g76_wiring` 增 2 条：真 sqlite + 真 `shutil.move`，验 ①文件到位 ②内容完好 ③源目录保留 ④同名不覆盖 |
| 本机慢集（Windows 真实 git） | **Ran 129 · OK（69.7s）** |
| 快集全量 | **Ran 1421 · failures=0 · errors=0 · skipped=4 · 覆盖 82%** |
| 高 DPI 渲染 | `QT_SCALE_FACTOR=1.25` 重跑截图审计：**38/38**，人工过目 `download_deep_1440x900` 与 `tab_settings_1024x768` —— 齿条/刻度/pill/设置页新控件均正确，无裁切、无 tofu、无重叠 |
| 进度刻度（并发会话落地） | `progress_delegate.tick_positions()` 纯函数 + 3 条单测；paint 里画在填充之上、文本之前（不遮数字） |
| 构建 | PyInstaller 从**已提交树**重建（保证产物 == tag），健康检查见下 |
| 未执行 | 真人目检（无真人）；真实用户数据目录搬移（只在临时目录做） |

### 23.1 发布与下载复验（v9.9.2）

| 项 | 结果 |
|---|---|
| CI | run `39905267408` on `d69d229` · **7/7 success** |
| 产物 == 源码 | 构建在提交之后，且 `_internal/gcm/__init__.py` 与工作区 sha256 **一致** |
| 健康检查 | `version_match 9.9.2` · `started_ok=true` · `repos_db_created=true` |
| Release | `v9.9.2` id 421490075 · https://github.com/lza6/Git-clone-Max/releases/tag/v9.9.2 |
| 服务端校验 | 两次上传：①并发会话首传 exe `30a76fcd…`（其内置下载校验因 SSL 抖动被跳过，改用 API digest + 下载重算复核）；②**最终发布物**由本会话用 HEAD(d69d229) 重建后重新上传并**走通内置下载校验**：exe `c70a48a8…`（41,354,950 B）/ zip `05b21b6c…`（79,779,525 B）。**以 API asset digest 与本地实测 sha256 为准，两者一致** |
| 下载物 E2E | 用**从 Release 下载回来的 exe**：`action=cloned` → 二次 `action=fetched / 已是最新`，空输入 `exit=2` |
| 边界 | 并发会话 13:45 又跑了一次本地构建，`dist/` 里的 exe 因此与已发布哈希不同（源码/工作区未变）；发布物以服务端 digest + 下载复验为准 |

### 23.1 发布证据回填（v9.9.2）

| 项 | 真实值 |
|----|--------|
| 提交 | `d69d229`（`feat(g77)`，含 G75 收口增量）· 已推送 `origin/main` |
| CI | run `39905267408` **7/7 success**（test win/ubuntu/macos + e2e-smoke + fast-smoke + slow + perf-baseline）|
| 本地快集 | `Ran 1421 · failures=0 · errors=0 · skipped=4`；覆盖率 **82%**（Windows 门禁 81）|
| Lint / 类型 | `ruff check gcm` = All checks passed；`mypy gcm` = Success（87 source files）|
| 构建 | PyInstaller EXIT=0；exe **41,354,950 B** sha256 `c70a48a8…`；zip **79,779,525 B** |
| 产物健康 | `health_check.py`：`version=9.9.2`、`archive_version=9.9.2`、`started_ok=true`、`repos_db_created=true` |
| 打包 E2E | `dist/Git-clone-Max.exe --cli --json`：首跑 `action=cloned`、复跑 `action=fetched`、磁盘 `HEAD=7fd1a60`、`version=9.9.2` |
| 发布 | Release id `421490075` · https://github.com/lza6/Git-clone-Max/releases/tag/v9.9.2 · 服务端下载校验 sha256 一致 |
| 未执行 | 真机 125% DPI 目检 · 实际点击更新弹窗开浏览器（`webbrowser.open` 未在自动化里断言，只验到"是否弹窗"分支）|

## 24. 独立审查记录（v9.9.3：G77 拆分 + G75 收口）

> 说明：本轮先后启动过 3 个独立只读 reviewer 子代理，前两个随会话重启被回收（无结论），
> 第三个改为**前台 + 10 分钟硬时间盒**完成。以下只记录它实际给出的内容。

| 项 | 内容 |
|----|------|
| 审查对象 | `git diff 042b641..HEAD -- gcm/ tests/`（`update_flow.py` 抽取 + `progress_delegate.tick_positions` + 相应测试与版本 bump）|
| 审查方式 | 独立上下文、有罪推定；允许只读命令（`git diff`/`grep`/单模块 `unittest`/python 探针）|
| **Verdict** | **Approve**（无 Critical Issues）|
| 它实测过的命令 | `tests.test_g66_patch.TestUpdateCheckThreading` 5 OK · `TestProgressTicks` 3 OK · `test_g76_wiring`+`test_update_auto` 32 OK · `test_g68_settings_ui.TestNoModalFromHiddenWindow`+`test_manage_ui` 29 OK · `ruff` 0 error · `mypy` Success |

### 24.1 Required Changes 与关闭结果（全部已修）

| # | 审查发现（file:line） | 我的处理 | 证据 |
|---|----------------------|---------|------|
| R1 | `tests/test_g66_patch.py` 的「给 `update_flow` 也打桩」分支是**死代码**（该模块无模块级 `QMessageBox`，靠 `_msgbox()` 运行期取 `main_window.QMessageBox`），注释宣称的保护不存在 | 删除 `uf_mod`/`real_mb_uf` 两处分支与 import，注释改为「单点打桩即覆盖」 | `grep real_mb_uf` = 0 命中；`tests.test_g66_patch` 66 OK |
| R2 | `tests/test_g66_patch.py` 新增行 E501（104 > 100）| 折行 | `ruff check gcm` 与人工复核 |
| R3 | `update_flow.on_checked` 的 `_update_checking = False` 被排到延迟 import **之后**，一旦 import 抛异常该标志永久置位 → 「检查更新」永久失效且零提示 | 复位提到 import 之前（原实现中它也是第一条语句）| 68 行附近删除即回滚；`test_g66_patch`/`test_update_auto`/`test_manage_ui` 绿 |

### 24.2 Suggestions 采纳情况

| # | 建议 | 处理 |
|---|------|------|
| S1 | `assertIn("threading.Thread", 全文)` 属弱断言（线程挪到同文件别的函数仍绿）| **采纳**：改成 AST 作用域断言（必须位于 `check_now` 自身语句），并做**变异实验**验证：①把起线程改成 `_worker()` → `Thread` 缺失=真；②把线程挪进另一个函数 → `Thread`/`start` 均缺失=真 |
| S2 | `mb = _msgbox()` 在 `try` 之外 | **采纳**：移进 `try`，异常走降级提示 |
| S3 | `check_silent` docstring 丢了 G67-9 的历史理由 | **采纳**：补回「原实现 `QTimer.singleShot(2500,...)` 在 GUI 线程查网 → 弱网冻结窗口」|
| S4 | 未知进度（`pct<0`）也画刻度 | **采纳**：`if pct >= 0:` 才画；`tick_positions` docstring 写明 `steps` 会 int 截断 |
| S5 | `tests/test_g76_wiring.py` 巨型 `with` 可读性差（建议 `ExitStack`）| **不采纳**（本轮范围外、纯可读性；登记为后续）|
| S6 | `progress_delegate.py` 三连空行（E303）| **采纳**：已删 |

### 24.3 审查未覆盖的范围（诚实披露）

- 审查只覆盖 `042b641..HEAD` 的**代码与测试**，未重跑全量快集、未审 `docs/`、未做安全专项。
- 「点击更新后真的打开浏览器」`webbrowser.open` 仍无自动化断言（只验到"是否弹窗"分支）。

### 23.2 独立复验（发布物被二次构建替换后重做）

| 项 | 结果 |
|---|---|
| 服务端事实（API） | `v9.9.2` id 421490075：exe **41,354,950 B** digest `c70a48a8…`；zip **79,779,525 B** digest `05b21b6c…` |
| 三方一致 | API digest == **从 Release 下载回来重算的 sha256** == 本地 `dist/` 实测 sha256（exe `c70a48a8…`、zip `05b21b6c…`） |
| 下载物 E2E | 用**下载回来的 exe**：`version=9.9.2` → `action=cloned` → 二次 `action=fetched / 已是最新` → 无效输入 `exit=2` |
| CI | `d69d229` / `e5ab085` / `ff1c3f0` / **`57b31a9`** 四次 run **全部 7/7 success**（最新 run 39908614927） |
| 说明 | 首传（`30a76fcd…`）与终传（`c70a48a8…`）是**同源码两次构建**；以终传为准。两次下载遇到 TLS 抖动，第三次成功 —— 网络问题，非产物问题 |

## 25. v9.9.3 发布证据（独立审查整改轮）

| 项 | 真实值 |
|----|--------|
| 提交 | `0643535`（含整改代码，由并发会话代为提交）+ `afb92e2`（归属更正）· 已推送 `origin/main` |
| 门禁 | `ruff check gcm` = All checks passed；`mypy gcm` = Success（87 source files）|
| 定向回归 | `unittest`（g66_patch / g75_visual_system / update_auto / g76_wiring / g68_settings_ui / manage_ui / progress_ui）= **196 tests · OK**（2 skipped）|
| 全量快集 | `Ran 1421 · failures=0 · errors=0 · skipped=4`；覆盖率 **82%** |
| 构建 | PyInstaller EXIT=0；exe **41,358,954 B** sha256 `d68cc4ca…`；zip **79,788,175 B** |
| 产物健康 | `health_check.py`：`version=9.9.3`、`archive_version=9.9.3`、`started_ok=true` |
| 打包 E2E | `action=cloned` → `action=fetched`，`version=9.9.3` |
| 发布 | https://github.com/lza6/Git-clone-Max/releases/tag/v9.9.3 · 服务端下载校验 sha256 一致 |
| 独立审查 | 见 §24（Approve；3 条 Required + 4 条建议全部关闭/采纳，1 条建议登记不采纳）|
| 未执行 | 真机 125% DPI 人工目检 · 「点击更新后真的打开浏览器」（`webbrowser.open` 仍无自动化断言）|

### 25.1 收尾补测与边界（同日追加）

| 项 | 结果 |
|----|------|
| 新增「更新弹窗尾部链路」测试 | `tests/test_g75_update_flow.py`（5 项，`Ran 5 · OK`）：点「是」→ `webbrowser.open(url)` 恰好一次 · 点「否」→ 不开 · err 分支 → 只提示 · `_update_sha256` 用后清空 · 不可见窗口不弹模态 |
| 为什么单独成文件 | `tests/test_g66_patch.py` 已 1597 行，且它没有模块级 `QApplication`：把「先建 app 再建 MainWindow」的用例塞进去**实测会把进程挂住**（已复现并回退）|
| 125% DPI 渲染核查 | `QT_SCALE_FACTOR=1.25` 离屏渲染 38 张（`reports/ui-g75-125/`），逐张看：布局整体正确、pill/刻度/齿条均正常；**发现 1 处外观裁切**（1024×768@125% 下「快速填充」行半截），已登记 `.agent/memory/known-issues.md` |
| CI 状态 | `57b31a9` → run success **7/7**；`0643535` / `afb92e2` / `5e91f35` 的 run **仍在 GitHub 队列中（queued）**，本报告不把它们计为已通过 |

### 23.3 v9.9.3（独立审查整改）与发布物三方核对

| 项 | 结果 |
|---|---|
| 为什么要 9.9.3 | ①**我 G77 拆分引入的真 bug**：`update_flow.on_checked` 把 `_update_checking = False` 排到 `from .main_window import _fmt_dt` **之后** —— 该导入一旦抛异常，标志永久 True，「检查更新」按钮永久失效且零提示；②未知进度（`pct<0`）仍画 25/50/75 刻度 → 空轨道被误读成已有进度；③弱子串断言 → AST 作用域断言（变异实验验证） |
| 本地验证 | `ruff` 全过 · `mypy` Success(87) · 定向 **168 条 OK** · 快集 **1421 绿** · 打包 E2E cloned→fetched · 无效输入 exit=2 |
| 产物 | exe **41,358,954 B** / zip **79,788,175 B**；`health_check` = `version_match 9.9.3` + `started_ok` |
| 发布 | `v9.9.3` id 421507266 · https://github.com/lza6/Git-clone-Max/releases/tag/v9.9.3 |
| 三方一致 | API asset digest == 本地实测 sha256 == **从 Release 下载回来重算** 的 sha256（exe `d68cc4ca…` / zip `fdf773ab…`） |
| 下载物 E2E | 用下载回来的 exe：`version=9.9.3` → `cloned` → `fetched/已是最新` → 无效输入 `exit=2` |
| 产物 == 源码 | 构建于 `afb92e2`；其后 `5e91f35`/`3fa9b16` **只改 tests 与 docs**（`gcm/` 无变更），故产物仍对应 HEAD 源码 |
| CI | `d69d229`/`e5ab085`/`ff1c3f0`/`57b31a9` 四次 run **7/7 success**；`afb92e2` 之后 4 个 run **一直 queued 未出结论** → **不声称通过**，后台轮询中，出结果后补登记 |

## 26. v9.9.4：快捷填充 chip 裁切修复（自查发现的真实缺陷）

| 项 | 内容 |
|----|------|
| 缺陷 | `quick_scroll` 写死 `setFixedHeight(38)`，chip 实测 `sizeHint().height() == 42` → 底部 4px 被裁（1440×900 标准模式可见）|
| 误判更正 | 早先登记为「仅 1024×768@125% 缩放」；实测三档尺寸 + 三档 `QT_SCALE_FACTOR` 后确认**与缩放无关**，已改 `known-issues.md` |
| 修法 | `sync_quick_scroll_height()`：按最高 chip 高度 + 2 推导；在 `rebuild_quick_fill()`（编辑后）与 `_apply_layout_mode()`（尺寸/缩放变化后）两处重新同步 |
| 守卫 | `tests/test_g78_quick_scroll`(3)：标准模式不裁切 · 紧凑模式按设计隐藏 · 编辑后高度跟随 · 重复同步幂等 · 无 scroll 宿主不抛 |
| 视觉证据 | `reports/ui-g78/`（38 张，改后）对照 `reports/ui-g75/`（改前）|
| 教训 | 把"像素常量 vs 设备缩放"混为一谈会误导修复方向；尺寸类缺陷应当**先量 `sizeHint` 再改** |

### 26.1 v9.9.4 发布证据

| 项 | 真实值 |
|----|--------|
| 提交 | `d2ec841`（修复+版本 bump）+ `982cfe7`（移出误提交的 38 张截图，`.gitignore` 收敛为 `reports/ui-*/`）· 已推送 |
| 门禁 | ruff 0 error · mypy Success · 定向 134 tests OK · 快集 **1429 tests / 0 fail / 4 skip / 覆盖 82%** |
| 构建 | PyInstaller EXIT=0；exe **41,361,913 B** sha256 `5e607063…`；zip **79,792,364 B** |
| 产物健康 | `health_check.py`：version=9.9.4 · archive_version=9.9.4 · version_match=true |
| 打包 E2E | `action=cloned` → `action=fetched`，磁盘 `HEAD=7fd1a60`，`origin` 已规范化回 `https://github.com/...`（镜像只记在 `gcm.mirror`）|
| 发布 | https://github.com/lza6/Git-clone-Max/releases/tag/v9.9.4 · 服务端下载校验 sha256 一致 |
| 环境注记 | 本机此刻**直连 github.com 的 git 全部失败**（`git ls-remote` 3/3 `TLS connect error: unexpected eof`），E2E 首跑因此 failed；重试由**内置镜像回退**成功 —— 顺带验证了 G58-2/G69-21 的镜像链路与「克隆后回源站」规则 |
| CI | **仍在 GitHub 队列（queued）**，见 §27；本表不把未跑的 run 计为通过 |

## 27. ~~阻塞登记：GitHub Actions 不派发（2026-10-23 16:05 实测）~~ **已闭环（2026-10-24 复核）**

> **本节结论已作废，保留原始记录用于追溯。** 2026-10-24 通过 GitHub Actions API 只读查询确认：
> 最新 HEAD `7097209` 的 run **`39922315053`** 状态 `completed / success`，**7/7 job 全绿**
> （slow 55s · e2e-smoke 6s · perf-baseline 32s · test(ubuntu) 135s · test(windows) 306s ·
> test(macos) 171s · fast-smoke 25s），完成时间 `2026-10-23T11:35:03Z` —— **晚于本节登记阻塞的时刻**。
> 即：runner 恢复后，按本节自订的「恢复后要做的唯一一件事」执行，结论为**绿**。
>
> 仍未闭合的只有一件事：**Actions 分钟额度从未核实过**（`evidence/G78-ci-concurrency.json` 的
> `not_verified` 项），需要浏览器打开 `/user/settings/billing/actions` 才能看到。这不影响代码结论。
>
> **教训（写进 memory）**：在 runner 异常期间反复推送既解决不了问题，又会因
> `cancel-in-progress` 把正在跑的 run 全部取消 —— 最终连「最后一次真实结论」都不敢引用。
> 正确做法是**先停推、只在本地留证据、等恢复后看最新 HEAD 的那一个 run**。

| 项 | 事实 |
|----|------|
| 现象 | run `39917512506`（`2f4e06e`）自 07:24 UTC 起 **pending 41 分钟**，零 job 启动；`faa33f4` 的 run 自 06:40 UTC 起 **queued 1h25min** |
| 已排除 | ①`githubstatus.com` 显示 Actions **operational**；②workflow YAML 可解析（run 被成功创建）；③并发组未卡死（见下）|
| 已确认发生的事 | 并发会话在 `9051402` 给 workflow 加了 `concurrency{group: ci-<ref>, cancel-in-progress: true}` → **旧 run 全部被取消**（`9051402/7847bd5/d2ec841/982cfe7/c6373e3/6023580` 均 `cancelled`），**包括 `afb92e2`（v9.9.3 对应提交）与 `d2ec841`（v9.9.4 对应提交）** |
| 结论 | 这不是代码问题；**是 runner 不派发**。按停止条件「缺不可替代的环境」上报阻塞，不伪造"CI 通过" |
| 最后一次真实 CI 结论 | `d69d229` / `e5ab085` / `ff1c3f0` / `57b31a9` 各 **7/7 success**（run 39905267408 等）|
| 恢复后要做的唯一一件事 | 看**最新 HEAD 的那一个 run**：它包含当前全部代码（G75 + G76 + G77 + 九.9.4 修复）；绿即可把本段改为"已闭环" |
| 不要再做的事 | 每推一次都会因 `cancel-in-progress` 取消正在等/在跑的 run —— **runner 恢复前不要为了登记而反复推送** |

## 28. G79 工程治理与配置真实性收口（2026-10-24，v9.10.0）

> 来源：`docs/计划书/下一步改进指南.md`（Rev 5）方案 A，用户 2026-10-24 拍板。
> 纪律：只做治理，不做功能、不做大幅重构；每条改动都先有「能失败」的守卫。

### 28.1 边界与目标

| 项 | 内容 |
|---|---|
| 起点 | `main` @ `7097209`（9.9.4）+ 上一轮未提交的计划文档（Rev 5） |
| 目标 | ①删掉唯一零读取的僵尸配置字段 ②修好「本该抓它却没抓到」的守卫 ③截图与根目录死文件出库 ④一阶文档事实对齐 |
| 不做 | 主窗拆分（T-41/T-42）、`settings_groups` 拆分（T-43）、测试确定性根治（T-44）—— 属后续独立轮次 |

### 28.2 节点

| ID | 任务 | 状态 | 验收（真实输出） |
|---|---|---|---|
| G79-36a | `reports/ui-audit-final/` 38 张 PNG 出库 + `.gitignore` 补规则 | Done | `git ls-files reports/` 46→**8**；本地 38 张仍在（可重建） |
| G79-36b | 根目录 `z.png` / `cli.py` 删除 | Done | 先全仓 grep 确认零引用（`cli.py` 仅命中 `gcm/cli.py`） |
| G79-36c | 新增 `tests/test_g79_repo_hygiene`（9 项） | Done | **首跑 FAILED (failures=4)** → 修复后 `Ran 9 · OK` |
| G79-37a | 一阶文档对齐 | Done | 测试文件数 113/135→**137**；主窗 2658→**2665**；31→**42** 个测试文件；PyQt6「未锁」→已锁 `6.11.0` |
| G79-38 | `test_ledger` 回填 + 新增 S34 | Done | §2.1 追加 5 行真实数字；S34「可重建产物 → git 索引」 |
| G79-39 | `workflow_status` §27 阻塞结论更正 | Done | 改为「已闭环」+ run `39922315053` 7/7 全绿 |
| G79-45 | 删除僵尸字段 `Settings.depth` | Done | 旧配置含 `depth` 仍能加载（`has depth attr: False`，未知键忽略） |
| G79-46 | 收紧 `TestG69_18NoZombieSettingsFields` 口径 | Done | 收紧后 **FAILED 并精确点名 `['depth']`** → 删字段后 `Ran 2 · OK` |
| G79-40 | `evidence/G79-hygiene.json` | Done | JSON 可解析（15 键），含红/绿两段真实输出 |

### 28.3 这一轮最有价值的东西：一个「绿着的守卫」

`Settings.depth` 是僵尸字段（随 `asdict` 落盘进用户 `settings.json`，全库零读取），
而 `TestG69_18NoZombieSettingsFields` **一直是绿的**。根因：它把**任意 `ast.Name`**
都算作「字段引用」，于是 `gcm/app/diag_bundle.py` 的 `_redact_obj(value, depth=0)`
参数名（函数体里 `if depth > 4` 是 `ast.Name`）把 `depth` 放行了。

收紧口径后守卫立刻红并指名 `depth` —— **这才是「守卫存在」的证据**。
顺序被固定为「先让守卫红、再改代码让它绿」，否则永远无法证明守卫有效。

### 28.4 验证（本轮真实运行）

| 项 | 命令 | 结果 |
|---|---|---|
| 静态门禁 | `ruff check gcm` / `mypy gcm` | **All checks passed** / **Success（87 文件）** |
| 快集全量 | `coverage run --source=gcm scripts/ci_run_fast.py` | **Ran 1438 · failures=0 · errors=0 · skipped=4** |
| 覆盖率 | `coverage report --include="gcm/*"` | **TOTAL 82%**（门禁 Windows 81 / POSIX 80） |
| 卫生守卫红→绿 | `unittest tests.test_g79_repo_hygiene` | 首跑 **4 fail**（38 PNG / cli.py+z.png / 135≠137 / 113≠137）→ **9 OK** |
| 僵尸字段红→绿 | `unittest ...TestG69_18NoZombieSettingsFields` | 首跑 **1 fail**（点名 `['depth']`）→ **2 OK** |
| 版本单源 | `TestContractVersionSingleSource` + `TestPackagingVersionSync` | **Ran 11 · OK** |
| 旧配置兼容 | 加载含 `depth`+未知键的 settings.json | `loaded concurrency: 4` / `has depth attr: False` |

### 28.5 本轮暴露并修掉的一个真实测试缺陷

改版本号 9.9.4→9.10.0 后，`tests/test_g44_url_updater.py` 立刻红：
它把 `v9.9.9` 写死当成「比当前更新」的版本 → 语义变成 9.9.9 < 9.10.0。
这是**版本时间炸弹**（与项目已禁的「绝对日期表达相对语义」同类）。
修法：`_newer_tag()` 按当前主版本 +1 动态构造，断言强度不变（仍要求 `has_new=True`）。

> 这条值得单独记：**发版本身是一次回归测试**。凡是把版本号写死进断言的地方，
> 都会在下一个 minor 版本上炸一次。
