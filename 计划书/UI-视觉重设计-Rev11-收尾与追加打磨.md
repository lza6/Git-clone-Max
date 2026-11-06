# UI 视觉重设计 · Rev11 收尾与追加打磨计划（Rev11-C）

> **状态：计划（未执行）**。本文件落盘前，本轮**没有修改任何 `gcm/` 源码、测试、配置或依赖**。
> 实际动作只有：读代码、跑只读命令与定向测试、目检 `reports/ui-rev11-final/` 的截图、写这份文档。
> **你过目并点头之后，我才开始按 §4 / §5 动代码。**
>
> **基线**：`main` @ `f6b913e`；`gcm.__version__ = 9.18.0`；工作区已暂存 Rev11 交付（41 文件，+2990/−127，未提交）。
> 编写日期：**2026-11-05**。
>
> ⚠ **并发改动警示（2026-11-05 05:54 检测）**：工作区出现了**非本会话创建**的未跟踪文件
> （`gcm/app/undo.py`、`gcm/ui/menu_bar.py`、`gcm/ui/undo_bar.py`、`gcm/util/oplog.py`、
> `tests/test_rev12_usability.py`），并且 `main_window.py` 从 2983 涨到 **3110**、
> `manage_panel.py` 从 656 涨到 **678**。这看起来是另一个会话在做「Rev12 可用性（撤销 / 菜单栏 / 帮助）」。
> 因此 §1.1 的数字是 **05:52 快照**，执行前必须重新冻结；与并行会话重叠的文件
> （`main_window.py` / `manage_panel.py`）在它收敛前**不要动**。详见 §1.4。
>
> **本文不是事实源**。事实源仍是：`gcm/__init__.py`（版本）、代码本身（行为）、
> `docs/architecture.md`（分层）、`policy.md`（交付门禁）、`test_ledger.md`（接缝注册表）、
> `evidence/*.json`（节点证据）、`docs/验证记录-*.md`（验证记录）。
> **本文只负责：核对旧计划是否还成立、把剩余项拆到可执行、把追加打磨限定在证据支持的范围内。**

---

## 0. 这份文档怎么用

### 0.1 三类读者

| 读者 | 读哪几节 | 用法 |
|------|---------|------|
| **你（产品负责人）** | §0.2 → §1.3 → §2.2 → §3 → §4 / §5 的任务表 → §8 | 看四件事：旧文档哪里说错了、还剩什么、追加打磨是不是你要的、风险我挡没挡住 |
| **执行 AI / 执行人** | §1 → §4 → §5 → §6 → §7 | 一次只做一个 Wave；开工前先 `git status --porcelain` 核现状，已完成项从批次里划掉 |
| **下一轮审计 / Rev12 作者** | §2.1 → §4 / §5 的「状态」行 → §10 | **不许拿本文的「计划」当已完成证据**；逐条重跑，`evidence/` 没有的一律不算 |

### 0.2 你本轮拍板（授权记录，2026-11-05）

| # | 决策 | 你的选择 | 对本计划的影响 |
|---|------|---------|--------------|
| **D-9** | 收尾范围 | **6 项收尾 + 追加新打磨** | W6 做 R-01…R-06（外加 R-07 文档纠错）；W7 追加 P-01…P-04。改动面最大，但每一项都有本轮代码/截图证据支撑 |
| **D-10** | V-15 动作分区 | **保留两行，只做三区语义** | 不动控件树、不改按钮文案、不冒 1020×560 溢出风险；用 objectName + QSS 权重表达主区/批次控制区/工具区 |
| **D-11** | `replace` 危险档 | **单独满饱和 warning 计数**（为 0 时隐藏） | 结论条新增危险计数标签；`replace > 0` 时用户动手前就能看到「N 将删除重建」 |
| **D-12** | 本轮动作 | **只评审文档，不执行** | 本轮不修改任何 `gcm/` / `tests/`；你看完本文后再决定是否开 W6。§4/§5 仍保持 `TODO`，未被标成 DONE |

> **D-11 的理由（写死在这里，防未来被“界面更简洁”说服）**：
> `app/batch_plan.py` 的 docstring 明写 `replace` 会**先删目标目录再重克隆**，
> 它是整批里唯一“会毁东西”的动作。把它并进「将克隆」等于在界面上把危险抹平；
> 本项目的 `policy.md` 与 `CLAUDE.md` 都要求「不静默、诚实呈现」，所以它必须单独可见。
> 它只在 >0 时出现，不会给普通批次增加噪音。

### 0.3 证据标签（全文档强制，与 `policy.md` §3 一致）

- `已验证`：**本轮**实际运行并观察到结果（附命令与输出摘要）
- `静态确认`：读代码/截图确认，未运行
- `合理推断`：有间接证据，未直接验证
- `待验证`：缺环境 / 权限 / 凭证 / 时间

**「已验证」不接受转述**。执行方引用本文指标时，必须自己重跑一次再写进 `evidence/`。

### 0.4 状态回填与证据命名（防「全部完成」式伪闭环）

每个任务有唯一 ID（`R-01` / `P-01`）。完成后在本文对应条目的 `状态:` 行改成：

```
状态: DONE 2026-11-XX · evidence/rev11c-<wave>.json · docs/验证记录-2026-11-05-Rev11C.md
```

**只允许** `TODO` / `DOING` / `DONE` / `CUT（附理由+拍板人）` / `BLOCKED（附最小缺失条件）`
五种。禁止「基本完成」「已完成大部分」「理论上可行」。**没有 evidence 文件的一律不许标 DONE。**

### 0.5 本文与旧文档的关系（先读这段，避免拿两份互相矛盾的要求执行）

| 文档 | 角色 | 本轮处置 |
|------|------|---------|
| `计划书/UI-视觉重设计-Rev11.md`（668 行） | 已批准的 **设计宪法 + W0–W5 任务书** | **设计原则继续有效**（§2 三原则、§2.3 色彩预算、§2.4 排版声部、§5 守卫账）；**执行状态与 5 处事实描述按本文 §2.2 更正** |
| `docs/验证记录-2026-11-04-Rev11.md` | 上一轮的执行证据与边界清单 | **不重写历史**；在末尾追加「2026-11-05 更正」一节，指向本文 §2.2 与 `evidence/rev11c-*.json` |
| `docs/plans/.rev10-part*.md`（未跟踪） | 另一批草稿，主题是稳定性/反馈闭环 | **不并入、不删除**；本文只在 §2.2-E6 说明它与 `batch_plan.py` 的实际关系已经变了 |
| `docs/archive/plans/20261023/UI视觉系统重构指南.md` | G75 已归档计划 | 只读追溯；其未闭环三项已由 Rev11 承接，本文不复活 |

**冲突消解顺序**：代码事实 > 本文（最新实测） > 旧 Rev11 文档（设计与原则） > 其他历史文档。

---

## 1. 事实基线（2026-11-05 现测，不是转述）

### 1.1 环境与规模

> **以下为 05:52 快照**。05:54 起工作区出现并行 Rev12 改动（`main_window` 3110、`manage_panel` 678、tests 191 等），
> 执行前必须重新冻结基线；对照与避让规则见 §1.4。

| 项 | 实测值 | 获取方式 | 标签 |
|----|--------|---------|------|
| 分支 / HEAD | `main` @ `f6b913e` | `git log --oneline -1` | 已验证 |
| 版本 | `9.18.0` | `gcm/__init__.py:18` | 已验证 |
| 工作区 | Rev11 交付已暂存：**41 文件 +2990/−127**；另有 `?? docs/plans/` 与 4 个截图目录未跟踪 | `git status --short` / `git diff --cached --stat` | 已验证 |
| `gcm/` 规模 | **131 个 `.py` / 32,617 行** | `scripts/dev_metrics.py` | 已验证 |
| `tests/` 规模 | **190 个 `test_*.py` / 36,810 行** | 同上 | 已验证 |
| `gcm/ui/` 模块数 | **55 个** | `ls gcm/ui/*.py \| wc -l` | 已验证 |
| 超 800 行模块 | **3 个**：`app/engine.py`、`git/service.py`、`ui/main_window.py`；与登记集合一致 | `dev_metrics.py` | 已验证 |
| 贴红线（500–800） | `settings_panel` 795 · `progress_table` 795 · `settings_groups` 785 · `repo_db` 750 · `manage_panel` 656 | 同上 | 已验证 |
| `main_window.py` | **2980 行**（与 `CLAUDE.md` §6 当前登记一致） | `wc -l` | 已验证 |
| >100 行函数 | **19 个**（最大 `MainWindow.__init__` 252、`_build_qss` 240、`run_git_ui` 217） | `dev_metrics.py` | 已验证 |
| 异常预算 | `except` 728 / `pass-only` 284 | 同上 | 已验证 |
| 截图基线 | `reports/ui-rev11-baseline/` **34 张**；最终 `reports/ui-rev11-final/` **34 张** | `ls \| wc -l` | 已验证 |

> **容积约束仍是硬的**：`settings_panel` 795 / `progress_table` 795 / `settings_groups` 785 都贴线。
> 本轮任何新增逻辑**不得**再塞进这三个文件；`manage_panel` 656 还有约 140 行余量，
> 但 R-04 的持久化辅助函数若超过 60 行，应拆独立模块（如 `manage_columns.py`）。

### 1.2 本轮真实跑过的命令与结果

| 命令 | 结果 | 标签 |
|------|------|------|
| `./.venv/Scripts/python.exe -m ruff check gcm` | `All checks passed!` | 已验证 |
| `./.venv/Scripts/python.exe -m mypy gcm` | `Success: no issues found in 130 source files` | 已验证 |
| `./.venv/Scripts/python.exe scripts/doc_drift_check.py` | `DRIFT 0` | 已验证 |
| `unittest tests.test_rev11_visual tests.test_rev11_w2 tests.test_rev11_w4` | `Ran 54 tests in 0.396s · OK` | 已验证 |
| `unittest tests.test_g75_visual_system tests.test_g79_repo_hygiene tests.test_ui_compact_visibility tests.test_g76_wiring` | `Ran 85 tests in 12.868s · OK (skipped=1)` | 已验证 |
| `./.venv/Scripts/python.exe scripts/dev_metrics.py` | 见 §1.1；与登记集合一致 | 已验证 |
| 截图目检 | `reports/ui-rev11-final/` 的 `download_deep_1440x900.png`、`tab_manage_1440x900.png`、`tab_settings_1440x900.png`、`dialog_event_stream.png`、`dialog_failure.png` | 已验证（目检） |

> **05:54 复核**：并行 Rev12 改动落盘后，`doc_drift_check` 变为 `DRIFT 5`（main_window 2980 vs 3110、tests 190 vs 191），
> `test_g79_repo_hygiene` 4 例红。这些红灯来自并行改动，不是本计划造成；处置见 §1.4，**不要用改宽断言的方式消红**。

**本轮没有跑**：快集全量、coverage、PyInstaller、真实克隆 E2E、CI 三平台。
理由：本轮只读+计划，无代码变更；这些属于 §7 的收尾门禁，执行完 W6/W7 才跑。

### 1.3 现状核对表（旧文档写的 vs 代码实际）

| 项 | 旧 Rev11 文档写的 | 本轮实读 | 结论 |
|----|------------------|---------|------|
| V-05 accent 预算 | DONE | `qss_build.py` 页签选中/hover/kicker 已改中性；`TestAccentBudget` 5 例 | **成立** |
| V-06 新色板键 | DONE | `palettes.py` 四主题均有 `panel_primary` / `signal_dim` | **成立** |
| V-07 卡面权重 | DONE | `qss_build.py:143-150` 有 `#panelPrimary` / `#panelGhost` | **成立** |
| V-08 Primer 兜底 | DONE | `radar_chart.py` 兜底改 `PALETTES["deep"]`；`main_window` 的 `#888888` 已清 | **成立** |
| V-09 批次结论条 | DONE | 组件三态存在；**但 `set_ready` 零生产调用点**（见 E-2） | **口径需更正** |
| V-10 就绪清单 | **BLOCKED（Rev10 未交付）** | `gcm/app/batch_plan.py` **存在**，`plan_batch` 可调用（见 E-1） | **BLOCKED 不成立** |
| V-11 左缘刻度尺 | DONE | `edge_ruler.py` 已挂 `progress_table` 第 0 列 delegate | **成立** |
| V-12 历史按钮自绘 | TODO | `manage_model.py:324-331` 仍是 `style.drawControl(CE_PushButton)`；`:320` 仍是硬编码 `"查看"` | **TODO 成立** |
| V-13 动效 | DONE | `batch_conclusion.py` 有 220ms bar / 180ms pulse，`motion=False` 全跳过 | **成立** |
| V-14 3:1 输入区 | CUT（已记录） | `download_layout.py` 仍是上下结构，未做 3:1 | **CUT 成立** |
| V-15 动作三区 | TODO | `download_layout.py:290-350` 仍是两行平铺；无 ghost 样式 | **TODO 成立** |
| V-16 耗时列 | DONE | `progress_table.py:91-103,607-611`，6 列，末尾 index=5 | **成立** |
| V-17 管理页列宽 | TODO | `manage_panel.py:157-164` 仍是 3-7 列 Fixed 硬编码；无持久化 | **TODO 成立** |
| V-18 健康/大小列 | TODO | **G84-17 已加** 两列 ToolTip（`:216-223`）与「计算中…」三态；仅剩空值显示空串而非 `—` | **大部分已完成** |
| V-19 阶段时间线 | DONE | `repo_detail_dialog.py:97-105,364-371` 接 `stage_timeline` | **成立** |
| V-20 快捷填充编辑 | DONE | `main_window.py:1786` 调 `edit_quick_repos_inline` | **成立** |
| V-21 排序模式 | DONE | `progress_table.py:42,262-268` + `sort_mode.py` | **成立** |
| V-22 空态统一 | DONE(工厂) | 工厂存在；**只有 `progress_table` 一处生产调用**（见 E-4） | **口径需更正** |
| V-23 全角冒号 | DONE(零改动) | 抽查 `settings_groups.py` 全角统一 | **合理成立** |
| V-24 首启向导 | DONE | `onboarding.py` 有 kicker 铭牌 | **成立** |
### 1.4 并发改动与协调（执行前必读）

**事实**：2026-11-05 05:51–05:54，工作区出现了一批**本会话没有创建/修改**的文件与改动。
本会话只写了本文档与两个临时分段文件（已删除），没有碰 `gcm/`、`tests/` 下任何文件。

| 类型 | 文件 | 05:52 快照 | 05:54 实测 |
|------|------|-----------|-----------|
| 新增（未跟踪） | `gcm/app/undo.py`、`gcm/ui/menu_bar.py`、`gcm/ui/undo_bar.py`、`gcm/util/oplog.py`、`tests/test_rev12_usability.py` | 不存在 | 存在 |
| 修改 | `gcm/ui/main_window.py` | 2983 行 | **3110 行** |
| 修改 | `gcm/ui/manage_panel.py` | 656 行 | **678 行** |
| 修改 | `gcm/app/retry_queue.py`、`gcm/ui/download_tools.py`、`gcm/ui/help_dialog.py`、`gcm/ui/retry_panel.py` | 干净 | 有未暂存改动 |
| 规模 | `gcm/` | 131 文件 / 32,617 行 | **135 / 33,635** |
| 规模 | `tests/` | 190 / 36,810 | **191 / 37,220** |
| 异常预算 | except / pass-only | 728 / 284 | **750 / 287** |

**当前守卫状态（不是本计划造成的）**：

```text
$ python scripts/doc_drift_check.py
✗ CLAUDE.md §6：main_window.py 声称 2980，实测 3110
✗ docs/architecture.md §4：main_window.py 声称 2980，实测 3110
✗ README.md / CLAUDE.md / _EXPECTED_TEST_FILES：190 vs 实测 191
DRIFT 5
$ python -m unittest tests.test_g79_repo_hygiene
FAILED (failures=4)  # 190 != 191
```

**处置规则（写死，防误伤并行工作）**：

1. **不删、不改、不暂存**上述未跟踪文件与并行改动；它们不属于本计划。
2. 执行任何 R-*/P-* 之前，先 `git status --porcelain` + `scripts/dev_metrics.py` **重新冻结基线**；
   本文 §1.1 的 05:52 数字只作历史参考。
3. **文件级避让**：并行会话正在改 `main_window.py` 与 `manage_panel.py`，这两处不先动。
   可先做的、不重叠的子集是：R-03（`download_layout` / `qss_build`）、
   P-03（`event_panel`）、P-04（`failure_panel`）—— 但 R-01/R-02/R-04/R-05/R-06/P-01/P-02
   必须等并行会话收敛。
4. **守卫红灯不要用「改宽断言」处理**：190→191 是并行的真实变化，等它收敛后按真实数字
   三处同源同步；`main_window` 行数同理。谁的最后一次改动落盘，谁负责把数字改准。
5. 如果并行会话就是你要同时推进的另一条线，正确做法是**先让它提交/收口**，再开始本文的 W6；
   否则两边会互相覆盖，且截图无法归因。

---

## 2. 对既有文档的时效性判定（你本轮明确要求）

### 2.1 判定表

| 文件 | 是什么 | 判定 | 处置 |
|------|--------|------|------|
| `计划书/UI-视觉重设计-Rev11.md` | Rev11 设计与 W0–W5 任务书 | **部分过期**：设计原则仍有效；**5 处执行状态/事实失实** | 本文 §2.2 逐条更正；旧文档对应状态行在 R-07 同批修正，不重写全文 |
| `docs/验证记录-2026-11-04-Rev11.md` | 上一轮执行证据 | **基本成立，但边界清单里 V-10 的 BLOCKED 理由错误** | 追加「更正」节，不篡改历史 |
| `evidence/rev11-W*.json` | 节点证据 | **W5 的 doc_sync 数字已过期**（快照早于最后几行编辑） | 追加 `corrections` 字段，保留原始快照 |
| `docs/plans/.rev10-part1..5.md` | 另一批未跟踪草稿 | **主题不同、未执行**；其关键能力 `batch_plan.py` 实际已随 9.18.0 交付 | 不合并、不删除；旧文档 §1.5 的「Rev10 先 / Rev11 后」边界已失效，见 E-6 |
| `docs/archive/plans/20261023/UI视觉系统重构指南.md` | G75 归档计划 | 已归档只读 | 不复活 |

**结论：同主题已有 Rev11 文件，但它已不能直接当执行依据。本文是全新的、按 2026-11-05 代码事实校准的收尾计划。**

### 2.2 逐条更正（每条都有可复现证据）

#### E-1 · V-10「BLOCKED：`app/batch_plan.py` 不存在」是事实错误

**旧文档/证据的说法**：V-10 就绪清单依赖 Rev10 的 `app/batch_plan.py`，Rev10 未执行 → BLOCKED，idle 态显示 `—`。

**实际**：

```text
$ ls gcm/app/batch_plan.py
gcm/app/batch_plan.py
$ git log --oneline -3 -- gcm/app/batch_plan.py
a4f5006 feat(g84): 执行前预览、MCP 异步作业与资源、接线缺口与数据安全、质量门禁
$ grep -rn "plan_batch" gcm/cli.py gcm/mcp_server.py
gcm/cli.py:391:        from gcm.app.batch_plan import plan_batch
gcm/mcp_server.py:476:                from .app.batch_plan import plan_batch
```

`batch_plan.py` 在 **v9.18.0（G84）** 就随 `--dry-run` / MCP `clone_batch(dry_run=True)` 交付了，
比 Rev11 计划编写更早。`plan_batch(text, root) -> BatchPlan` 是纯本地、零网络、可重复调用的函数，
`BatchPlan.counts` 直接给出 clone/update/replace/blocked/duplicate/invalid 六档。

**结论**：V-10 **不是 BLOCKED**，是「数据源已存在但没接线」。R-01 负责接。
旧文档与验证记录必须改成这个口径，否则下一轮会继续把可做的活当阻塞。

#### E-2 · V-09 标 DONE，但第三态（就绪）在生产里不可达

**实际**：

```text
$ grep -rn "set_ready" gcm/ --include=*.py
gcm/ui/batch_conclusion.py:124:    def set_ready(...)
$ grep -rn "_conclusion_from_overview" gcm/ --include=*.py
gcm/ui/main_window.py:896:        # 数据槽接线见 `_conclusion_from_overview`（从既有批次信号取数，零引擎契约变更）。
```

`main_window.py:896` 的注释指向一个**不存在的方法**；`set_ready` 除定义外**零调用**。
也就是说：运行态 `begin()` 与终态 `finish()` 是真接线的（`:2135` / `:2654`），
但「就绪清单」只存在于单测里 —— 用户永远看不到 `9 将克隆 · 3 将更新`。
按本项目 `policy.md` 的口径，这不能叫「三态已完成」，只能叫「组件三态已实现 / 生产仅两态接线」。

**结论**：V-09 状态更正为 `DONE(组件) + 生产接线 TODO`；R-01 补齐 + 删掉那条假注释。
这也是本轮最值得修的一条：它直接决定结论条是不是「一个组件，三种状态」这个记忆点成立。

#### E-3 · V-18 标 TODO，但 G84-17 已经做了大部分

**实际**（`gcm/ui/manage_model.py`）：

- `:216-219` 健康列 ToolTip：解释 `0–5` 的构成与雷达图入口；
- `:221-222` 大小列 ToolTip：解释「尚未算出」与刷新方式；
- `:281-294` `mark_sizes_pending`：把一批行置为「计算中…」，区分「在算」与「坏了」。

所以「健康列无解释」「大小列空白无法判断」这两个原始问题**已经解决**。
仅剩的差异：真正未知时显示空串 `""`（`:210`），而 Rev11 计划要求显示 `—`。
**结论**：V-18 降级为「余量项」（R-06），不是一整项待办。

#### E-4 · V-22 标 DONE(工厂)，但只有 1/4 个界面接入

**实际**：

```text
$ grep -rn "build_empty_state" gcm/ --include=*.py
gcm/ui/empty_state.py:17:def build_empty_state(...)
gcm/ui/progress_table.py:157:        from .empty_state import build_empty_state
```

失败面板仍用自己的 `QLabel("暂无失败记录")`（`failure_panel.py:164`），
管理页仍用自己的 overlay `QLabel`（`manage_panel.py:185`），日志区没有任何空态。
**结论**：工厂成立，四处接入是真实的剩余工作（R-05）。

#### E-5 · V-12 / V-15 / V-17 确认为 TODO（不是文档漏更新）

| ID | 代码证据 | 现状 |
|----|---------|------|
| V-12 | `manage_model.py:320` `btn.text = "查看"`；`:331` `style.drawControl(CE_PushButton, ...)` | 仍是**原生按钮外观**，深色主题下渲染成浅色块（截图 `tab_manage_1440x900.png` 可见） |
| V-15 | `download_layout.py:290-350` 两行平铺；`grep "QPushButton#ghost" qss_build.py` 零命中 | 三区语义与 ghost 样式都不存在 |
| V-17 | `manage_panel.py:162-163` 五列 `Fixed` + 硬编码宽度；`grep "manage_columns\|ui_state" gcm/` 零命中 | 无自适应、无持久化 |

#### E-6 · 排期边界失效：「排在 v10.0.0 之后」与实际不符

旧文档 §0.2 D-4 写「独立专项，排在 v10.0.0 之后」，§1.5 写「Rev10 先、Rev11 后」。
实际：版本仍是 **9.18.0**，Rev11 的 W0–W5 已经在工作区执行完；`docs/plans/.rev10-*` 仍是未跟踪草稿。
**结论**：这条排期不再约束执行；本文把它替换为「Rev11-C 收尾 + W7 打磨」，
并明确 W7 不依赖任何未交付的 Rev10 能力。**不要再按「等 Rev10」拖延。**

#### E-7 · 数字漂移：文档在容差内、证据快照已过期

| 值 | 旧文档/证据 | 2026-11-05 实测 | 判定 |
|----|------------|----------------|------|
| `main_window.py` | 2980（CLAUDE/architecture）/ 2987（W5 evidence） | **2983** | 文档在 ±5 容差内；evidence 已过期 7 行 |
| `progress_table.py` | 794 / 794 | **795** | 同上（±1） |
| `result_labels.py` | 82（W5 evidence） | **87** | evidence 过期 5 行 |
| `gcm/ui/` 模块数 | 47（Rev11 §1.1 基线） | **55** | 正常增量；执行时必须用 55 |
| tests | 187 / 36,069 | **190 / 36,810** | 正常增量 |

另外 `CLAUDE.md:245-248` 存在一处**叙述歧义**：段首写 `main_window.py 2980`，
段尾历史链写「本版（X-4 布局修复）+3 行 → **2794**」。2794 是 Rev11 之前的历史节点，
但两数并列容易被读成矛盾。R-07 会把历史链补一句「Rev11 后 2983」，并让证据快照补 `corrections`。

> **`doc_drift_check.py` 是 ±5 行容差**，所以 2980 vs 2983 会 `DRIFT 0`。
> 这不代表数字精确 —— 本轮收尾时按实测改成精确值，别满足于「闸没红」。

---

## 3. 设计立场（不改，只把剩余缺口挂到原则上）

### 3.1 三原则仍是宪法（直接沿用旧 Rev11 §2.2）

1. **一个颜色只能说一件事**：`accent` 只表示「此刻正在发生」；页签选中、静态文字、装饰描边一律不用 accent。
2. **层级由字号 + 字族 + 间距表达，不靠颜色**：重要区块更大、更等宽、留更多空。
3. **数字是仪表读数，必须可纵向比较**：数量/时间/百分比/字节/SHA 走 `mono`，文字走 `body`。

**本轮不新增任何色板键、字族、动效类型。** 所有 W6/W7 改动只能消费既有的
`palettes` / `typography` / `qss_build` token；新增语义色一律从现有 `success/warning/error` 取。

### 3.2 剩余缺口 → 任务映射

| 旧诊断 | 问题还在吗 | 由哪项承接 |
|--------|-----------|-----------|
| V1 层级坍塌 | 首屏已有 `display`（结论条）；分组标题仍是 body | 不在本轮扩面（避免动输入区标题层级），W7 不碰 |
| V2 强调色通胀 | 已收敛 | 已完成 |
| V3 按钮墙 | **下载页仍是 8 个等权按钮；管理页更严重（6 个工具 + 3 个顶部）** | **R-03**（下载页）/ **P-01**（管理页） |
| V4 批次叙事被埋没 | 已置顶 + 三态 | 已完成 |
| V5 状态色圣诞树 | pill 已变淡 | 已完成 |
| V6 数据不是等宽 | 进度表已做；**管理页时间/HEAD/健康/大小仍比例字体、数字不对齐** | **P-02** |
| V7 历史列原生按钮 | 仍在 | **R-02** |
| V8 卡片等权 | 三档权重已落；3:1 CUT | R-03 只动动作区，不复活 3:1 |
| V9 管理页 9 列拥挤 | 仍硬编码列宽 | **R-04** |
| V10 动效零预算 | 已有两处 | 已完成 |
| V11 空态各自为政 | 仍只有下载页接入 | **R-05** |
| V12 Primer 兜底 | 已清 | 已完成 |

### 3.3 追加打磨（W7）的美学边界

W7 不是「再来一轮换皮」，只做三类**一致性**工作，全部有截图/代码证据：

1. **权重一致**：工具按钮在所有页面都应是 ghost，而不是管理页继续用一排实心等权按钮；
2. **读数一致**：管理页/事件流/失败面板的数字与时间戳必须和进度表同口径（mono + 右对齐）；
3. **语言一致**：管理页按钮与事件流的 `task_result` / `failed` 这类原始枚举值，不应直接暴露给中文用户。

**W7 明确不做**：不新增页面、不改导航结构、不换配色、不加动画、不动设置页表单密度。
任何一项如果实施中发现收益低于回归风险，允许逐条 CUT（P-* 编号独立，CUT 不影响其他项）。

---

## 4. W6 · Rev11 收尾（R-01…R-07）

> **执行铁律**：一次只做一个子批（W6-A…W6-F）；每个子批收口要有 `evidence/rev11c-<批>.json`。
> **任何新模块必须有生产调用点**（`test_g69_regressions.TestG69_18WiringClosure` 会抓孤儿）。
> **改动顺序按文件聚类**：同一文件的 R-02/R-04/R-06 放同一批，避免反复改同一处、反复截图。

### R-01 · 结论条就绪态接线（承接 V-10，修正 V-09 口径）

| 项 | 内容 |
|----|------|
| 目标 | 未开跑时，结论条显示「N 个待处理 · x 将克隆 · y 将更新 · [N 将删除重建]」，让用户在点「开始」前看到这批会干什么 |
| 落点 | `gcm/ui/batch_conclusion.py`（组件）、`gcm/ui/main_window.py`（接线）、`gcm/ui/download_layout.py:135-139`（防抖定时器） |
| 数据源 | `gcm/app/batch_plan.plan_batch(text, root) -> BatchPlan`，`.counts` 六档；**不得自建第二份计划逻辑**（`decisions.md` D-39） |
| 状态 | TODO |

**实施步骤（组件侧）**

1. 扩展 `set_ready`：签名改为
   `set_ready(*, clone, update, protected=0, blocked=0, replace=0, duplicate=0, invalid=0)`。
   `protected` 保留为兼容参数（默认 0），不再作为主展示项。
2. 主读数 `metric_lbl`：`total = clone + update + replace`；为 0 时仍显示 `—`（不显示假 0）。
3. 明细 `detail_lbl`：只拼**非零**档位，顺序固定：
   `x 将克隆 · y 将更新 · [n 需人工处理] · [n 重复] · [n 无效]`。
   **不显示 0 档**（旧实现无条件显示 `0 受保护 · 0 被拦截`，违反「空态不显示假 0」）。
4. 新增 `self.danger_lbl = QLabel("")`，`objectName="dangerCounter"`，放在主读数右侧；
   `replace > 0` 时显示 `tr("{n} 将删除重建").format(n=replace)`，颜色取
   `theme.current_palette()["warning"]`（满饱和），为 0 时 `hide()`。
   **不用字符符号**（⚠ 在微软雅黑缺字形），只用文字 + 语义色。
5. 新增 `reset_idle()`：`_state="idle"`，`metric_lbl="—"`，`detail_lbl=""`，隐藏 bar / review / danger。
6. 新增 `set_plan_unavailable(reason: str)`：`metric_lbl="—"`，`detail_lbl=reason`，
   用于「未选下载目录」「条目过多」两种诚实降级；**不编数字**。
7. `begin()` / `finish()` 里隐藏 `danger_lbl`（运行后它已无意义，危险档已进入执行）。

**实施步骤（接线侧）**

1. `main_window._refresh_url_validation()` 保持原职责不变；新增 `_refresh_conclusion_plan()`。
2. 在 `download_layout._build` 里，`_validate_timer.timeout` 同时连到两个槽
   （`_refresh_url_validation` 与 `_refresh_conclusion_plan`）；`target_edit.textChanged`
   也连到 `_on_input_changed`（同一 300ms 防抖）。
3. `_refresh_conclusion_plan()` 逻辑：
   - `if self.busy: return`（运行中不得改结论条）；
   - 取非空、非 `#` 注释行；为空 → `reset_idle()`；
   - `target = self.target_edit.text().strip()`；为空 → `set_plan_unavailable(tr("先选择下载目录"))`；
   - 行数 > **1000** → `set_plan_unavailable(tr("条目过多，开始后计算"))`（防 stat 风暴）；
   - 否则 `plan = plan_batch("\n".join(lines), target)`，按 `.counts` 调 `set_ready(...)`。
4. 删除 `main_window.py:896` 那条指向不存在方法的注释，改成指向 `_refresh_conclusion_plan`。
5. **异常策略**：不在 UI 层加 `try/except`。`plan_batch` 是纯函数，脏输入应在 app 层
   产出 `invalid` 行；若实测它抛异常，修 `app/batch_plan.py` 并补测试，**不许 UI 吞掉**
   （`test_g80_hygiene` 的异常预算棘轮只降不升）。

**守卫 / 测试**

- `tests/test_rev11c.py::TestReadyWiring`
  - 纯组件：三档计数、`replace>0` 显示危险标签且颜色取 warning、`replace=0` 隐藏、
    0 档不出现、`reset_idle`、`set_plan_unavailable`。
  - 接线：构造 `MainWindow`（沿用 `test_g76_wiring` 的 QApplication + tempdir 模式），
    临时目录里造三种目标（不存在 / `.git` 存在 / 非 git 目录），
    `repo_input` 填 3 行 → 调 `_refresh_conclusion_plan()` → 断言 `9 将克隆` 等文本。
  - `busy=True` 时不改；空输入回 `—`；>1000 行走降级文案。
- `tests/test_g69_regressions.py` 的 wiring 守卫：确认 `set_ready` **至少一个生产调用点**；
  新增断言 `assertNotIn("_conclusion_from_overview", src)`。
- `test_rev11_w2` 既有 8 例**不得改**（新参数都有默认值，向后兼容）。

**验收证据**

- 截图：1440×900 `deep` / `light` 各一张，输入 3 条（1 clone、1 update、1 replace）；
  1020×560 compact 一张，危险计数仍可见（单行模式）。
- `evidence/rev11c-w6a.json`：命令、计数、截图路径。
- 目检结论写清：危险计数是否满饱和、是否只出现一次、空态是否无假 0。

**风险与回滚**

- 风险：`plan_batch` 对 1000 行做 stat 可能卡 UI。缓解：300ms 防抖 + 1000 行硬上限；
  实测 300 行耗时写进 evidence。回滚：删接线，组件保留 idle 占位（回到现状）。
- 风险：结论条在未跑批次时覆盖上一批成绩单。约定：**只在用户改输入/改目录且未运行时**切就绪；
  不动输入时保留成绩单。测试要覆盖「finish 后不改输入 → 仍是 finished」。

---

### R-02 · 历史列「查看」按钮自绘 + i18n（V-12）

| 项 | 内容 |
|----|------|
| 目标 | 深色主题下不再出现浅色原生按钮；`"查看"` 走 `tr()`；点击行为与 sizeHint 不变 |
| 落点 | `gcm/ui/manage_model.py:299-351` |
| 状态 | TODO |

**实施步骤**

1. `paint()`：不再 `style.drawControl(CE_PushButton)`，改用 `QPainter`：
   - 圆角 4–6px（与 `TOKENS["radius"]` 同源）；
   - 底色 `palette["panel"]`，1px 描边 `palette["border"]`；
   - 文字 `tr("查看")`，颜色 `palette["text"]`，字体 `typography.type_font("body")`；
   - hover（`option.state & State_MouseOver`）时描边换 `palette["border_strong"]`，底色不变
     （不引入 accent，遵守原则一）。
2. `_option()` 去掉 `QStyleOptionButton` 依赖，改为返回文本/矩形；
   `sizeHint()` 固定 `56×24`（不再依赖 `QStyle.sizeFromContents`，避免原生样式差异）。
3. `btn.text = "查看"` → `tr("查看")`；`gcm/i18n/en.py` 补 `"查看": "View"`（若已存在则复用）。
4. `editorEvent()` 保持原逻辑（按下/松开 + `rect.contains`），不改成 `QAbstractItemView` 的 open 行为。

**守卫 / 测试**

- `TestHistoryDelegateSelfDrawn`：
  - 源码断言：`drawControl` 在该类中零出现（用 `inspect.getsource`）；
  - 行为：在 `deep` 色板下把 delegate paint 到 `QImage(70×30)`，采样中心/边框像素，
    断言**不是**默认按钮的浅色（例如像素亮度低于 120），且文字区域有非背景像素；
  - i18n：`tr("查看")` 在 zh/en 都有非空值。
- `tests/test_history_delegate.py` 既有 3 例**不得改**，全绿（`button_rect` / `editorEvent` / `sizeHint` 契约不变）。

**验收证据**

- `tab_manage_deep_1440x900.png` 截图：历史列按钮与表格底色同族，无浅色块。
- `evidence/rev11c-w6c.json`。

**风险与回滚**：纯绘制替换，回滚 = 恢复原 `paint` 三行；无数据/契约影响。

---

### R-03 · 动作三区语义（V-15，D-10：保留两行）

| 项 | 内容 |
|----|------|
| 目标 | 八个按钮不再等权：主操作 1 个反相块、批次控制 2 个描边、其余 ghost；不改变控件树与文案 |
| 落点 | `gcm/ui/download_layout.py:290-350`、`gcm/ui/qss_build.py` |
| 状态 | TODO |

**实施步骤**

1. `objectName` 规划（**不改任何 `owner.btn_*` 变量名，不改按钮文本**）：
   - `btn_start`：保持 `#primary`（唯一 cta）；
   - `btn_cancel` / `btn_pause_batch`：新增 `#secondary`（描边强调，禁用态弱化）；
   - `btn_clear` / `btn_open` / `btn_failures` / `btn_retry_queue` / `btn_diag_bundle`：`#ghost`。
2. `qss_build.py` 新增：
   - `QPushButton#secondary`：透明底 + `border_strong` 1px + `text`；hover 只换边框，不加 accent；
   - `QPushButton#ghost`：透明底 + 透明边框 + `text_dim`；hover 变 `text` + `border`；
   - disabled 态：`text_dim` 且不显示边框。
3. 第一行布局保持「主区 → 批次控制区 → 清空 → stretch」；
   **第二行整体右对齐**（把 `addStretch(1)` 移到行首），让工具区在视觉上远离主操作。
4. 用 `addSpacing(12)` 在区之间留呼吸，不加分隔线控件（少一个控件少一处裁切风险）。
5. **不改任何按钮文案**：`command_palette` 按 `QPushButton.text()` 去重，改文案会改 key。

**守卫 / 测试**

- `test_ui_compact_visibility`：11 个 owner 属性 × 9 尺寸**原样通过**（这是本项第一门禁）；
- `test_c2_ux` 忙态锚点不变；
- 新增 `TestActionZones`：断言 5 个 ghost + 2 个 secondary + 1 个 primary 的 objectName 集合；
  断言 8 个按钮的 `text()` 与改动前快照完全一致（防顺手改文案）；
- QSS 校验：`qss_validate_or_raise` 四主题全绿。

**验收证据**

- 截图：1440×900 `deep` / `light` / `nord` / `hc` 各一张 + 1020×560 compact 一张；
- 目检：主操作是否仍是唯一反相块；工具区在余光里是否退后；紧凑下是否零裁切。
- `evidence/rev11c-w6d.json`。

**风险与回滚**

- 风险：`#ghost` 让按钮在浅色主题对比不足。缓解：`text_dim` 对 `bg` 的对比度用现有
  `contrast_ratio` 断言 ≥ 4.5:1（`TestPaletteContrast` 已覆盖四主题键，新增选择器也走同一色键）。
- 回滚：删除 QSS 块与 objectName 赋值，布局骨架未变，`git checkout` 单文件即可。---

### R-04 · 管理页列宽自适应 + 持久化（V-17）

| 项 | 内容 |
|----|------|
| 目标 | 列宽不再硬编码；用户拖过的宽度重启后保持；1024×768 下不出现横向滚动条（路径列允许省略） |
| 落点 | `gcm/ui/manage_panel.py:155-164`、`gcm/db/settings.py`（新增字段）、`SettingsStore` 读写 |
| 状态 | TODO |

**实施步骤**

1. 列策略：
   - 第 0 列（仓库）与第 2 列（本地路径）保持 `Stretch`，吸收窗口宽度变化；
   - 第 1 列（作者/仓库）与 3–7 列改 `Interactive`，给初始宽度：
     `1=200, 3=150, 4=78, 5=70, 6=64, 7=84`；
   - `setStretchLastSection(False)`（否则最后一列会把用户拖的宽度吃掉）。
2. 新增设置字段（`gcm/db/settings.py`，紧跟 `geometry` / `last_tab` 之后）：
   ```python
   manage_columns: tuple = ()  # G? / Rev11-C：管理页可拖列宽度（8 个整数，空=默认）
   ```
   `_coerce()` 对 tuple 字段已有「list/tuple 互通，逐项转 str」的逻辑，无需改。
3. `manage_panel`：
   - `_load_manage_columns()`：读 `settings.manage_columns`；长度不为 8、值不在 `32..600` 的项忽略；
     应用宽度时置 `self._applying_columns = True`，避免触发保存回环；
   - `_on_header_resized(logical, old, new)`：`sectionResized` 信号 → 400ms 单次 `QTimer` 防抖 →
     收集 8 列宽度 → 写 `settings.manage_columns` → `SettingsStore.save()`（已带跨进程锁）；
   - 只在值真的变化时保存（避免每次启动都写盘）。
4. 初始化顺序：先设 `ResizeMode` 与默认宽度，再 `_load_manage_columns()`，最后连 `sectionResized`。
5. 兼容：旧 `settings.json` 无该键 → 默认空 tuple → 用内置宽度；保存失败沿用 `SettingsStore`
   既有异常路径（UI 记日志，不静默吞）。

**守卫 / 测试**

- `TestManageColumnPersistence`：
  - 设置宽度 → 触发防抖槽 → 新 `SettingsStore` 重新加载 → 断言宽度保持；
  - 脏值（`"9999"`、`"abc"`、长度 3）→ 回退默认，不抛异常；
  - 1024×768：`horizontalScrollBar().isVisible()` 为 `False`（Stretch 列吸收）；
  - 写侧与读侧都存在（源码/行为双断言，防「只写不读」）。
- `test_g76_wiring` 的 settings 持久化模式可复用；新增字段不改变既有用例。
- `test_g79_repo_hygiene` 的模块行数：`manage_panel.py` 656 → 预计 ≈ 720，仍 < 800。

**验收证据**

- 截图：1024×768 与 1440×900 各一张，表头列宽可读、无横向滚动；
- `evidence/rev11c-w6c.json` 记录「设置 → 重启（重新构造窗口）→ 宽度保持」的行为。

**风险与回滚**

- 风险：新设置字段没接线 = 用户改了没用（项目红线）。缓解：写读两侧都有测试。
- 风险：`sectionResized` 在窗口初始化时也会触发，可能把默认宽度写进 settings。缓解：
  `_applying_columns` 标志 + 值变化比较。
- 回滚：删字段与读写逻辑，回到固定宽度；`settings.json` 多出的键会被 `_coerce` 忽略。

---

### R-05 · 空态四处接入（V-22）

| 项 | 内容 |
|----|------|
| 目标 | 下载页/管理页/失败面板/日志区的空态文案与视觉同源，不再各自为政 |
| 落点 | `gcm/ui/empty_state.py`、`manage_panel.py:185-191`、`failure_panel.py:164-167`、`settings_panel.py:61-67`（日志视图） |
| 状态 | TODO |

**实施步骤**

1. 在 `empty_state.py` 增加**文案常量**（全部走 `tr()`）：
   - `EMPTY_DOWNLOAD`（已有调用方，保留）；
   - `EMPTY_MANAGE = ("尚未导入仓库", "点击「导入本地已有仓库」开始")`；
   - `EMPTY_FAILURES = ("暂无失败记录", "失败会在任务结束后按类别聚合到这里")`；
   - `EMPTY_LOG = "暂无日志；开始任务后这里会实时滚动"`；
   - `EMPTY_EVENTS = ("暂无事件", "任务开始后这里会显示执行轨迹")`（P-03 使用）。
2. 管理页：把 `QLabel("尚未导入仓库\n...")` 换成 `build_empty_state(*EMPTY_MANAGE, parent=self.manage_table)`；
   **保留属性名 `self._empty_manage`**（`test_g36_ui.py:157` 直接读它），只换构造方式与 objectName。
3. 失败面板：把 `QLabel(tr("暂无失败记录"))` 换成 `build_empty_state(*EMPTY_FAILURES)`；
   **保留属性名 `self.empty_lbl`**（`test_g52_failure_panel.py:75` 直接读它）。
4. 日志区：`QPlainTextEdit` 用 `setPlaceholderText(EMPTY_LOG)`。QPlainTextEdit 原生占位符
   比叠加 QLabel 更稳（不参与布局、不需要 resize 跟随），但文案来自同一常量，仍是「唯一来源」。
5. 下载页 `progress_table` 的既有空态保留，只把文案常量迁移到 `empty_state`（行为不变）。

**守卫 / 测试**

- `TestEmptyStateSurfaces`：四个界面分别构造/显示，断言空态文案来自 `empty_state` 常量
  （源码断言常量被引用 + 行为断言标签文本/占位符文本一致）；
- `test_g36_ui.py`、`test_g52_failure_panel.py` 既有断言**不得改**；
- `test_rev11_w4.TestEmptyState` 工厂三例保持全绿。

**验收证据**

- 截图：管理页空库、失败面板无失败、日志区 0 条、下载页空表；
- `evidence/rev11c-w6e.json`。

**风险与回滚**：纯文案/构造替换；回滚 = 恢复各自 QLabel，无逻辑影响。

---

### R-06 · 大小列空态余量（V-18 residual）

| 项 | 内容 |
|----|------|
| 目标 | 大小列真正未知时显示 `—`，与「计算中…」「具体值」形成三态 |
| 落点 | `gcm/ui/manage_model.py:210` |
| 状态 | TODO |

**实施步骤**

1. `data()` 的 `col == 7` 返回 `r.size or "—"`（`r.size` 为「计算中…」时原样返回）。
2. 不改 `set_size` / `mark_sizes_pending` 的写入逻辑。
3. 确认 ToolTip（`:221-222`）仍解释「尚未算出」；显示 `—` 后 tooltip 文案仍准确。

**守卫 / 测试**

- `TestSizeColumnThreeStates`：空值 → `—`；`mark_sizes_pending` → 「计算中…」；`set_size` → 具体值；
- ToolTip 三态都非空；
- 既有管理页用例不因 `—` 改变而红（若有用例断言 `""`，同批更新并在 PR 说明）。

**验收证据**：`tab_manage_deep_1440x900.png` 中大小列不再是纯空白；`evidence/rev11c-w6c.json`。

**风险**：`—` 与「计算中…」宽度不同，可能导致列宽轻微变化；列宽已是 Interactive/持久化，可接受。

---

### R-07 · 文档、台账与证据纠错（收口必做）

| 项 | 内容 |
|----|------|
| 目标 | 让仓库里的文字与代码一致；不让这一轮的「假 BLOCKED」继续误导下一轮 |
| 落点 | 旧 Rev11 文档状态行、验证记录、`evidence/rev11-W5.json`、`test_ledger.md`、`CLAUDE.md`/`docs/architecture.md` 行数、changelog 碎片 |
| 状态 | TODO |

**实施步骤**

1. **旧 Rev11 文档**（只改状态与事实，不改设计章节）：
   - V-09：`DONE` → `DONE(组件) + 生产接线由 R-01 完成`；
   - V-10：`BLOCKED` → `TODO（数据源已存在，R-01 接线）`，并在条目下注明 E-1 的更正；
   - V-18：`TODO` → `DONE(实质，G84-17) + 余量 R-06`；
   - V-22：`DONE(工厂)` → `DONE(工厂) + 四处接入 R-05`；
   - V-12/V-15/V-17：状态改为 `TODO（Rev11-C 承接）`。
2. **验证记录**：在 `docs/验证记录-2026-11-04-Rev11.md` 末尾追加「2026-11-05 更正」一节，
   列出 E-1…E-7 与本文路径；**不删原文**，保留审计轨迹。
3. **evidence 快照**：给 `evidence/rev11-W5.json` 追加 `corrections` 字段：
   `{"main_window_lines": 2983, "progress_table_lines": 795, "result_labels_lines": 87,
     "reason": "快照早于最后几行编辑；2026-11-05 实测"}`。**不改原 changes 数组**。
4. **接缝注册表**：`test_ledger.md` 新增
   - `S50`：`batch_plan.counts → BatchConclusionBar.set_ready`（就绪计数唯一来源）；
   - `S51`：`settings.manage_columns ↔ 管理页表头`（写读两侧）。
5. **数字同步**：`CLAUDE.md` 与 `docs/architecture.md` 的行数改为实测精确值；
   `CLAUDE.md` 的历史链补一句「Rev11 后 2983」，消除 2794/2980 的并列歧义；
   测试文件数若新增 `tests/test_rev11c.py` → 190→191，三处同源（README/CLAUDE/`_EXPECTED_TEST_FILES`）一起改。
6. **changelog 碎片**：`python scripts/changelog.py new --category fixed --slug rev11c-closure --title "..." --verify "..."`。
7. 收口跑 `python scripts/doc_drift_check.py`，必须 `DRIFT 0`。

**守卫 / 测试**：`test_g79_repo_hygiene`（白名单、测试文件数）、`test_doc_drift_guard`、
`test_g67_docs_freshness`（不扫根级 `计划书/`，但 `CLAUDE.md` 在其中，改数字要同步）。

**风险**：改 `CLAUDE.md` 的测试文件数会牵动三处；漏一处就红。按项目既有流程「三处同源」一起改。

---

## 5. W7 · 追加打磨（P-01…P-04，你本轮选择加做）

> W7 的前提：**W6 全部收口后再动**。理由：W7 会再次改 `manage_panel` / `manage_model` /
> `event_panel` / `failure_panel`，若与 W6 并行会互相踩文件、截图也无法归因。
>
> W7 的每一项都可以独立 CUT，不影响 W6 的交付。

### P-01 · 管理页动作区权重（V3 的同型问题）

| 项 | 内容 |
|----|------|
| 证据 | 截图 `tab_manage_1440x900.png`：顶部三个大按钮（一键更新全部 / 取消全部 / 刷新列表）等权；下面六个工具按钮（导出元数据/导入元数据/导入本地已有仓库/删除选中记录/打标签/导出所选）一排等权；其中「刷新列表」是纯工具动作，却和主操作一样大 |
| 目标 | 每屏唯一 cta（一键更新全部）；取消全部为 secondary；刷新列表降为 ghost；六个工具按钮统一 ghost |
| 落点 | `gcm/ui/manage_panel.py:83-127`、`qss_build.py`（复用 R-03 的 `#secondary` / `#ghost`） |
| 状态 | TODO |

**实施步骤**

1. `btn_refresh`：`setObjectName("ghost")`；去掉大按钮的最小高度/拉伸策略（若沿用顶部行布局）。
2. `btn_cancel_manage`：`setObjectName("secondary")`。
3. 六个工具按钮统一 `setObjectName("ghost")`；「导入本地已有仓库」在**空态**里是主入口，
   空态按钮单独保留 `#primary`（空态与表格互斥，不构成同屏双 cta）。
4. `tr()`：本轮顺带把管理页 10 个硬编码按钮文案包 `tr()`（`"一键更新全部"` 等），
   `gcm/i18n/en.py` 补齐；**zh 文案一字不改**，所以 `command_palette` 的 key 不变。
5. 不动按钮变量名、不动信号连接、不动 `manage_panel` 的业务方法。

**守卫 / 测试**

- `TestManageActionWeights`：断言 1 个 primary + 1 个 secondary + 其余 ghost 的集合；
- `command_palette`：既有去重/文本集合不变（zh 下 `tr()` 返回原中文）；
- `i18n_coverage`：新增 key 双语齐全；
- `test_g76_wiring` 管理页相关用例全绿。

**验收证据**：管理页 1440/1024 截图；`evidence/rev11c-w7a.json`。

**风险**：ghost 按钮在深色主题可能“太隐形”。缓解：`text_dim` 对比度 ≥4.5:1，hover 变 `text`；
目检四主题。回滚：删除 objectName 赋值。

---

### P-02 · 管理页数据列排版（V6 余量）

| 项 | 内容 |
|----|------|
| 证据 | 截图：上次同步 / HEAD / 健康 `5/5` / 大小 都是比例字体；数字列左对齐，纵向不可比 |
| 目标 | 3/4/6/7 列走 `mono`；6/7 数字右对齐；表头不改文案 |
| 落点 | `gcm/ui/manage_model.py`（新增 `FontRole` / `TextAlignmentRole`）、`typography.type_font` |
| 状态 | TODO |

**实施步骤**

1. 在 `ManageModel` 初始化时缓存 `self._mono_font = type_font("mono")`（**不要每行每列新建 QFont**）。
2. `data()` 新增：
   - `FontRole`：`col in (3,4,6,7)` → `self._mono_font`；其他列返回 `None`（继承视图字体）；
   - `TextAlignmentRole`：`col in (6,7)` → `AlignRight | AlignVCenter`；`3/4` → `AlignLeft | AlignVCenter`。
3. 表头 `setDefaultAlignment(AlignLeft)` 保持不变；列标题已是 `tr()`。
4. 不把整表字体改成 mono —— 仓库名/路径是标识符，按 Rev11 §2.4 也应 mono，但
   **本轮只改数据列**，避免整页等宽化带来的可读性回归；仓库名保持现状，记为后续可选。

**守卫 / 测试**

- `TestManageDataTypography`：`FontRole` 的 families 含 `Cascadia Mono`；对齐角色正确；
- 行高/列宽不因字体变化破 34px（截图目检 + `sizeHint` 断言）；
- 四主题 `contrast_ratio(text, bg)` 不变（字体不改色）。

**验收证据**：管理页 1440/1024 截图（数字对齐可读）；`evidence/rev11c-w7b.json`。

**风险**：Cascadia Mono 在 CI Linux/macOS 不存在 → 字族链回退 Consolas/monospace；
测试只断言**字族链**，不断言字体存在（与 `test_g75_visual_system.TestFontFamilies` 同口径）。

---

### P-03 · 事件流对话框结构化（黑匣子可读性）

| 项 | 内容 |
|----|------|
| 证据 | 截图 `dialog_event_stream.png`：序号/时间/类型用比例字体；类型列直接显示 `task_result`；状态列 `failed` / `success` 是英文枚举；无空态 |
| 目标 | 序号/时间/类型走 mono；事件类型与状态中文化；状态列用语义色；0 事件时显示统一空态 |
| 落点 | `gcm/ui/event_panel.py:33,121-122,172-178,189`、`result_labels.py`（状态文案复用） |
| 状态 | TODO |

**实施步骤**

1. 新增纯函数 `event_type_label(raw: str) -> str`：把 `EventType` 全部 16 个值映射到中文
   （`batch_started`→`批次开始`、`task_result`→`任务结果`、`task_error`→`任务错误`、
   `retry_dropped`→`重试放弃` 等），未知值回退原始字符串。走 `tr()`。
2. 状态列复用 `result_labels.status_label(raw)`（唯一来源，不抄第二份）；`failed/success/conflict` 变中文。
3. 表格：`apply_type(self.table, "mono")` 只对 0–2 列生效不可行（QTableWidget 无按列字体），
   改为 `setItem` 时对 0–4 列 `item.setFont(mono_font)`，第 5 列摘要保持 body。
4. 状态列 `ForegroundRole`：`success`→`palette["success"]`，`failed/conflict`→`palette["error"]`，
   `pending/running`→`text_dim`；**不要整行染色**（原则一）。
5. 空态：0 行时显示 `build_empty_state(*EMPTY_EVENTS)` overlay（同管理页做法），有行时隐藏。
6. 不改变自动刷新、暂停、复制 JSON、过滤逻辑。

**守卫 / 测试**

- `TestEventTypeLabel`：16 个 EventType 全覆盖 + 未知值回退；
- `TestEventStatusLabel`：复用 `status_label`，不得出现第二份映射（源码断言）；
- 空态显隐；
- 既有无头测试 `test_g71_event_stream` 不因显示层改动而红（事件载荷不变）。

**验收证据**：`dialog_event_stream.png` 更新版（中文类型/状态、等宽时间）；`evidence/rev11c-w7c.json`。

**风险**：`EventType` 是 SSE 的 `event:` 名称，**绝不能改常量值**；只改显示层映射。
回滚：删除 label 映射，回到原始字符串。

---

### P-04 · 失败对话框读数排版

| 项 | 内容 |
|----|------|
| 证据 | 截图 `dialog_failure.png`：「失败次数」左对齐、比例字体；类别列与操作列权重接近；空态未走统一工厂 |
| 目标 | 次数列 mono + 右对齐；类别列固定宽；空态接入 R-05 的统一工厂 |
| 落点 | `gcm/ui/failure_panel.py:164-167,198-210` |
| 状态 | TODO |

**实施步骤**

1. `失败次数` 列：`QTableWidgetItem` 设 mono 字体 + `TextAlignmentRole` 右对齐。
2. 类别列宽度固定（约 110px），「原因与处理」列 `Stretch`，操作列 `ResizeToContents`。
3. 空态已经在 R-05 换成工厂；本项只验证文案与视觉一致，不重复改。
4. 操作按钮文案已是 `tr()`（检查 `重试该类别`/`去设置`/`诊断包`），缺的补 i18n。

**守卫 / 测试**

- `TestFailurePanelTypography`：次数列字体/对齐；
- `test_g52_failure_panel` 既有用例全绿；
- 空态与 R-05 的共享常量一致。

**验收证据**：`dialog_failure.png` 更新版；`evidence/rev11c-w7d.json`。

**风险**：低；纯显示层。回滚：恢复 `setItem` 默认。---

## 6. 守卫变更账（改哪些、凭什么不算削弱断言）

### 6.1 现有守卫会怎么拦我

| 守卫 | 它钉死了什么 | 本计划会踩到吗 | 处置 |
|------|------------|--------------|------|
| `test_ui_compact_visibility` | 11 个 owner 属性 × 9 尺寸 `visibleRegion` 非空 | **会**（R-03 动作区） | **不改守卫**：11 个控件保留原名、原控件类型、原文案；只加 objectName 与样式 |
| `test_c2_ux` | 忙态锚定 `btn_diag_bundle` 等按钮 | **会**（R-03） | 锚点控件不动 |
| `command_palette` 去重 | 按 `QPushButton.text()` 收集动作 | **会**（R-03 / P-01 加 `tr()`） | **zh 文案一字不改**；`tr()` 在 zh 下返回原文，key 不变 |
| `test_g46_nav_batch` | 精确断言 `batch_overview` 的 `"已完成 5/5 · 成功率 60%"` | **不会**（R-01 只改 `batch_conclusion`） | 保留 `batch_overview` 原样 |
| `test_g75_visual_system` | 七级阶梯、字族链、四主题对比度、`TestAntiGenericAesthetics` | **会**（P-01/P-02 新样式/字体角色） | 对比度断言**一条不加宽**；不新增色值；只消费既有 token |
| `TestNoTofuGlyphsInUi` | 状态不得用字符符号 | **会**（R-01 危险计数） | 只用文字「N 将删除重建」+ 语义色，不用 ⚠ |
| `test_g79_repo_hygiene` | 根目录白名单 + 超 800 行集合 + `_EXPECTED_TEST_FILES` | **会**（新增测试文件） | 新增**一个** `tests/test_rev11c.py` → 190→191，三处同源一起改 |
| `test_function_size_budget` | >100 行函数集合相等 | **会**（R-04 容易把函数写长） | 拆小函数；不更新集合（除非实在拆不开，需在报告里解释） |
| `test_g80_hygiene` | `except` 728 / pass-only 284 棘轮 | **会**（R-01 想加 try/except） | **不新增 except**；脏输入修 app 层，不在 UI 吞 |
| `test_g69_regressions.TestG69_18WiringClosure` | 只在 tests 出现 = 虚伪实现 | **会**（R-01 的 `set_ready`） | 必须加生产调用点；新增源码断言防回退 |
| `test_g67_docs_freshness` | `docs/**`、README、CLAUDE.md 的版本/路径引用 | **会**（R-07 改 CLAUDE.md 数字） | 只改真实数字，不写目标版本号 |
| `test_g66_patch`（subprocess encoding） | 文本模式必须显式 `encoding` | 不会（无新子进程） | — |
| `test_g76_wiring` | 设置项写侧与读侧都要有 | **会**（R-04 新字段） | 写读两侧都加测试 |
| `i18n_coverage` | zh/en key 对齐 | **会**（P-01/P-03 新文案） | 新 key 同时进 zh/en |

### 6.2 允许的守卫变更（白名单）

只有以下三条允许改，且必须在最终报告里逐条说明理由：

1. `_EXPECTED_TEST_FILES` 190 → 191（新增 `tests/test_rev11c.py` 的**事实变化**）；
2. `README.md` / `CLAUDE.md` 里的测试文件数同步 190 → 191；
3. `CLAUDE.md` / `docs/architecture.md` 的行数表改为实测精确值（在 ±5 容差内，但本轮要求精确）。

**禁止**：为让测试变绿而删用例、跳过用例、放宽对比度阈值、mock 生产逻辑、把断言改成 `assertTrue(True)`。

### 6.3 新增守卫（本轮必须补的）

| 守卫 | 防什么复发 |
|------|-----------|
| `TestReadyWiring` | 组件三态只存在于单测、生产只有两态（E-2 同型） |
| `TestNoStaleWiringComment` | 注释指向不存在的方法（`_conclusion_from_overview` 同型） |
| `TestActionZones` | 重排动作区时改了按钮文案/变量名/可见性 |
| `TestHistoryDelegateSelfDrawn` | 回退到 `drawControl` 原生按钮 |
| `TestManageColumnPersistence` | 新设置项「只写不读」或「只读不写」 |
| `TestEmptyStateSurfaces` | 空态又各自为政 |
| `TestManageDataTypography` | 数字列回到比例字体/左对齐 |
| `TestEventTypeLabel` | 事件类型映射漏项或第二份状态文案 |

---

## 7. 验收矩阵（按风险分层，不机械全跑）

### 7.1 每个子批（W6-A…W6-D / W7-A…W7-C）

| 层级 | 手段 | 判定 |
|------|------|------|
| L0 静态 | `ruff check gcm` + `mypy gcm` | 0 error / Success |
| L0 文档 | `python scripts/doc_drift_check.py` | `DRIFT 0`（只有改了行数/测试数才需要） |
| L1 定向 | 本子批新增/改动的 `tests/test_rev11c.py` 用例 + 相邻模块 | 全绿；每条断言都能失败（反向自检） |
| L2 视觉 | `scripts/ui_screenshot_audit.py` 产出 34 张，**只目检本子批涉及的页面** | 逐张写结论，不写「看起来没问题」 |
| L3 响应式 | `test_ui_compact_visibility` | 9 尺寸全绿（每个子批结束都跑，因为它最容易被布局改动打红） |

### 7.2 W6 收口（R-07 之前）

| 层级 | 手段 | 判定 |
|------|------|------|
| L1 全量定向 | `test_rev11_visual` / `test_rev11_w2` / `test_rev11_w4` / `test_rev11c` / `test_g75_visual_system` / `test_g76_wiring` / `test_g79_repo_hygiene` / `test_ui_compact_visibility` / `test_g36_ui` / `test_g52_failure_panel` / `test_history_delegate` | 全绿 |
| L2 视觉 | 34 张最终截图 + 4 主题关键页灰度图 | 左缘刻度尺/状态形状/危险计数在灰度下仍可辨 |
| L4 回归 | `coverage run --source=gcm scripts/ci_run_fast.py` | 0 失败 + 覆盖率 ≥ Windows 81 |
| 文档 | `doc_drift_check` + `i18n_coverage` | `DRIFT 0` / `I18N OK` |

> **截图脚本的已知行为**：`scripts/ui_screenshot_audit.py` 在 offscreen 下**会以 `0xC0000409` 退出**，
> 但 34 张 PNG 已完整落盘（Rev11 W0 证据 `rev11-W0-exec.json` 记录过同型）。
> 判定标准是**产物完整 + 逐张目检**，不是退出码 —— 但必须在 evidence 里如实写「退出码非 0，产物 34 张」。

### 7.3 W7 收口

| 层级 | 手段 | 判定 |
|------|------|------|
| L1 定向 | `test_rev11c.py` 的 P-* 用例 + 相邻模块 | 全绿 |
| L2 视觉 | 管理页 / 事件流 / 失败面板截图（4 主题中至少 deep + light） | 权重、对齐、语言三项一致 |
| L4 回归 | 快集全量 | 0 失败 + 覆盖率 ≥ 81 |
| L6/L7 打包 / CI | **本轮不做**，除非你明确要发版 | 发版前按 `docs/RELEASE_CHECKLIST.md` 另跑 |

### 7.4 明确不做的验证（避免假绿）

- 不跑真实克隆 E2E：本轮不改引擎/网络；结论条数据来自纯本地 `plan_batch`。
- 不跑 PyInstaller：不改依赖/入口；发版前另跑。
- 不跑 CI 三平台：未提交、未推送；按保守策略等你授权。

---

## 8. 风险登记与回滚

| 风险 | 概率 | 影响 | 缓解 | 回滚 |
|------|------|------|------|------|
| `plan_batch` 在大输入下卡 UI | 中 | 中 | 300ms 防抖 + 1000 行硬上限 + 实测 300 行耗时写 evidence | 删接线，回到占位 `—` |
| 就绪态覆盖上一批成绩单 | 中 | 低 | 只在「用户改输入/目录且未运行」时切；finish 后不动输入保持成绩单 | 删 textChanged 连接 |
| 新增设置字段只写不读 | 中 | 中 | 写读两侧测试 + `test_g76_wiring` 同型断言 | 删字段，回固定列宽 |
| `sectionResized` 初始化时写盘 | 中 | 低 | `_applying_columns` + 值变化比较 + 400ms 防抖 | 删保存连接 |
| `#ghost` 在浅色/hc 主题太淡 | 中 | 中 | `text_dim` 对比度 ≥4.5:1 断言 + 四主题目检 | 删除 objectName/QSS 块 |
| 管理页数字列改 mono 后列宽变化 | 低 | 低 | 列宽已 Interactive/持久化；1024 截图确认 | 去掉 FontRole |
| 事件类型中文化改了 SSE 契约值 | 低 | 高 | **只改显示层映射**，`EventType` 常量零改动；测试断言原值不变 | 删映射 |
| 测试文件数/行数三处不同步 | 中 | 低 | R-07 一次性改三处 + `doc_drift` | 回滚文档改动 |
| 顺手改按钮文案破坏命令面板 | 低 | 中 | `TestActionZones` 快照断言按钮 text 不变 | 恢复文案 |
| 截图脚本退出码误导判定 | 中 | 低 | 判定「产物 34 张 + 目检」，evidence 如实记录退出码 | — |

---

## 9. 明确不做（替你挡掉，附理由）

| 不做 | 理由 |
|------|------|
| 换技术栈 / QML / Web 前端 | 打包体积与回归面不划算；桌面单机工具没必要 |
| 拆 `main_window.py`（2983 行） | 已登记为**显式接受的技术债**（`CLAUDE.md` §6）；本轮新增 UI 一律落独立模块 |
| 新增色板键 / 字体 / 动效类型 | 设计系统已经够用；再扩只会稀释 Instrument 的读数纪律 |
| 复活输入区 3:1 分栏（V-14 CUT） | 上一轮已按「收益 < 回归」CUT；本文不推翻 |
| 加 `health_card` 冗余列 | 评分由 `sync_history` 实时派生是唯一事实源（G53-1 结论） |
| 把 `replace` 并进「将克隆」 | 会在界面上抹平唯一会删目录的动作（D-11 已否决） |
| 整页等宽化（含仓库名/路径） | 本轮只改数据列，避免可读性回归；仓库名等宽记为后续可选 |
| 删逐行 1.2s 完成消隐 | 是用户可见设置项 `ck_animations`，删=功能回退（G75 D4） |
| 自动打包 / 发版 / 推 CI | 未获授权；按保守策略只做本地验证 |
| 把旧 `计划书/` 归档删除 | 本文执行完才满足归档条件；先保留追溯 |

---

## 10. 执行顺序、纪律与停止条件

### 10.1 推荐顺序（按文件聚类，避免互相踩）

| 批次 | 内容 | 为什么这个顺序 | 完成信号 |
|------|------|--------------|---------|
| **W6-A** | R-01（结论条接线） | 独立文件；最有价值的一条；先做能最早暴露 `plan_batch` 成本问题 | `test_rev11c.py::TestReadyWiring` 全绿 + 2 张截图 |
| **W6-B** | R-02 + R-04 + R-06（管理页簇） | 同一批文件（`manage_model` / `manage_panel` / `settings`），一次改完、一次截图 | 管理页测试 + 重启保宽 + 三态用例 |
| **W6-C** | R-03（动作三区） | 只动 `download_layout` + `qss_build`；与 W6-B 无文件重叠 | 11 控件 × 9 尺寸 + 4 主题截图 |
| **W6-D** | R-05（空态四处） | 跨 3 个面板，放最后减少重复截图 | 四处空态 + 既有用例 |
| **W6-E** | 收口：R-07 + 快集 + 34 张截图 + changelog | 所有代码稳定后才写最终数字 | `DRIFT 0` + 快集 0 失败 + 覆盖率 ≥81 |
| **W7-A** | P-01 + P-02（管理页打磨） | 同一批文件 | 管理页截图 + 权重/排版测试 |
| **W7-B** | P-03（事件流） | 独立对话框 | 事件流截图 + 映射测试 |
| **W7-C** | P-04 + W7 收口 | 失败面板 + 全量回归 | 快集 0 失败 |

**依赖**：W6-A 不依赖任何未交付能力；W6-B 依赖 R-01 的结论条已稳定（避免同屏视觉冲突）；
W7 全部依赖 W6-E 收口完成。

### 10.2 纪律（照抄 `policy.md`，不要问为什么）

1. **未运行 ≠ 通过**。「完成」必须附本轮命令 + 退出码 + 输出尾部。
2. **一个子批一收口**。收口 = 代码 + 单测 + 截图 + `evidence/rev11c-<批>.json` + 验证记录一节。
3. **不接受「基本完成」**。状态只有五种（§0.4）。
4. **不许为绿灯削弱断言**（§6.2 白名单外一律禁止）。
5. **新模块必须有生产调用点**。
6. **同一映射只允许一份实现**（`decisions.md` D-39）：状态文案、事件类型、批次计划都不得手抄第二份。
7. **遇到未拍板的语义选择必须停下来问**；本轮的 D-9/D-10/D-11 已拍板，照办不问。

### 10.3 停止条件

**可停止并交付**：所有 R-* / P-* 状态为 `DONE` 或 `CUT（附理由）`；无未解决 P0/P1；快集 0 失败；
覆盖率 ≥ 门禁；`ruff` / `mypy` / `doc_drift` 全绿；剩余风险已披露。

**停下来报告阻塞**（而不是无限尝试）：

- `plan_batch` 在 300 行实测耗时 > 300ms 且无法通过缓存/限制解决；
- 管理页列宽持久化与 `SettingsStore` 的跨进程锁冲突，且两次替代方案都失败；
- ghost 样式在 `hc` 主题无法达到 4.5:1 且不能用既有色键解决；
- 任何改动触发 `test_ui_compact_visibility` 且无法在不裁切的前提下修复。

### 10.4 残留决策（执行到对应项再问，不提前替你做）

| ID | 决策点 | 我的建议 | 何时问 |
|----|--------|---------|--------|
| `Q-1` | `plan_batch` 自动计算的输入上限 | **1000 行**（超出走诚实降级文案） | R-01 开工前（若你不同意） |
| `Q-2` | W7 是否全部做，还是先做 P-01/P-02 | 先 P-01/P-02（管理页最显眼），P-03/P-04 视时间 | W6 收口后 |
| `Q-3` | 是否提交/推送本轮改动 | **不提交**，等你明确授权 | W6/W7 收口后 |

---

## 11. 交付前反向自检（实施完成后逐条过）

1. **需求有没有偷换？** W6 六项是否逐条落地，还是把「BLOCKED」又当成了理由？
2. **链路是否真打通？** `set_ready` 是否有生产调用点？`manage_columns` 写读两侧是否都通？
3. **异常/边界是否遗漏？** 空输入、只有无效行、>1000 行、目录不存在、busy 中改输入。
4. **会不会破坏原有逻辑？** `batch_overview` 文本、11 控件可见性、命令面板 key、`test_g52` 空态属性。
5. **新环境能否跑起来？** 新设置字段对旧 `settings.json` 是否向后兼容（缺失键走默认）。
6. **文档与真实行为是否一致？** 旧 Rev11 状态行、验证记录、CLAUDE 行数、test_ledger 接缝。
7. **有没有未验证就宣称完成？** 每条 DONE 必须有 `evidence/rev11c-*.json` 与命令输出。

---

## 附录 A · 本轮实测命令清单（可复现）

```bash
# 静态与文档
./.venv/Scripts/python.exe -m ruff check gcm                 # All checks passed!
./.venv/Scripts/python.exe -m mypy gcm                       # Success: 130 source files
./.venv/Scripts/python.exe scripts/doc_drift_check.py        # DRIFT 0
./.venv/Scripts/python.exe scripts/dev_metrics.py            # 131 文件 / 32,617 行；190 tests

# 定向测试
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest \
  tests.test_rev11_visual tests.test_rev11_w2 tests.test_rev11_w4          # Ran 54 OK
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest \
  tests.test_g75_visual_system tests.test_g79_repo_hygiene \
  tests.test_ui_compact_visibility tests.test_g76_wiring                  # Ran 85 OK (skipped=1)

# 现状核对
ls gcm/app/batch_plan.py
git log --oneline -3 -- gcm/app/batch_plan.py                # a4f5006 ...
grep -rn "set_ready" gcm/ --include=*.py                     # 只有定义
grep -rn "_conclusion_from_overview" gcm/ --include=*.py     # 只有 main_window.py:896 注释
grep -rn "build_empty_state" gcm/ --include=*.py             # 只有 progress_table 消费
grep -n "drawControl" gcm/ui/manage_model.py                 # :331 仍在
```

## 附录 B · 任务 → 文件 → 行号索引

| 任务 | 主文件 | 关键行 | 备注 |
|------|--------|--------|------|
| R-01 | `gcm/ui/batch_conclusion.py` | 38, 41, 124-137, 141, 173 | 扩展 set_ready / 新增 reset_idle / danger_lbl |
| R-01 | `gcm/ui/main_window.py` | 896, 897-900, 918, 1720-1725, 2135, 2335, 2654 | 接线与删除假注释 |
| R-01 | `gcm/ui/download_layout.py` | 135-139 | 防抖定时器与 textChanged |
| R-01 | `gcm/app/batch_plan.py` | 40, 49, 62, 133 | 数据源（不改契约） |
| R-02 | `gcm/ui/manage_model.py` | 299-351 | 自绘 delegate |
| R-03 | `gcm/ui/download_layout.py` | 280-351 | 两行动作区 |
| R-03 | `gcm/ui/qss_build.py` | 143-150, 175-180 | 新增 secondary/ghost |
| R-04 | `gcm/ui/manage_panel.py` | 155-164, 204 | 列策略与持久化 |
| R-04 | `gcm/db/settings.py` | 314-315, 475, 571 | 新字段 + 序列化 |
| R-05 | `gcm/ui/empty_state.py` | 17-39 | 新增文案常量 |
| R-05 | `gcm/ui/failure_panel.py` | 164-167 | 改工厂 |
| R-05 | `gcm/ui/manage_panel.py` | 185-191, 216 | 改工厂 |
| R-05 | `gcm/ui/settings_panel.py` | 61-67 | 日志占位符 |
| R-06 | `gcm/ui/manage_model.py` | 210 | `r.size or "—"` |
| P-01 | `gcm/ui/manage_panel.py` | 83-127 | 权重 + tr() |
| P-02 | `gcm/ui/manage_model.py` | data() 角色 | FontRole / TextAlignmentRole |
| P-03 | `gcm/ui/event_panel.py` | 33, 121-122, 172-178, 189 | 中文映射 / 字体 / 语义色 / 空态 |
| P-03 | `gcm/ui/result_labels.py` | status_label | 状态文案唯一来源 |
| P-04 | `gcm/ui/failure_panel.py` | 198-210 | 次数列排版 |

## 附录 C · 状态对照（旧文档 → 本文）

| 旧 ID | 旧状态 | 本文状态 | 承接 |
|-------|--------|---------|------|
| V-09 | DONE | **DONE(组件) + 接线 TODO** | R-01 |
| V-10 | BLOCKED | **TODO（阻塞理由不成立）** | R-01 |
| V-12 | TODO | TODO | R-02 |
| V-15 | TODO | TODO | R-03 |
| V-17 | TODO | TODO | R-04 |
| V-18 | TODO | **DONE(实质) + 余量** | R-06 |
| V-22 | DONE(工厂) | **DONE(工厂) + 四处接入** | R-05 |
| V-14 | CUT | CUT（维持） | — |
| V-01…V-08 / V-11 / V-13 / V-16 / V-19…V-24 | DONE | DONE（维持） | — |

---

**本文到此结束。你过目后回复「按计划执行」或指出要调整的条目；我再开始 W6-A。**