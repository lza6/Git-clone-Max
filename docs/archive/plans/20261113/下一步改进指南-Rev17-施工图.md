# 下一步改进指南 — Rev17（v9.19.1 → 9.20.0）

> **写作基线**（全部为本机实测，非搬运历史数字）：
> - 版本：`gcm.__version__ = 9.19.1`，`main` @ `963d5db`，工作树 clean。
> - 规模：151 个 .py / 38,365 行；超 800 行模块 5 个（与登记集合相等）；超 100 行函数 19 个（`MainWindow.__init__` 262 行居首）。
> - 质量闸现状：`ruff check gcm` = All checks passed（实测）；vulture --min-confidence 80 = 0 命中（实测）；`i18n_coverage` = I18N OK（实测）。
> - CLI 基线：`--cli --dry-run --json` 真实运行 exit 0，plan 输出结构完整（实测）。
> - 上一轮 Rev16（2026-11-13）已收口：五项 P1（C1 settings.update() 真实现、C2 deepcopy、C3 CLI 超时退出码、C4 重试批次行重建、A7 MCP 审计）+ 四项 P2 + UI 人因批 + 复审整改（审计先打码后截断、scroll 最小高回 110）。快集 2235 例 0 失败、覆盖 81%、DRIFT 0。
>
> **本文件性质**：只写给"下一个执行 AI"的施工图——每项带文件落点、验收方式、回归命令。**不重写已达标的东西，不重复 Rev14/15/16 已闭环项**。已核实过时的台账条目在 §1 直接判死，不许再开工。

---

## 0. 执行纪律（沿用项目既有规则，此处只列最容易犯的）

1. **解释器一律 `./.venv/Scripts/python.exe`**；Windows bash 无 `rg`，用 `grep` 或 python。
2. **测试隔离**：直接构造 `MainWindow`/`QWidget` 的测试，模块级先 `QApplication.instance() or QApplication([])`。
3. **子进程**：`text=True` 必须配 `encoding`；git 用 `utf-8/errors=replace`，Windows 控制台工具用 `_console_encoding()`。
4. **改共享方法必须跑全部调用方模块**（D-G83-3 教训：v9.17.0 一次 bump 漏 3 处）。
5. **批量 `tr()` 迁移只允许纯插入**，改完用 `git show :<path>` 比对键集合（D40 教训）；`tr()` 只能 1 个位置参数（D44 教训：错误路径上的 TypeError 会 abort）。
6. **tag 打在最后一次内容提交之后**；发布回填文档先写完再打 tag（D43 教训）。
7. **新增/删除 Settings 字段后必须跑僵尸字段守卫** `TestG69_18NoZombieSettingsFields`。
8. **别在快集跑的过程中改代码**（G80-B2 教训，error 是机械副作用）。
9. 版本 bump 用 `scripts/bump_version.py`，先 bump `__version__` 再 `changelog.py build`（test_g81_changelog 守卫顺序）。
10. 新功能登记走 `changelog.d/` 碎片，不往 `gcm/__init__.py` 加 changelog。

---

## 1. 台账判死清单（先做减法，防止下一个 AI 重开已闭环的工）

以下条目经 2026-11-13 本机代码实测**已经闭环或过时**，任何一轮都不要再实现：

| 来源 | 条目 | 实测证据（本机） | 处置 |
|------|------|------------------|------|
| beads-u3l | R14-18 i18n UI 层批次 | `scripts/i18n_coverage.py` = I18N OK；EN 建窗扫描零中文残留（Rev14 复核） | **关闭** |
| beads-jbq | U-14 progress_menu ⟳ | 全 `gcm/ui` 搜 `⟳` = 0 命中 | **关闭** |
| beads-y8m | V-15/R-03 下载页 8 按钮 objectName | `download_layout.py` setObjectName 列表含 `primary/secondary/ghost` 全部 12 处 | **关闭**（若要"以按钮 id 命名"属增强，进 §4.4） |
| beads-3ns | P-02 管理页 mono+右对齐 | `manage_model.py` FontRole/TextAlignmentRole col (3,4,6,7) 已实现 | **关闭** |
| beads-4bo | U-18 错误三段式 | `gcm/ui/error_dialog.py`（148 行）已被 6 个文件引用；全库带 `str(e)` 的 warning/critical 仅剩 4 处（error_dialog 自身兜底 3 处 + diag_dialog.py:95） | **关闭**（diag_dialog 1 处进 §4.5） |
| beads-qe5 | U-15 busy 扩展 | `import_starred` 已接 `_set_buttons_enabled`（main_window.py:2156-2159 有 Rev12 U-15 注释）；`_export_diag_bundle` 已接 busy；settings export/import 已走 `error_dialog` | **关闭**（statistics 导出 PNG 仍无忙态，进 §4.5） |
| beads-tdy | L3 oplog+撤销 | `gcm/util/oplog.py`（230 行，JSON 载体+跨进程锁+落盘前打码）、`gcm/app/undo.py`、`gcm/ui/undo_bar.py`（194 行）、`main_window.menu_undo/undo_op/offer_undo` 全部存在 | **关闭**（DB schema v9 迁移方案作废——JSON 载体决策已写进 oplog docstring，勿回退） |
| beads-13a | Rev 文档审计收口 | 6 份旧文档已在 `docs/archive/plans/2026110x/` 归档； Rev15/16 验证记录齐 | **关闭**（§4.1 撤白名单守卫仍有效） |
| beads-gbu | 克隆转知识 | `gcm/app/knowledge.py`（316 行，零 LLM 画像）+ `repo_profile` + `repo_detail_dialog` 展示齐全 | **已闭环**（beads 状态未刷新，只是记账滞后） |
| known-issues #7 | MCP 长任务 progress/cancel | `gcm/mcp_jobs.py` 有 job_status/job_cancel/progress（Rev14 已复核） | 更新 known-issues 措辞 |
| known-issues #9 | mirror_used 只到 CLI | `progress_table.py` 已含 mirror_used tooltip（Rev16 D3） | 更新 known-issues 措辞 |
| beads-ph3 / d78 | 真机多显示器/真人验收 | 本机单屏 + 无屏幕阅读器，物理不可自动化 | **保留开放**，但标注"等用户真机窗口"，不是 AI 任务 |

**同轮顺手做**：把上表逐条 `bd close <id> --reason="Rev16 已闭环，2026-11-13 实测复核"`；known-issues.md #5/#9/#7 措辞更新。这本身就是 §2 的 P1 任务 T0。

---

## 2. P1 — 本轮必做（按依赖排序）

### T0 · 台账清理与守卫撤销（0.5h）

- **落点**：beads 批量 close（§1 表格逐条）；`tests/test_g79_repo_hygiene.py` 里 `2026-11-09 登记，同日撤销` 白名单段若仍指向已删文档则删除；`known-issues.md` 措辞更新。
- **验收**：`bd list --status=open` 中不再出现已闭环条目；`test_g79_repo_hygiene` 绿。

### T1 · Rev16 复审遗留 P2/P3 收口（2h）

Rev16 §7.1/§7.3 复审报了但标"接受现状"的三件，本轮做掉：

- **a. `repo_input maxHeight 96 真机-only 分支无守卫**（Rev16 复审 P3）：
  - 落点 `gcm/ui/download_layout.py`。offscreen 下 `effective_minimum` 夹到 584 使 `<560` 分支不可达。
  - 做法：不造假环境。把该分支的高度常量抽成模块级常量（如 `QUICK_INPUT_MAX_COMPACT = 96`），写**常量消费守卫**测试（断言 `_apply_compact_visibility` 引用了该常量而非字面量），并在 `test_g78_quick_scroll` 补一条"极矮窗常量存在且被引用"。这是守卫可达到的最大强度，明说它不证明真机渲染。
- **b. settings `update()` 对 ui_state 的浅合并已做（B6），补一条文档同步**：`docs/data-model.md` 若仍写"ui_state 整值替换"则更新。
- **c. `diag_dialog.py:95` `QMessageBox.warning(self, tr("导出失败"), str(exc))`**：换成 `error_dialog.show_error`（与其余 6 个文件同口径）。落点一行 + 上下文传 `tr("导出诊断包")`。验收：`grep -n "str(exc)" gcm/ui/diag_dialog.py` = 0 命中（information 分支的 `str(out)` 是成功路径展示，**不动**）。

### T2 · statistics 忙态补齐 + 慢入口盘点收尾（1.5h）

- `gcm/ui/statistics_dialog.py` `_export_png`（430 行起）无 busy：PNG 导出含渲染大表，接 `busy(owner, "export_png", buttons=("btn_export_png",))`（先确认按钮 objectName，实际是什么用什么）。回归：`tests.test_g74_ui_layout` + 定向新守卫 `TestStatisticsExportBusy`（进入禁用/退出恢复，含异常路径）。
- 全库扫一遍剩余 export/import 入口（`manage_panel._export_metadata/_import_metadata` 已有 error_dialog 但**无忙态**——本地 JSON 读写毫秒级，**登记"判定不需要"**，不接）。

### T3 · `p9v` 预览可勾选执行（4h，本轮最大件）

现状：`batch_preview_dialog.py`（108 行）只读展示 plan_batch，确认后整批执行。beads 描述的"预览结果可勾选后再执行"未做。

- **做法**：
  1. `QTableWidget` 加 checkbox 列（`ItemIsUserCheckable`），默认全选；`replace` 行默认**不选**（破坏性动作默认排除，与 R15-06 关窗默认 No 同一人因原则）。
  2. 对话框加公开方法 `selected_rows() -> list[dict]`；`main_window._run_sync_clone` / `_handle_call` 的 clone 路径把未勾选行从 specs 剔除后再 `launch()`。
  3. 被剔除的行记日志 + 结论条播报"已跳过 N 条（预览未勾选）"。
- **红线**：仍与 CLI/MCP 同源 `plan_batch`（不写第二份判定）；对话框本身仍只读不写盘不联网。
- **守卫**：`test_preview_selection`（≥6 例：默认全选、replace 默认不选、勾选子集 launch specs 数、全不选时按钮禁用+提示、剔除行播报、plan 一致性）。
- **风险**：`_launch` 链路在 `main_window.py`（3661 行），新逻辑写进 `download_tools.py` 或独立 `preview_apply.py`（≤200 行），main_window 只留薄转发。

### T4 · `main_window.__init__` 262 行拆分（2h，Rev16 明确"下次动这块顺手拆"）

- 落点 `gcm/ui/main_window.py:__init__`（269 行，全库第一）。照 `engine_factory.py` 先例：把「启动期引擎构建 + settings 映射」抽 `ui/startup_wiring.py`（≤200 行）返回 (engine, hooks)；`__init__` 保留控件装配顺序。
- **铁律**：纯搬移不改逻辑；搬完 `TestStartupEngineReflectsSettings`（4 例）+ `TestG69_18NoMainWindowRefCycle` 必须原样绿；引用环纪律——新模块不得用 lambda 捕获 owner。
- 这是**唯一**允许动 main_window 的结构性变更；其余 UI 新逻辑仍走独立模块。

### T5 · MCP 只读面收尾（2h）

known-issues #5 剩余项里**真正有价值**的是 `resources/`（`gcm://repos`、`gcm://history` 两个只读资源），把 CLI/MCP/HTTP 三入口已有数据再暴露一种协议形态；审计行/异步作业已闭环（A7/mcp_jobs）。`prompts` 无真实消费场景，**明确不做**。
- 守卫：`test_g56_mcp_extra` 补 resources/list + read 两例；`docs/agent-contract.md` §tools 同步。

---

## 3. P2 — 排队项（P1 全绿后做）

### T6 · 管理页/进度表快照护栏（1h，beads-qpm 拆前置）

统计中心、管理页在 DB 大于 5k 行时的 GUI 线程写入已实测 3ms/仓可接受（S-08 结论），**不后台化**。做的是：`_load_db_into_grid` 与 `update_results` 批量路径加**行数守卫日志**（>10k 行时 applog WARN 一次），让"数据量增长拖慢 UI"从静默退化变成可观测。不改执行模型。

### T7 · 预览结果回填 CLI/MCP（1h）

T3 的反向：`--dry-run --json` 输出加 `"preview_id"`（进程内 uuid，不落盘），MCP `clone_batch(dry_run=true)` 响应同字段；GUI 预览对话框侧边显示"与 CLI 预览一致"指纹（同 plan_batch 输入的 counts）。**只加字段不改 schema 既有键**（`test_g80_contract` 契约快照须同步更新，见 `docs/agent-contract.md`）。

### T8 · 冗余守卫族瘦身审视（1h）

`tests/` 217 文件里存在历史分层重复（例：G76 接线守卫与 G84 wiring 守卫口径相近）。**动作**：跑 `python -m unittest discover -s tests -p "test_g7*.py"` 计时，找出 >60s 的组合；**只合并同文件内重复断言**，不删跨文件守卫（它们是快集黑名单机制的单元）。产出：`docs/慢测定位.md` 补一行实测数据。

### T9 · README 功能表与 knowledge.py 打通（1h）

`app/knowledge.py` 画像（lang/kind/has_ci/has_tests/summary）已在 repo_detail 展示，但 README 功能表未列（809beff 只补过 4 项）。补一行 + `docs/product-brief.md` 的"克隆转知识"叙事同步。**同步删掉 `gcm/i18n/en.py` 里 `lore` 死键**（实测 grep 命中，i18n_coverage 不报因为它在字典侧不在调用侧）。

---

## 4. P3 / 明确不做（防下一个 AI 跑偏）

**明确不做**（每条都有理由，不要重开）：
- **统一限流模块**（G58-1 结论不变）：无真实调用场景。
- **内置 GitHub 镜像**：v9.12.1 实测慢 8 倍，永久移除，`DEFAULT_GITHUB_MIRRORS` 守卫钉死为空。
- **MCP prompts**：无消费方。
- **health_card 数据库列**：评分由 sync_history 实时派生是唯一事实源，加列必漂移。
- **自动重试按 host 分组退避**：全局 streak 已覆盖主场景（D-G83-1）。
- **DB schema v9 oplog 迁移**：JSON 载体决策已沉淀，重开=推翻已验证设计。
- **Web 控制台/前端工程**：Rev15 §1.1 已论证不成立。

**真机人工验收**（等用户窗口，不是 AI 任务）：双屏拔插（ph3）、混合 DPI、NVDA/JAWS（d78）、真机触摸密度、1366 实屏首屏目检。

**低优先增强**（有余力再做，按价值排序）：
- **§4.4 objectName 语义化**：给 8 个下载页按钮补 `btn_*` 名（qss 已用 primary/secondary/ghost 角色，不冲突）；价值是 QSS 可按 id 微调 + busy() 按钮恢复列表更稳。
- **`--diag` 单独用静默走 GUI 的 P3**（known-issues #13）：`--help` 文案补"必须配 --cli"，一行改动。
- **备份重试间隔 60ms → 指数退避**（B6 P3 遗留，1 行）。
- **`_save_locked` 重复 asdict**（B6 P3，微优化，无行为变化）。

---

## 5. 执行顺序与依赖图

```
T0 (台账清理) ──┬─→ T1 (复审遗留) ──→ T2 (忙态收尾) ──┐
                │                                      ├─→ T3 (预览勾选) ──→ T7 (回填 CLI/MCP)
                └─→ T4 (init 拆分，与 T1-T2 无文件交集，可并行)   │
                                                       └─→ T5 (MCP resources) ──→ T9
T6/T8 任意间隙插入
```

- T0/T1/T2 动的面互不重叠（beads+hygiene / diag_dialog+download_layout 常量 / statistics），可一轮串行完成。
- T3 依赖 T1 完成（同在 download 链路，避免预览对话框改动和常量抽取互相踩）。
- T4 独立，随时可做，但**必须在快集运行之外的时间窗**（教训：不要边跑快集边改代码）。
- 全部完成后：bump 9.20.0 → changelog build → ruff/mypy/快集 → PyInstaller → health_check --json → commit → tag v9.20.0 → push → publish_release。

---

## 6. 验收门禁（本轮整体完成的判据）

| 项 | 命令 | 通过线 |
|----|------|--------|
| 静态 | `ruff check gcm` / `mypy gcm` | 0 error / Success |
| 死代码 | `vulture gcm --min-confidence 80` | 0 命中 |
| i18n | `python scripts/i18n_coverage.py` | I18N OK |
| 文档漂移 | `python scripts/doc_drift_check.py` | DRIFT 0 |
| 快集 | `coverage run --source=gcm scripts/ci_run_fast.py` | 0 失败 0 错误，覆盖 ≥81% |
| 新守卫 | T1c/T2/T3/T5 各自的守卫族 | 全绿且含**变异自检**（注释掉实现→红→恢复→绿，照 G82/C-17 方法） |
| 规模 | `python scripts/dev_metrics.py` | 无第 6 个超 800 行模块；超 100 行函数集合与登记一致 |
| 台账 | `bd list --status=open` | §1 已闭环条目不再出现；known-issues 措辞与代码一致 |
| 发布 | health_check.py --json | 7 项 OK，smoke_ms 中位数 ≤3500 |

**证据落盘**：`docs/验证记录-2026-1X-XX-Rev17.md`（命令+真实输出）+ `evidence/rev17.json`；本文件随执行在条目上标注 `已完成 @ commit`，全部完成后按项目惯例归档到 `docs/archive/plans/<日期>/` 并写 archive-manifest。
