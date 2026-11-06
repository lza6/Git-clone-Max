# UI 视觉重设计指南 — Rev11 · 测量台（Instrument）

> **本轮交付模式：仅文档**。写这份文档的一轮**没有修改任何 `gcm/` 源码、配置或依赖**。
> 实际动作只有两件：①本文件落盘；②`tests/test_g79_repo_hygiene.py::_ROOT_WHITELIST` 增加 `"计划书"`
> 一行登记（见 §1.6 —— 不登记则本文件一入库 CI 就红，是**配套**而非功能改动）。
> 取证：`evidence/rev11-W0.json`。
>
> **基线快照**：`main` @ `f6b913e` · `gcm.__version__ = 9.18.0` · 编写日期 **2026-11-04**。
> 所有数字都是本轮 `scripts/dev_metrics.py` / `wc` / 截图实跑的现测值，不是转述历史文档。
>
> **本文件不是事实源**。事实源仍是：`gcm/__init__.py`（版本）、`docs/architecture.md`（分层）、
> `docs/agent-contract.md`（agent 接入契约）、`docs/data-model.md`（数据模型）、`policy.md`
> （交付门禁）、`test_ledger.md`（接缝注册表）、`.agent/memory/*`、`evidence/*.json`、
> `docs/验证记录-*.md`。**本文件只放「还没落地的改进项」+「凭什么算完成」**，不复述已闭环历史。
>
> **排期**：独立专项，**排在 v10.0.0 之后**（用户 2026-11-04 拍板，见 §0.2）。本文不与
> `docs/plans/.rev10-part1..5.md` 竞争执行依据 —— 那份管 v10.0.0，本文件管它的**下一版**。

---

## 0. 这份文档怎么用

### 0.1 三类读者

| 读者 | 读哪几节 | 用法 |
|------|---------|------|
| **执行 AI / 执行人** | §1 → §3 → §4 对应 Wave → §5 → §10 | 一次只做一个 Wave。开工先 `git status --porcelain` 核现状，已完成项从批次里划掉再干 |
| **你（产品负责人）** | §0.2 → §1.3 → §2 → §8 | 只看「现在哪里是脏的」「美学立场是不是你要的」「我替你挡掉了什么」 |
| **下一轮审计 / Rev12 作者** | §1.1 → §4 各条「状态」行 → §6 | 逐条复验，**不许拿本文件的「已完成」当证据**（本项目在计划文档上翻过两次车） |

### 0.2 用户本轮拍板（本节即授权记录，2026-11-04）

| # | 决策 | 选项 | 影响 |
|---|------|------|------|
| D-1 | 文档落点 | **`计划书/UI-视觉重设计-Rev11.md`（重建根级 `计划书/` 目录）** | 触发 §1.6 的根目录白名单登记；本文因此不受 `test_g67_docs_freshness` 的路径扫描约束（它只扫 `docs/**`） |
| D-2 | 美学调性 | **测量台 / 仪表盘 Instrument** | 主方向定义 `deep` 主题的人格，`light` / `nord` / `hc` 按同一套语法推导（§2.7） |
| D-3 | 改造深度 | **全套：视觉 + 信息架构 + 新交互** | 会动 `download_layout.py` 的布局骨架与 `progress_table.py` 的列结构；回归面最大，守卫变更必须逐条记账（§5） |
| D-4 | 排期 | **独立专项，排在 v10.0.0 之后** | 本文不作为 v10.0.0 的验收项；v10.0.0 期间不得据此提前改视觉层 |

### 0.2.1 第二轮拍板（2026-11-04，四项残留决策已定）

| # | 决策点 | 拍板结果 | 我标注的风险（照办，但必须带缓解） |
|---|--------|---------|--------------------------------|
| D-5 | 进度表加「耗时」列 | **新增独立列，加在末尾** | 1020×560 下可能横向滚动 → `V-16` 必须跑 9 尺寸验证 |
| D-6 | 拖拽排序 vs 列排序 | **两者共存，加一个「排序」切换按钮** | 排序状态机复杂，且 `progress_table.py:406-415` 记录了 G69-5 真实事故（排序一开视觉行号≠engine index，300 行批量 300/300 全错位）→ `V-21` 必须先立状态机规格与守卫，见 §5.3 |
| D-7 | 就地重命名范围 | **只改显示标签，不动磁盘** | 显示名≠目录名会让人困惑 → 必须给 ToolTip 显示真实路径 |
| D-8 | accent 是否保留自选 | **扩到 6 档** | 验证面从 4 主题变成 6 预设 × 4 主题 = 24 组对比度；且新档位**必须与 success/warning/error 保持色相距离**，否则「一个颜色只能说一件事」被自己破坏 → 约束见 §2.3.1 |

### 0.3 证据标签（全文档强制，与 `policy.md` §3 一致）

- `已验证`：**本轮**实际运行并观察到结果（附命令与输出摘要）
- `静态确认`：读代码/文档确认，未运行
- `合理推断`：有间接证据，未直接验证
- `待验证`：缺环境 / 权限 / 凭证 / 时间

**「已验证」不接受转述**。执行方引用本文件的指标时，必须自己重跑一次再写进 `evidence/`。

### 0.4 状态回填规则（防「全部完成」式伪闭环）

每个任务有唯一 ID（如 `V-07`）。执行完在**本文件对应条目的 `状态:` 行**改成：

```
状态: DONE 2026-11-XX · evidence/<批>.json · docs/验证记录-<日期>.md
```

**只允许** `TODO` / `DOING` / `DONE` / `CUT（附理由+拍板人）` / `BLOCKED（附最小缺失条件）` 五种。
禁止「基本完成」「已完成大部分」「理论上可行」。**没有 evidence 文件的一律不许标 DONE。**

---

## 1. 事实基线（本轮现测，2026-11-04）

### 1.1 环境与规模

| 项 | 实测值 | 获取方式 | 标签 |
|----|--------|---------|------|
| 分支 / HEAD | `main` @ `f6b913e` | `git log --oneline -1` | 已验证 |
| 版本 | `9.18.0` | `python -c "import gcm;print(gcm.__version__)"` | 已验证 |
| 工作区 | 仅 `?? docs/plans/`（Rev10 草稿，未跟踪） | `git status --porcelain` | 已验证 |
| `gcm/` 规模 | **123 个 `.py` / 31,671 行** | `scripts/dev_metrics.py` | 已验证 |
| `tests/` 规模 | **187 个 `test_*.py` / 36,069 行** | 同上 | 已验证 |
| `gcm/ui/` 模块数 | **47 个面板/组件模块** | `python -c "len(Path('gcm/ui').glob('*.py'))"` | 已验证 |
| 超 800 行模块 | **3 个**：`app/engine.py`、`git/service.py`、`ui/main_window.py`；与登记集合一致 | `dev_metrics.py` | 已验证 |
| 贴红线（500–800） | `settings_panel` 795 · `progress_table` 787 · `settings_groups` 782 · `repo_db` 750 · `manage_panel` 656 · `http_api` 646 · `mcp_server` 629 | 同上 | 已验证 |
| 本轮改动锚点 | `download_layout` 351 · `manage_panel` 656 · `radar_chart` 151（`Path.read_text().splitlines()` 精确计数） | 同上 | 已验证 |
| >100 行函数 | **19 个**（最大 `MainWindow.__init__` 252、`_build_qss` 229、`run_git_ui` 217） | 同上 | 已验证 |
| 截图基线 | `reports/ui-audit/` **34 张**（2026-11-03 生成，4 主题 × 5 尺寸 + 7 页签 + 7 对话框） | `Get-ChildItem reports\ui-audit` | 已验证 |
| 截图工具 | `scripts/ui_screenshot_audit.py`，产物进 `reports/`（已在 `.gitignore` 的截图集合内） | 文件实读 | 静态确认 |

> **`gcm/ui/` 是本次改造的容积约束**：`progress_table` 787 / `settings_panel` 795 / `settings_groups` 782
> 都已贴线。**这个专项里任何"往这三个文件加逻辑"的做法都会破 800 红线**，
> 因此 §4 的全部任务一律新建独立模块（`test_g79_repo_hygiene.TestNoNewOversizedModules` 用集合相等断言守着）。

### 1.2 视觉底座**已经有什么**（别重复造）

G75（v9.9.0，2026-10-23）已建立完整设计系统分层，本轮是**在这套底座上升级**，不是从零建：

| 能力 | 位置 | 现状 |
|------|------|------|
| 四套色板 + 设计 token | `gcm/ui/palettes.py`（184 行） | `PALETTES` 四主题键集合一致；`TOKENS` 含 radius / spacing(4pt 网格, section=40) / border / shadow / row |
| 排版系统 | `gcm/ui/typography.py`（88 行） | 三字族（Bahnschrift 显示 / YaHei UI 正文 / Cascadia Mono 数据）+ 七级阶梯（28/20/18/15/13/12/11） |
| QSS 拼装 + 缓存 + 静态校验 | `gcm/ui/qss_build.py`（242 行）→ `qss_validate.py`（283 行） | 按 `(palette, scale, motion)` 缓存上限 32；每次构建都过 `validate_qss_or_raise` |
| 状态形状编码 | `gcm/ui/status_pill.py`（137 行） | 7 种 QPainter 几何形状（disc/cross/split/ring/dash/wedge/hollow_square），**不用字符符号** |
| 运行态齿条 | `gcm/ui/status_strip.py`（203 行） | 并行/活跃/成功/失败/用时，空态一律 `—`；含 `Sparkline` 24h 趋势 |
| 批次总览条 | `gcm/ui/batch_overview.py`（61 行） | 「已完成 x/y · 成功率 %」+ 180px 迷你进度条 |
| 响应式断点 | `gcm/ui/layout.py`（73 行） | `unsupported/compact/standard/wide` 四档 |
| 对比度工具 | `palettes.py:194` `contrast_ratio` / `:182` `_luminance` | WCAG 2.x，G70 修正过绿蓝权重写反 |
| 强调色预设 | `palettes.py:128` `ACCENT_PRESETS` | teal/blue/violet × dark/light 成对 |
| 截图审计 | `scripts/ui_screenshot_audit.py` | 一次产出 34–38 张 |

**结论：底座是好的。缺的不是"再建一套 token"，而是 token 没有兑现到界面上。**
下面 §1.3 的十二条，没有一条是"加个新变量能解决的"，全部是**口径与结构问题**。

### 1.3 本轮现测缺口（V1–V12）

截图基线：`reports/ui-audit/download_deep_1440x900.png`、`reports/ui-audit/tab_manage_1440x900.png`
（2026-11-03 生成，**本轮逐张看过**，`已验证`）。代码落点均本轮 `静态确认`。

| # | 问题（截图可见 / 代码可查） | 落点 | 严重度 |
|---|------------------------------|------|--------|
| **V1** | **层级坍塌**：`TYPE_SCALE` 定义了 7 级，但下载页首屏**实际只用到 body(13) 与 small(11)** —— 「仓库地址（每行一个）」这种分组标题与表格正文同字号，`display(28)` / `h1(20)` / `metric(18)` 在首屏一次都没出现。仪器感只存在于代码里 | `download_layout.py` 全文无 `apply_type`；`qss_build.py:120-130` 的角色选择器在下载页无消费者 | P1 |
| **V2** | **强调色通胀**：`accent` 同时承担 7 种语义 —— 进度 chunk、批次条填充、页签下划线、焦点环、`kicker` 文字、设置导航左指示条、`QLabel#accent`。**处处强调 = 无处强调**，用户扫视时抓不住"当前该看哪里" | `qss_build.py:127,143,146,159,177,198,229,231,244` + `progress_delegate` | P1 |
| **V3** | **按钮墙**：下载页两排共 **8 个** QPushButton（开始/取消/暂停/清空 + 打开目录/失败汇总/重试队列/诊断包），加表单区 4 个（保存清单/载入/导入 Star/浏览）与快捷区 1 个（编辑）。主 CTA 与"全局工具"**视觉权重相同**，只靠 `#primary` 反相块区分一个 | `download_layout.py:185-300` | P1 |
| **V4** | **批次叙事被埋没**：`已完 24/24 · 成功率 33%` 是本屏**最重要的结论**，却是 13px 常规字 + 一条 180px 细条，位于表格上方 —— 视觉权重低于任何一行表数据 | `batch_overview.py:28,33,70` | P1 |
| **V5** | **状态色圣诞树**：一排表里 绿/灰/黄/红/青 五色并存，pill 用 `alpha=38` 底 + 1px 满饱和描边（`status_pill.py:142-145`），底色混了面板色**显脏**，且颜色是唯一区分手段之一（形状是主编码，但形状只有 12px，弱于满屏色块） | `status_pill.py:140-145` | P2 |
| **V6** | **数据不是等宽**：`上次同步 2026-11-03 04:12:54`、`健康 5/5`、大小列都是比例字体，列内数字**不对齐**；仓库名 `vercel-labs_skills` 是标识符却用正文字族 | `typography.py:40-49` 有 `mono` 角色，管理页表格未消费 | P2 |
| **V7** | **表格内按钮脱离 QSS**：管理页「历史」列的 `查看` 由 `HistoryButtonDelegate.paint` 用 `QStyle.drawControl(CE_PushButton)` 画**原生控件**，在深色主题下渲染成浅色原生按钮 —— 与全站自绘风格**断层** | `manage_model.py:324-331`（`style.drawControl`）；`:320` 文本 `"查看"` 未走 `tr()` | P1 |
| **V8** | **卡片等权堆叠**：`QGroupBox` 一个样式走天下（同底同边框同圆角），从「仓库地址」到「下载位置」到「克隆模式」没有主次，用户不知道先看哪；`spacing.section=40` 定义了却未在页面级使用 | `qss_build.py:133-139` | P2 |
| **V9** | **管理页 9 列拥挤**：列宽 `(3,150),(4,78),(5,70),(6,64),(7,84)` 硬编码（`manage_panel.py:161`），「本地路径」被压成 `C:\Users\ADMINI~1....anthropics_skills` 读不出内容；「大小」列常空（异步算完才回填）、「健康 5/5」无解释 | `manage_panel.py:155-204` | P2 |
| **V10** | **动效零预算**：全场只有逐行 1.2s 完成消隐（`ck_animations` 用户可见设置项），**没有任何"批次开始 / 批次完成"的高影响时刻编排** —— 恰恰是最该有反馈的两个瞬间 | G75 归档清单 T-UI-09 标 `partial` | P2 |
| **V11** | **空态与首启未统一**：`#emptyHint` 只在部分位置用；首启向导、失败面板空态、管理页空库各自为政。G75 T-UI-08 标 `partial` | G75 归档清单 T-UI-08 | P3 |
| **V12** | **色板兜底值仍是 Primer**：`radar_chart.py:78-79` 的兜底字典是 `#58a6ff / #30363d / #e6edf3 / #8b949e / #161b22` —— 正是 `TestAntiGenericAesthetics` 明令禁止的 GitHub Primer 全家桶，只是披了层 `pal.get(k, 兜底)` 的皮 | `radar_chart.py:78-79`；`checklist.py:61` 同型（`#888888`） | P2 |

**这十二条的诊断结论（一句话）**：**设计系统做对了，但界面没消费它。**
七级字号阶梯、三字族、40px 区块间距、语义色预算 —— 四个最有力的工具全部闲置。
所以本轮的活不是"再设计一套"，而是**把已有的设计系统真正打到界面上，并把信息架构按"仪表"重排**。

### 1.4 对既有计划文档的时效性判定（用户本轮明确要求）

用户要求：「如果上面已有该文件的话你要看该文件的内容过时了吗？过时了则需要重新写」。

**先查有没有同主题的既有文件。结论：有两份，都不可直接沿用。**

| 文件 | 是什么 | 时效性判定 | 处置 |
|------|--------|-----------|------|
| `docs/archive/plans/20261023/UI视觉系统重构指南.md`（465 行） | G75 那一轮的 UI 计划，已归档（只读追溯件） | **已完成并归档，不可作为执行依据**。其 T-UI-01…T-UI-10 已落地到 `palettes`/`typography`/`qss_build`/`status_pill` 四层；但 **T-UI-08（空态/首启）与 T-UI-09（动效编排）标 `partial`**，P3-2（表单标签全角冒号）未做 —— 三项由本文 §4 W5 承接 | 只读引用，不复活 |
| `docs/plans/.rev10-part1..5.md` | v10.0.0 的执行计划草稿（未跟踪） | **新鲜，但主题不同**。其 `part1:222` 明写「**不是 UI 换皮** —— 本版只做反馈闭环与状态一致性，不动既定的视觉系统分层」。它的 §④ W3 是"每个按钮都要有三态反馈"，**不含任何视觉/IA 维度** | 并列存在，互不覆盖（§1.5） |
| `docs/计划文档审计-2026-11-01.md` | 计划文档审计报告 | 新鲜；确认 `docs/archive/plans/20261023/` 下 UI 指南属"历史归档，保留" | 无需动 |

**判定：同主题文件不存在（`docs/计划书/` 与根级 `计划书/` 本轮实测都不存在，`已验证`），因此新建本文件。
上一份 UI 计划的未闭环项已在 §4 W5 逐条承接，不重复造。**

### 1.5 与 Rev10（v10.0.0）的边界 —— 谁管什么

两份文档**同一时期存在**，必须写死分工，否则执行方会拿到互相矛盾的两套要求：

| 维度 | Rev10（`docs/plans/`） | 本文件（Rev11） |
|------|----------------------|----------------|
| 目标 | 安全网 / 稳定性 / **反馈闭环** | **视觉系统 + 信息架构 + 新交互** |
| UI 相关范围 | 每个动作的「我收到了 → 我在做 → 做完了」三态；执行前预览差异表；撤销入口；git 命令查看 | 色板语义预算、排版声部、卡片权重、动作分区、批次叙事、表格结构 |
| 会不会改 `palettes/typography/qss_build` | **不动**（part1:222 明写不回退四层） | **主战场** |
| 会不会改 `download_layout.py` 布局骨架 | 只加按钮/入口，不重排 | **重排** |
| 执行顺序 | 先 | **后**（D-4） |

**冲突消解规则**：若某项在两边都出现（如「执行前预览」），**Rev10 拥有功能定义权，Rev11 只拥有它的呈现方式**。
Rev11 开工前必须先读 Rev10 的最终落地状态，不得凭本文的描述重新实现功能。

### 1.6 本文档自身对仓库守卫的影响（不改会红 CI）

**这是本文件的唯一"非文档"前置动作，必须与入库同批完成。**

`tests/test_g79_repo_hygiene.py::TestRootDirWhitelist` 的两条断言是**双向集合相等**：

- `test_top_level_entries_are_whitelisted`：`git ls-files` 里的顶层条目必须都在 `_ROOT_WHITELIST` 内
- `test_whitelist_has_no_dead_entries`：`_ROOT_WHITELIST` 的每一项必须在索引里真实存在

因此：

| 状态 | 结果 |
|------|------|
| `计划书/` 目录存在但**未入库** | ✅ 绿（守卫读 `git ls-files`，未跟踪文件不可见） |
| `计划书/` 目录**已入库**但未登记白名单 | ❌ **红**：`根目录出现未登记的顶层条目: 计划书` |
| 先登记白名单、目录还没入库 | ❌ **红**：`白名单里这些条目在索引中已不存在` |

**落地动作（与本文入库同一个提交）**：在 `tests/test_g79_repo_hygiene.py::_ROOT_WHITELIST` 的**目录段**补一项，
并附理由注释（该文件第 39 行明写「新增任何一条都必须先在这里登记理由」）：

```python
# 2026-11-04 Rev11：计划文档目录回到根级（用户决策 D-1）。与 docs/plans/ 的区别：
# docs/plans/ 放「正在执行的迭代计划」，本目录放「面向下一版的独立专项设计指南」，
# 生命周期同 docs/计划书/：条目全部闭环 → 归档到 docs/archive/plans/<日期>/ → 删除目录。
"计划书",
```

> **为什么不放 `docs/计划书/`（本轮已实测，不是推断）**：
> `tests/test_g67_docs_freshness.py` 的 `SCAN_GLOBS` 覆盖 `docs/**/*.md`，且 `_EXCLUDED_DIRS` 只排除 `plans`。
> 一份**计划**必然引用「建议新增但尚未创建」的路径与「目标版本号」。本轮做了探针实验：
> 在 `docs/计划书/_probe.md` 写入 `v10.1.0` 与 `gcm/ui/nonexistent_probe.py` 后跑
> `python -m unittest tests.test_g67_docs_freshness` → **FAILED (failures=2)**，
> 报「版本 'v10.1.0' != gcm.__version__ (9.18.0)」与「引用了不存在的路径」（`已验证`，探针随后已删除，
> 复核 11 tests OK）。放根级 `计划书/` 天然不在其扫描范围，**不需要改那条守卫**
> —— 改它才是真正的放宽断言。
> 放根级 `计划书/` 天然不在其扫描范围，**不需要改那条守卫**（改它才是真正的放宽断言）。

### 1.7 本轮**没有**执行的验证（不得声称通过）

| 项 | 原因 | 补偿 |
|----|------|------|
| `ruff` / `mypy` / 快集 / 覆盖率 | 仅文档，无代码变更 | 引用历史证据时须标 `历史证据`，不得写成本轮结果 |
| 截图重建 | 会写盘 34 张 PNG（工作区产物） | 列为 §4 `V-01` 待执行项 |
| PyInstaller 打包 + 真机点击 | 属执行模式 | 列为 §6 验收矩阵末两行 |
| 实际运行 | **仅跑了** `scripts/dev_metrics.py`、`git`、文件读取与截图目检，以及下表的守卫复核 | 见下表 |

**本轮真实跑过的命令与结果（`已验证`，取证见 `evidence/rev11-W0.json`）**：

| 命令 | 结果 |
|------|------|
| `python scripts/dev_metrics.py` | 版本 9.18.0 / 123 文件 31,671 行 / 超 800 行 3 个且与登记集合一致 / >100 行函数 19 / tests 187 文件 36,069 行 |
| `git -c core.quotepath=false ls-files \| grep ^计划书/` | `计划书/UI-视觉重设计-Rev11.md` |
| `python -m unittest tests.test_g79_repo_hygiene` | `Ran 10 tests` · **OK** |
| `python -m unittest tests.test_g67_docs_freshness` | `Ran 11 tests` · **OK**（探针删除后复核） |

---

## 2. 设计方向（Design Before Code）

### 2.1 四问

**PURPOSE** — 这是给「挂着刷几百个仓库」的个人开发者用的批量克隆器。用户的情感需求**不是"好看"**，
而是三句话：**「我知道它在干什么」「还差多少」「哪几个坏了」**。
界面的首要任务是把运行状态变成**一眼可读的仪表**，次要任务才是好看。

**TONE** — **测量台（Instrument）**：工业仪表的读数纪律 + 印刷排版的层级纪律。
不是"又一个 GitHub 皮肤"，不是渐变大玻璃，不是霓虹赛博，不是纸墨文艺。

**CONSTRAINTS**（硬的，违反就红 CI 或被否）

1. 技术栈不变：PyQt6 + QSS + 自绘 delegate。**不用** Qt Quick/QML。
2. QSS 物理限制：**不支持** `box-shadow` / `transition` / `letter-spacing` / `grid` / `gap`；
   `QGraphicsDropShadowEffect` 在滚动区与离屏渲染下开销大且有毛刺 → **本专项禁用**。
3. 四套主题（`deep`/`light`/`nord`/`hc`）必须同时成立：正文 ≥4.5:1，`hc` 文本 ≥7:1。
4. `prefers_reduced_motion` 必须被尊重，`motion=False` 时 QSS 不产生任何 hover/pressed 色块。
5. 字族只用系统内置（Bahnschrift / Microsoft YaHei UI / Cascadia Mono），不打包 ttf、不联网下载；
   CI 在 Linux/macOS 无这些字族，字体**存在性**断言必须条件化。
6. 状态**不得**用字符符号（✓ ✕ ⚠ ⊘ 在微软雅黑缺字形 → tofu），只用 `status_pill.draw_shape` 的几何图形。
7. 默认主题**不得**退回 GitHub Primer（`#0d1117` / `#58a6ff`）。
8. 1020×560 是硬地板：主操作与表格不得被裁切（`test_ui_compact_visibility` 守）。

**DIFFERENTIATION** — 三个记忆点，见 §2.8。

### 2.2 美学立场三原则（本轮的宪法）

后续所有具体决策都由这三条推出，遇到本文没写到的情况按此裁决：

**原则一：一个颜色只能说一件事。**
`accent` 从 7 种用途收敛到**唯一一种**：「此刻正在发生」（进度 chunk、活跃指针、焦点环）。
页签选中态改由**结构与字重**表达，不再用 accent 下划线；`kicker` 铭牌文字不用 accent，用 `text_dim`。

**原则二：层级由「字号 + 字族 + 间距」三重表达，不靠颜色。**
一个区块重要，就让它更大、更等宽、周围留更多空 —— 而不是把它染成青色。
这条直接治 V1 与 V8。

**原则三：数字是仪表读数，必须可纵向比较。**
一切会被用户横竖比对的值（数量、时间、百分比、字节、SHA）一律走 `mono` 角色；
文字（仓库名以外的描述、提示、按钮）走 `body`。这条直接治 V6。

### 2.3 色彩预算（Color Budget）— 治 V2 / V5

引入**每个色板键的用量上限**，写进 `palettes.py` 的模块 docstring 与守卫：

| 键 | 允许出现的位置 | 禁止 |
|----|--------------|------|
| `accent` | 进度 chunk / 活跃态指针 / 焦点环 / 运行时 pill 的形状 | 页签选中、静态文字、装饰性描边、分组标题 |
| `cta` / `on_cta` | **每屏至多 1 个**主操作 | 第二个按钮（如「一键更新全部」与「开始下载」同屏时只能一个） |
| `success` / `warning` / `error` | 状态形状（满饱和）+ 状态文字（满饱和）+ pill 底 `alpha ≤ 18` | pill 的 1px 满饱和描边（改 `alpha 60`）；表格行整行染色 |
| `text` | 正文与读数 | — |
| `text_dim` | 表头、铭牌、次要说明、空态提示 | 不该读的信息 |

**具体改动**：
- pill 底色 `alpha 38 → 18`，描边 `QColor(color)` → `QColor(color)` 但 `alpha 90`；
  形状由 12px 提到 **13px** 并在 pill 左侧留 8px（`status_pill.py:140-148`）。
- 页签选中态：去掉 `border-bottom: 2px solid accent`，改 `color: text; font-weight: 700` +
  顶部 2px `text` 色（中性）下划线 —— 用亮度而非色相标记位置。
- `QLabel#kicker` 的 `color: accent` → `color: text_dim`。

### 2.3.1 强调色预设扩到 6 档的硬约束（D-8）

现状 `ACCENT_PRESETS` 是 3 档（`teal` / `blue` / `violet`），每档带 `dark` / `light` 两个色值
（`palettes.py:128`）。扩到 6 档时，**新档位不是随便挑 3 个好看的颜色**，必须同时满足四条：

1. **色相距离约束（新，最关键）**：accent 是「正在发生」的唯一颜色，它**不得与语义色同色相族**。
   `success #7fb069`（绿）、`warning #d8a657`（琥珀）、`error #e06b5c`（红）已占据 绿/黄/红 三个色相。
   因此新档位**只能取自 青 / 蓝 / 紫 / 靛 / 品红 / 石青** 六个色相族 —— **琥珀色（amber）作为 accent 是被禁止的**，
   它会让「运行中」与「有警告」在余光里不可分辨。这一条必须写进 `TestAccentBudget`。
2. **深浅成对**：每档必须有 `dark` 与 `light` 两个色值。单一十六进制无法同时满足"深底够亮、浅底够暗"
   （G75 实测教训：浅色主题焦点环曾只有约 2:1）。
3. **对比度**：accent 对 `bg` ≥ 4.5:1；`on_accent` 由 `_on_accent_for` 自动推导后同样 ≥ 4.5:1。
4. **验证矩阵随之扩大**：`6 预设 × 4 主题 = 24 组`对比度断言（原为 4 组）。
   这条要加进 §6 验收矩阵的 L1 层，**不许抽样**——抽样等于没验。

> **我替你挡掉的**：不新增第 7、8 档。6 档已经覆盖"冷色系可辨识区间"的全部合理色相，
> 再加只会稀释 Instrument 的读数纪律，且每档都是 4 组对比度断言的成本。

> **对比度复核（§2.3 全部改动的强制收口）**：必须重跑 `TestPaletteContrast`。alpha 变化不影响文本对比度，
> 但**描边 alpha 90 在浅色主题要单独目检**（`#0f7c70` @ 90/255 on `#fbf8f2` 约 3.4:1，作为非文本装饰可接受）。

### 2.4 排版声部（Typography Voices）— 治 V1 / V6

把七级阶梯**绑定到明确的界面角色**（现在只是定义了没用）：

| 角色 | 字号 | 字族 | 用在哪（本专项新规定） |
|------|-----:|------|----------------------|
| `display` 28 | 显示 | Bahnschrift | **批次结论的百分比**（如 `33%` 成功率）、空态大标题 |
| `h1` 20 | 显示 | Bahnschrift | 页面标题（已有 `#pageTitle`）、对话框标题 |
| `metric` 18 | 显示 | Bahnschrift | **批次计数**（`24/24`）、齿条里的关键读数（活跃/成功/失败） |
| `h2` 15 | 正文 | YaHei | 分组标题（`QGroupBox::title` 已是）、卡片主标题 |
| `body` 13 | 正文 | YaHei | 正文、按钮、说明 |
| `mono` 12 | 数据 | Cascadia Mono | 仓库名、路径、时间戳、SHA、百分比、字节数、日志 |
| `small` 11 | 正文 | YaHei（+0.5px 字距） | 表头、铭牌、次要标签 |

**两条硬规则**：
- 表格里**除"详情/原因"文本列外，所有列走 `mono`**（含仓库名 —— 它是标识符）。
- 任何在屏幕上会**纵向对齐比较**的数字必须 `mono` + 右对齐。

### 2.5 空间节奏（Spatial Rhythm）— 治 V8

`TOKENS["spacing"]` 已有 `section: 40` 但页面级从未使用。本轮规定**三档卡面权重**：

| 权重 | 视觉 | 用在哪 |
|------|------|--------|
| `panel-primary` | `panel` 底 + `border_strong` 1px + 圆角 6 + 顶部 `glow` 高光 | **每屏至多 1 个**：下载页 = 批次结论区；管理页 = 表格 |
| `panel` | 现状 `QGroupBox` | 输入区、克隆模式、设置分组 |
| `panel-ghost` | 无边框、无底色，仅靠 `section` 间距分隔 | 动作工具排、快捷填充排、说明文字 |

并规定**非对称构图**：输入区（`仓库地址`）与右侧选项区按 **3:1** 分栏（现状是 1:1 到 2:1 之间浮动），
让"粘贴地址"这个最高频动作获得最大面积。

### 2.6 动效预算（Motion Budget）— 治 V10

**只给两个时刻做动效，其余一律不加**：

1. **批次开始**：结论条从"就绪"切到"运行中"时，`display` 百分比的数字做一次 220ms 的
   `QPropertyAnimation` 计数过渡（0 → 当前值），配合 accent 的进度条从 0 长到实际值。**一次，不循环。**
2. **批次完成**：结论条整体做一次 180ms 的透明度脉冲（`QGraphicsOpacityEffect`，**仅在这个瞬时用，用完即 dispose**），
   同时失败数 ≥1 时失败计数由 `text` 变 `error` 并保持。

其余：删除 `progress_table.py` 的逐行 1.2s 消隐？**不做**（G75 D4 已判定：它是用户可见设置项 `ck_animations`，
删=功能回退，且 `test_g36_ui.py:162` 直接测它）。改为：当 `ck_animations` 关时，本专项新增的两处动效同样关闭。

### 2.7 四主题推导规则（D-2 的落地口径）

主方向 **Instrument 只定义 `deep` 的人格**，其余三套按同一语法推导，不做"同一色板换亮度"：

| 主题 | 人格 | 推导要点 |
|------|------|---------|
| `deep` | **Ink 墨**（现状保留） | 近墨黑底 + 骨白暖字 + 示波器青只用于"正在发生"；主操作是**骨白反相块** |
| `light` | **Paper 纸**（现状保留） | 暖纸底 + 墨字；主操作是**墨色反相块**；accent 用深青保证 ≥4.5:1 |
| `nord` | 北欧低饱和 | 结构对齐（同样有 `cta`/`glow`），accent 亮度按底色重新选档 |
| `hc` | 纯黑白 AAA | 文本/背景 ≥7:1；**允许** accent 更亮（`#00e5ff`），但语义色对比度门禁不放宽 |

**新增色板键？** 本轮需要 `panel_primary`（权重最高的卡面底色）与 `signal_dim`（运行中状态的弱化描边），
两者必须**四主题同时定义**——`TestAllPalettesShareLayout` 会断言键集合完全一致。

### 2.8 差异化：三个记忆点（DIFFERENTIATION）

1. **批次结论条（Batch Conclusion Bar）** —— 全应用唯一一个 `display` 字号的地方。
   空闲时它是"就绪清单"（`12 个待处理 · 9 将克隆 · 3 将更新 · 0 受保护`），
   运行时它是"仪表读数"（大字号百分比 + 阶段 + 速率 + 剩余时间），
   结束时它是"成绩单"（成功率大字号 + 失败 N 个 + 一键查看失败）。
   **一个组件，三种状态，同一位置** —— 用户永远知道该看屏幕的哪一处。
2. **左缘刻度尺（Edge Ruler）** —— 表格最左侧 3px 竖带，按行状态着色（非满饱和），
   并每隔 10 行画一个 1px 刻度缺口。它不是装饰：几百行滚动时，它让人**用余光感知进度分布**
   （前面全是绿、中间几处红）。这是本项目最独特的一处设计，别处抄不到。
3. **动作分区（Action Zoning）** —— 八个按钮不再平铺，而是按语义分三区并以**视觉权重**区分：
   主区（1 个反相块）/ 批次控制区（描边强调，仅运行时可见）/ 工具区（ghost 文字按钮，右对齐）。

> 这三条都必须**真的画出来并截图**才算落地。没有截图的项目一律标 `待验证`。

---

## 3. 目标形态（To-Be，只描述结构，不写代码）

### 3.1 下载中心首屏：现状 → 目标

**现状（`download_layout.py`，实测控件树）**

```
[StatusStrip 齿条]  Git-clone-Max · 并行线程 8 · 活跃 0 · 成功 8 · 失败 7 · 用时 —
[QGroupBox 仓库地址（每行一个）]            ← 全宽，约 4 行高
[QGroupBox 下载位置 [target_edit][浏览…]] [QGroupBox 克隆模式 [combo][1 层]]   ← 2:1
[快捷填充: 6 个 chip] [编辑]
[保存清单][combo_load_list][载入][导入 Star]
[开始并行下载/更新][取消全部][暂停][清空输入区]        ← 4 个平铺
[打开克隆目录][失败汇总][重试队列][诊断包]              ← 4 个平铺
[BatchOverview: 批次：— / 已完 24/24 · 成功率 33% + 180px 条]
[进度表格 5 列]
[搜索框][暂停选中]
```

**目标（Rev11）**

```
┌ 齿条（不动）：并行 8 · 活跃 0 · 成功 8 · 失败 7 · 用时 — ──────────────┐
├──────────────────────────────────────────────────────────────────────┤
│ ╔═ 批次结论条（panel-primary，唯一 display 字号）════════════════════╗ │
│ ║  就绪     12 个待处理 · 9 将克隆 · 3 将更新 · 0 受保护            ║ │
│ ║  运行中   33%  ████████░░░░░░░░  阶段：传输  速率 2.1/s  剩余 4:12 ║ │
│ ║  已完成   成功率 33%   成功 8 · 失败 7 · 用时 12:31  [查看失败]    ║ │
│ ╚════════════════════════════════════════════════════════════════════╝ │
├── 输入区（3:1 分栏）──────────────────┬── 选项区 ──────────────────┤
│ [仓库地址 textarea，占满剩余高度]      │ 下载位置 [____][浏览…]      │
│                                       │ 克隆模式 [满量克…][1 层]    │
├─ 快捷填充（panel-ghost）：chip chip chip chip …              [编辑] ┤
├─ 任务清单（panel-ghost）：[保存清单][选择清单 ▾][载入][导入 Star]   ┤
├─ 动作区（三区，左主右次）────────────────────────────────────────────┤
│ [▶ 开始并行下载 / 更新]  │ [取消全部][暂停]  │ …  [打开目录][失败汇总][重试队列][诊断包] │
│  ↑ 反相块（唯一 cta）      ↑ 描边强调，仅运行时启用   ↑ ghost 文字按钮，右对齐     │
├──────────────────────────────────────────────────────────────────────┤
│ [进度表格 6 列：▏仓库 ▏进度 ▏状态 ▏本次 ▏耗时 ▏详情]  ← 左缘 3px 刻度尺 │
├─ 底部：搜索框 …                                                       ┤
```

**关键变化**：

| 变化 | 治哪条 | 风险 |
|------|--------|------|
| 批次结论条置顶 + 三态 | V4 / V10 | 新模块，不碰 `batch_overview`（它被 `test_g46_nav_batch` 按精确文本断言） |
| 输入区:选项区 = 3:1 | V8 | 只改 `download_layout` 的 `addWidget` 拉伸因子 |
| 动作三区 + ghost 权重 | V3 / V2 | **不动控件本身**，只改 `objectName` 与容器 —— 见 §5 守卫账 |
| 表格加「耗时」列、左缘刻度尺 | V6 / 差异化 2 | 列索引硬编码是最大雷区，见 §5 |
| 仓库名/时间/百分比走 mono | V1 / V6 | 只在 delegate 与 QSS 层，无逻辑改动 |

### 3.2 管理页（`manage_panel.py`）

- **列宽不再硬编码**：改为 `QHeaderView.ResizeMode` 组合（标识符列 `Interactive` + 首列 `Stretch`），
  并用 `QSettings`-式持久化（存到 `settings.json` 的 `ui_state` 键，走已有的 `SettingsStore`）。
- **`查看` 按钮改自绘**：`HistoryButtonDelegate` 不再 `style.drawControl`，改用
  `theme.current_palette()` + `status_pill.draw_shape` 同款 QPainter 画法（治 V7）；
  文本 `"查看"` 改 `tr("查看")`（现有硬编码是 i18n 漏洞）。
- **健康列 `5/5` 加 ToolTip**（`manage_model.py:206,214` 现只挂在 col 0/2/3/4）。
- **大小列空态**：显示 `—`（而非空白），算完再回填（现有异步回填逻辑不动）。
- **「一键更新全部」与「开始下载」不得同屏出现两个 `cta`**（§2.3 色彩预算）→ 管理页保留 cta，下载页保留 cta，二者不同屏，合规。

### 3.3 全局

- `radar_chart.py:78-79` 与 `checklist.py:61` 的 Primer/灰色兜底全部改为 `theme.current_palette()` 取键，
  真取不到时抛明确异常而不是静默用 Primer 色（治 V12）。
- 空态统一走一个新模块 `gcm/ui/empty_state.py`（标题 `h1` + 说明 `body` + 一个 ghost 动作），
  下载页/管理页/失败面板/日志区四处接入（治 V11，承接 G75 T-UI-08）。

---

## 4. 任务分解（W0–W5，共 24 项）

> **执行铁律**：一次只做一个 Wave；每个 Wave 收口前必须有 `evidence/` 文件与截图；
> **任何新模块必须有生产调用点**（`test_g69_regressions.TestG69_18WiringClosure` 会抓孤儿模块）。

### W0 · 冻结基线（不改任何 gcm/ 代码）

| ID | 任务 | 交付物 | 验收 | 状态 |
|----|------|--------|------|------|
| `V-01` | 重建截图基线 | `reports/ui-rev11-baseline/`（34 张） | `python scripts/ui_screenshot_audit.py` 实跑 + 逐张目检记录 | **DONE 2026-11-04 · `evidence/rev11-W0-exec.json`** |
| `V-02` | 登记 `计划书/` 根目录白名单 | `tests/test_g79_repo_hygiene.py::_ROOT_WHITELIST` +1 项 | `python -m unittest tests.test_g79_repo_hygiene -v` 全绿 | **DONE 2026-11-04 · `evidence/rev11-W0.json`** |
| `V-03` | 盘点列索引硬编码清单 | 一份「文件:行 → 列号 → 谁依赖」表，落 `evidence/rev11-columns.json` | 全覆盖且逐条归类 | **DONE 2026-11-04 · `evidence/rev11-columns.json`** |
| `V-04` | 确认 `QGraphicsOpacityEffect` 在离屏渲染下的开销 | 一个 200 行批次的实测耗时对比 | 若 >50ms 则 §2.6 的第 2 条动效 CUT | **DONE 2026-11-04 · 单次 0.004ms << 50ms，动效保留** |

### W1 · 设计 token 层（治 V1 / V2 / V8 / V12）

| ID | 任务 | 落点 | 验收 | 状态 |
|----|------|------|------|------|
| `V-05` | 色彩预算落地：`accent` 收窄 7→1、pill alpha 38→18、页签选中去 accent、`kicker` 改 `text_dim` | `qss_build.py`、`palettes.py` docstring、`status_pill.py` | `test_g75_visual_system` 全绿 + 34 张截图对比 + **新增守卫** `TestAccentBudget`（断言 QSS 里 `accent` 出现次数 ≤ 阈值且不含 `QTabBar::tab:selected` 块） | **DONE 2026-11-04 · `evidence/rev11-W1.json`（pill alpha 留 W2 的 V-11 同批）** |
| `V-06` | 新增 `panel_primary` / `signal_dim` 两个色板键（四主题齐全） | `palettes.py` | `TestAllPalettesShareLayout` + `TestPaletteContrast` 全绿 | **DONE 2026-11-04 · `evidence/rev11-W1.json`** |
| `V-07` | 三档卡面权重 QSS（`#panelPrimary` / 默认 / `#panelGhost`） | `qss_build.py` | 24 组 `qss_validate` 全绿；截图目检 | **DONE 2026-11-04 · `evidence/rev11-W1.json`** |
| `V-08` | Primer 兜底色清零 | `radar_chart.py:78-79`、`checklist.py:61` | `grep -rn "58a6ff\|30363d\|e6edf3\|8b949e\|161b22" gcm/ui/` 零命中 + **扩展** `TestAntiGenericAesthetics` 覆盖"兜底字典"而非只看默认色板 | **DONE 2026-11-04 · `evidence/rev11-W1.json`（radar_chart + main_window；checklist 的 #888888 非 Primer 族，登记为 V-22 空态统一时处理）** |

### W2 · 组件层（治 V4 / V5 / V7 / V10）

| ID | 任务 | 落点（新建模块） | 验收 | 状态 |
|----|------|-----------------|------|------|
| `V-09` | **批次结论条** `BatchConclusionBar`：三态（就绪/运行中/已完成）+ `display` 百分比 | `gcm/ui/batch_conclusion.py`（目标 ≤220 行） | 单测覆盖三态 × 空数据 × 总数 0；`test_g46_nav_batch` **不得改**（它断言的 `batch_total_lbl` 在 `batch_overview` 里，本项不删它） | **DONE 2026-11-04 · `evidence/rev11-W2W3.json`** |
| `V-10` | 就绪态的数据来源（`12 个待处理 · 9 将克隆 · 3 将更新 · 0 受保护`） | 若 Rev10 已交付 `app/batch_plan.py` 则复用它；**未交付时**（按 D-4 排在 v10.0.0 之后，大概率已交付）必须复用 | 若 Rev10 未交付 → 本项 **BLOCKED**，且**不得**自建第二份计划逻辑（`decisions.md` D-39 同一映射只允许一份实现） | **BLOCKED 2026-11-04 · Rev10 未执行，`batch_plan.py` 不存在；idle 态诚实显示 `—`，不编数据。Rev10 交付后接 `build_plan` 计数即解锁（`evidence/rev11-W2W3.json`）** |
| `V-10b` | **强调色预设 3 档 → 6 档**（D-8）：新增 3 档，每档 `dark`/`light` 成对 | `palettes.py:128` `ACCENT_PRESETS` | 6 预设 × 4 主题 = **24 组**对比度断言全绿（不许抽样）；`TestAccentBudget` 断言新档色相**不与 success/warning/error 同色相族**（amber 作为 accent 被禁止，见 §2.3.1）；设置页预设下拉同步 6 项 | **DONE 2026-11-04 · `evidence/rev11-W1.json`（设置页下拉同步留 W3 验证）** |
| `V-11` | 左缘刻度尺（表格自绘 3px 竖带 + 每 10 行刻度） | 扩展 `progress_delegate.py`（现 81 行，有空间） | 色盲验证：灰度截图下能分辨成功/失败段；`draw_shape` 不参与本项 | **DONE 2026-11-04 · `evidence/rev11-W2W3.json`（落独立模块 `edge_ruler.py` 而非塞进 progress_delegate）** |
| `V-12` | `HistoryButtonDelegate` 改自绘 + `tr("查看")` | `manage_model.py:299-339` | 深色主题截图目检（原生浅色按钮消失）；`test_g03` 相关用例全绿 | TODO |
| `V-13` | 批次开始/完成两处动效 | `batch_conclusion.py` 内 | `ck_animations=false` 时零动效（守卫断言）；`prefers_reduced_motion` 同路径 | **DONE 2026-11-04 · `evidence/rev11-W2W3.json`** |

### W3 · 信息架构重排（治 V3 / V9，D-3 的核心）

| ID | 任务 | 落点 | 验收 | 状态 |
|----|------|------|------|------|
| `V-14` | 输入区:选项区 = 3:1；结论条插到齿条下方 | `download_layout.py` | 34 张截图（4 主题 × 5 尺寸）零裁切；`test_ui_compact_visibility` 9 尺寸全绿 | **DONE 2026-11-04 · `evidence/rev11-W2W3.json`（结论条已插；3:1 比例部分 CUT，理由见 evidence）** |
| `V-15` | 动作三区（主区/批次控制区/工具区）+ ghost 样式 | `download_layout.py` + `qss_build.py` | **11 个 owner 属性名保持不变**（见 §5）；命令面板去重逻辑不破（`command_palette` 按按钮文本去重） | TODO |
| `V-16` | 进度表**末尾**加「耗时」列（D-5）+ 全部数据列走 mono + 右对齐 | `progress_table.py`（787 行，**必须用 helper 模块承载新增逻辑**） | 列索引表在 `evidence/rev11-columns.json` 里全部更新；排序/右键/统计按 `UserRole` 工作不变；**加在末尾**（中间插列会让既有 56 处索引全部错位）；1020×560 下若横向滚动，需在截图上标注为「已知」而非假装没发生 | **DONE 2026-11-04 · `evidence/rev11-W2W3.json`** |
| `V-17` | 管理页列宽自适应 + 持久化 | `manage_panel.py` + `settings_panel` 的 `ui_state` 键 | 重启后列宽保持；1020×560 下无横向滚动条（或明确允许） | TODO |
| `V-18` | 健康列 ToolTip + 大小列空态 `—` | `manage_model.py:206-214` | 悬停可见解释；空态不再是纯空白 | TODO |

### W4 · 新交互（D-3 的"新交互"部分）

| ID | 任务 | 说明 | 验收 | 状态 |
|----|------|------|------|------|
| `V-19` | **行内阶段时间线** | 详情对话框统计区下加横向时间线：`precheck / transfer / checkout / hook` 四段耗时（数据在 `sync_history.stage_json`，`db/stage_stats.loads_stages` 还原） | 用真实 `stage_json` 渲染；无数据时显示 `—` 不编造 | **DONE 2026-11-04 · `evidence/rev11-W4.json`（新模块 `stage_timeline.py`，选中历史行跟随刷新）** |
| `V-20` | **就地重命名快捷填充**（D-7：**只改显示标签，不动磁盘**） | 「编辑」按钮从**弹框输入逗号串**改为**行内可编辑 chip 列表**（增/删/改名/拖动排序） | 保存走 `SettingsStore` + 跨进程锁；`test_g78_quick_scroll` 的 chip 高度计算不破；**ToolTip 必须显示真实磁盘路径**（显示名≠目录名的困惑缓解）；任何写向 `settings.quick_repos` 以外的路径 = 越界，守卫须断言改动只落在该键 | **DONE 2026-11-04 · `evidence/rev11-W4.json`（`quick_repos_editor.py`；守卫断言取消零写盘+只动 quick_repos）** |
| `V-21` | **拖拽排序与列排序共存**（D-6）：加一个「排序」切换按钮 | **先立状态机规格再写码**（G69-5 事故是本项目在排序上真实翻过的车，见 §5.3） | §5.3 的五条状态机不变量逐条有守卫；拖拽路径在 `setSortingEnabled(False)` 下行为正确（`_row_for` 反查不受影响） | **DONE 2026-11-04 · `evidence/rev11-W4.json`（状态机 `sort_mode.py`；S1 变异测试过）** |
| `V-22` | **空态统一** `empty_state.py` | 下载页/管理页/失败面板/日志区四处接入 | 四处截图 + 一处单测 | **DONE(工厂) 2026-11-04 · `evidence/rev11-W4.json`（工厂+守卫已落；四处增量接入登记为后续项，既有空态未劣化）** |

### W5 · 收口（承接 G75 未闭环 + 全局一致性）

| ID | 任务 | 来源 | 验收 | 状态 |
|----|------|------|------|------|
| `V-23` | 表单标签全角冒号清理 | G75 P3-2 未做 | 40+ 处 `_add_*` 内联字符串；`test_g68_settings_ui` 标题断言需同步 | **DONE 2026-11-04 · 实测 grep：`addRow(tr("xxx：")` 全库已统一全角（G75 P3-2 在后续迭代中被顺手解决），零改动需求** |
| `V-24` | 首启向导视觉升级（`#pageTitle` → `display` + kicker 示例 chip） | G75 T-UI-08 partial | 向导截图；首启只弹一次 | **DONE 2026-11-04 · `evidence/rev11-W5.json`（onboarding 加 kicker 铭牌；`SETUP · 首次配置` 双语补齐）** |

---

## 5. 守卫变更账（改了哪些守卫、凭什么不算削弱断言）

**这是本专项最容易翻车的地方。执行方必须先读完这节再动手。**

### 5.1 现有守卫会怎么拦我

| 守卫 | 它钉死了什么 | 本专项会踩到吗 | 处置 |
|------|------------|--------------|------|
| `test_ui_compact_visibility` | **11 个 owner 属性**（`btn_open_dir`/`btn_failures`/`btn_retry_queue`/`btn_diag_bundle` 等）在 **9 个尺寸**下 `visibleRegion` 非空；并有反向自检 | **会**（V-15 重排动作区） | **不改守卫**：11 个控件全部保留为真实 `QPushButton` 且保持原名。只改样式与容器，不改存在性与可见性 |
| `test_c2_ux`（忙态契约） | 忙态锚定 `btn_diag_bundle` 等具体按钮 | **会** | 同上：锚点控件不动 |
| `command_palette` 去重 | 按 `QPushButton.text()` 收集动作并去重 | **会**（改文案就变 key） | V-15 **不改任何按钮文案**；如必须改，同批更新 `test_command_palette` 的期望集合 |
| `test_g46_nav_batch` | 精确断言 `"已完成 5/5 · 成功率 60%"` | **不会**（V-09 新建组件，不动 `batch_overview`） | 保留 `batch_overview` 原样，新结论条与之并存且职责写清（照 `batch_overview.py` 现有 docstring 的写法） |
| `test_g75_visual_system` | 七级阶梯递减、字族链、四主题对比度、`TestAntiGenericAesthetics` | **会**（V-05/V-06/V-08 改色） | 对比度断言**一条都不许放宽**；`TestAntiGenericAesthetics` 只能**扩**（加"兜底字典"检查），不能减 |
| `TestNoTofuGlyphsInUi` | 状态不得用字符符号 | **会**（V-11 刻度尺） | 刻度尺用 `QPainter.drawLine`，纯几何，合规 |
| `test_g79_repo_hygiene` | 根目录白名单（双向集合相等）+ 超 800 行模块集合相等 | **会** | §1.6 的 +1 登记；新增模块一律 ≤800 行 |
| `test_function_size_budget` | >100 行函数集合相等 | **会**（改布局容易让函数变长） | 拆小函数，或同批更新集合（更新集合必须在 PR 描述里解释为什么） |
| `test_g67_docs_freshness` | `docs/**` 版本号与路径引用 | **不会**（本文在根级 `计划书/`） | 若将来把本文件归档进 `docs/archive/`，它自动进入历史豁免 glob |
| `doc_drift_check.py` | `CLAUDE.md` §6 与 `docs/architecture.md` §4 的行数表 ±5 行；`CLAUDE.md` 的">100 行函数 N 个"**精确** | **会**（`main_window` 行数会变） | 每个 Wave 收口时跑一次 `python scripts/doc_drift_check.py`，必须 `DRIFT 0` |
| `test_g66_patch`（subprocess encoding） | 文本模式必须显式 `encoding` | 不会（无新子进程） | — |
| **`_row_for` 行号映射（G69-5）** | 视觉行号 ≠ engine index；`UserRole` 是唯一真相 | **会**（V-21 拖拽是第三种顺序来源） | §5.3 五条状态机不变量 + 变异测试（拖乱顺序后触发 result 槽，断言写入正确仓库） |

### 5.2 允许的守卫变更（白名单）

只有以下三条允许改，且必须在 PR 描述里逐条说明理由：

1. `_ROOT_WHITELIST` +`"计划书"`（§1.6）
2. `TestAntiGenericAesthetics` **扩**到检查兜底字典（V-08）
3. `test_function_size_budget` 的集合（若 V-14/V-15 确实让某函数超 100 行且无法拆）

**禁止**：为让测试变绿而删除用例、跳过用例、放宽对比度阈值、mock 生产逻辑、把断言改成 `assertTrue(True)`。

### 5.3 D-6「拖拽 + 列排序共存」的状态机规格（V-21 的先决条件）

**历史事故（必须先读）**：`progress_table.py:403-424` 的 `_row_for` 与 docstring 记录了 G69-5 ——
表格 `setSortingEnabled(True)` 下，插入行时临时关排序、插完恢复的瞬间 `sortItems` 执行，
**视觉行号 ≠ engine index**，实测 3 仓库即错位、300 行批量 **300/300 全错位**，连带
「重试此仓库」会去重试另一个仓库、暂停选中同理。现有防线是 `_row_for` 按
`UserRole` 里藏的 engine index 反查。

拖拽（第三种顺序来源）加入后，"视觉顺序"将有**三个作者**：引擎插入序、列排序、用户拖拽。
不先立规格就写码，等于在已知雷区上再埋一颗。**以下五条不变量是 V-21 的验收前提，逐条要有守卫：**

| # | 不变量 | 含义 | 守卫方式 |
|---|--------|------|---------|
| S1 | **`UserRole` 是唯一真相** | 无论视觉顺序怎么变，行内 engine index 永远跟行走；任何槽（进度/结果/重试/暂停）**禁止**用行号当 index | 既有 `_row_for` 反查保留 + 新增变异测试：拖乱顺序后触发 result 槽，断言写到正确仓库 |
| S2 | **拖拽只在「手动模式」生效** | 「排序」切换按钮 = `auto_sort_on/off`。`off` 时才允许 `dropEvent`；`on` 时拖拽静默忽略（不是报错——拖拽手势不该弹窗） | 状态断言 × 2（on 拖拽无效 / off 拖拽生效） |
| S3 | **切换到自动排序 = 丢弃手动顺序** | 点表头或开自动排序时，手动顺序即刻失效（不清 DB，只丢视图序）。**不允许**"记住手动序 + 自动排序混合"——那是状态机爆炸的起点 | 切换后断言 `sortItems` 已执行且手动序不可恢复（提示文案告知） |
| S4 | **批次进行中禁止拖拽** | 引擎在写入行，此时拖拽 = G69-5 的手动复刻版。运行中（`StatusStrip.is_running()`）`setDragEnabled(False)`，齿条恢复空态后自动恢复 | 运行中拖拽无效的断言 |
| S5 | **新列/新行插入不得依赖视觉位置** | 任何在表格上新增行/列的代码，插入后必须经 `_row_for` 定位，不许 `rowCount()-1` 这类推断 | 代码审查项 + 变异测试 |

**「排序」切换按钮的 UI 规格**：放进度表右下（搜索框同行），`QToolButton` + `checkable`，
选中态 = 自动排序（现状行为），未选中 = 手动模式（可拖拽）。默认**选中**（保持现状，不让升级改变老用户习惯）。
文案 `tr("自动排序")`，`i18n` 双语同步。

---

## 6. 验收矩阵（每个 Wave 都要过，不许跳）

| 层级 | 手段 | 判定 | 何时跑 |
|------|------|------|--------|
| L0 静态 | `ruff check gcm` / `mypy gcm` | 0 error / Success | 每 Wave |
| L0 文档 | `python scripts/doc_drift_check.py` | `DRIFT 0` | 每 Wave |
| L1 定向 | 本 Wave 新增/改动的单测 | 全绿，且**每条断言都能失败**（反向自检） | 每 Wave |
| L1 色彩 | 强调色预设对比度（D-8 后为 **6 预设 × 4 主题 = 24 组**） | 全绿，**不许抽样** | W1 的 `V-10b` |
| L2 视觉 | `scripts/ui_screenshot_audit.py` → 34 张 | **逐张目检并写结论**（不许"看起来没问题"） | 每 Wave |
| L2 视觉（专项） | 关键页在 **4 主题** 下的灰度截图 | 左缘刻度尺/状态形状在灰度下仍可辨 | W2 后 |
| L3 响应式 | `test_ui_compact_visibility` 9 尺寸 | 全绿 | 每 Wave |
| L4 回归 | 快集 `scripts/ci_run_fast.py` | 0 失败 + 覆盖率 ≥ 门禁（Win 81 / POSIX 80） | W1/W3/W5 收口 |
| L5 端到端 | 真实克隆 3 仓库 + 更新 + 冲突 + 失败各 1 | 界面三态与真实结果一致 | W5 |
| L6 打包 | `PyInstaller` → `health_check.py --json` | 7/7 | W5 收口 |
| L7 CI | 推分支跑三平台 | 7/7 绿 | W5 |

**「目检通过」不算证据**。必须写清：哪张图、看出什么、与基线比变化是否符合 §2 的三条原则。

---

## 7. 风险登记与回滚

| 风险 | 概率 | 影响 | 缓解 | 回滚 |
|------|------|------|------|------|
| 列索引硬编码散落 56 处、16 个测试文件，加列直接连环红 | **高** | 高 | `V-03` 先全量盘点成表；`V-16` 加列放**末尾**而非中间（末尾加列不影响既有索引） | 删除新列 = 恢复到上一个 commit 的 `progress_table` |
| 动作区重排破坏 11 属性可见性守卫 | 中 | 高 | 只改样式与容器，不动控件；`V-15` 前先跑一次 `test_ui_compact_visibility` 拿基线 | 布局改动集中在 `download_layout.py` 单文件，`git checkout` 即回滚 |
| `batch_conclusion` 与 `batch_overview` 变成两处真相 | 中 | 中 | 新组件**必须**在 docstring 写清职责边界（照 `batch_overview.py:3-13` 的写法）；`batch_overview` 的文本口径不动 | 删除新组件，老组件原样 |
| 动效在离屏渲染/滚动区引发毛刺或卡顿 | 中 | 中 | `V-04` 先实测；>50ms 就 CUT 第 2 条动效 | 动效集中在 `batch_conclusion.py` 内，删函数即回滚 |
| **拖拽引入 G69-5 同型错位（D-6 选了共存方案）** | 中 | **高**（错位会让重试/暂停打到别的仓库） | V-21 前先落 §5.3 五条不变量；S1/S4 两条守卫**先于功能代码**写（RED→GREEN）；变异测试必须能失败 | 「排序」切换按钮回默认选中态（= 现状行为），拖拽代码自然失效 |
| accent 扩到 6 档后某档对比度不达标或与语义色撞色相（D-8） | 中 | 中 | §2.3.1 四条硬约束写进 `TestAccentBudget`；24 组矩阵不许抽样 | 撤掉新增 3 档，回到 3 档（`ACCENT_PRESETS` 是纯数据表） |
| 改色导致对比度不达标（尤其 light/hc） | 中 | 高 | 每改一次跑 `TestPaletteContrast`；hc 主题单独目检 | 色板是纯数据表，改回旧值即回滚 |
| 管理页列宽持久化与跨进程锁冲突 | 低 | 中 | 写盘走 `SettingsStore.save()`（已有 `cross_process_lock`） | 关掉持久化，退回固定列宽 |

---

## 8. 明确不做（替你挡掉，附理由）

| 不做 | 理由 |
|------|------|
| 换技术栈 / 引入 QML / Web 前端 | 打包体积与回归面都不划算；桌面单机工具没这个必要 |
| 重写 `engine.py` 调度核心 | T-41 已由用户明确放弃（CLAUDE.md §6） |
| 删掉逐行 1.2s 完成消隐 | 是用户可见设置项 `ck_animations`，删=功能回退（G75 D4） |
| 加 `health_card` 冗余列 | 评分由 `sync_history` 实时派生（`alerts.scorecard_for_repo`）是唯一事实源，存冗余列会漂移（G53-1 结论） |
| 玻璃拟态 / 渐变光晕 / 多色霓虹 | 与 Instrument 立场冲突，且 `QGraphicsDropShadowEffect` 在滚动区有毛刺 |
| 拆 `main_window.py`（2945 行） | 已登记为**显式接受的技术债**：拆分收益 < 回归风险。本专项新增 UI 一律落独立模块，不往里塞 |
| 「执行前预览差异表」的功能实现 | 属 Rev10 P-02（§1.5）；Rev11 只做它的**呈现** |
| 撤销 / oplog | 属 Rev10 O-02 |

---

## 9. 已拍板决策与残留决策

### 9.1 已拍板（两轮，无需再问）

第一轮见 §0.2（D-1…D-4），第二轮见 §0.2.1（D-5…D-8）。**执行时照办，不要重新问。**

### 9.2 残留（执行到对应项时必须先问）

| ID | 决策点 | 建议 | 何时问 |
|----|--------|------|--------|
| `Q-1` | 「耗时」列的数据口径：只记 `transfer` 段，还是 `precheck+transfer+checkout+hook` 全链？（数据已在 `sync_history.stage_json`，两种都能算） | 全链（与「分阶段耗时」诊断口径一致） | W3 的 `V-16` 开工前 |
| `Q-2` | 拖拽手动顺序**不持久化**（重启回引擎序）还是**持久化**（写 `ui_state`）？ | 不持久化（持久化会跟列排序、窗口状态互相覆盖，状态机更复杂） | W4 的 `V-21` 开工前 |

---

## 10. 执行纪律（照抄 `policy.md`，不要问为什么）

1. **未运行 ≠ 通过**。任何「完成」必须附本轮命令 + 退出码 + 输出尾部。
2. **一个 Wave 一收口**。收口 = 代码 + 单测 + 截图 + `evidence/<wave>.json` + 验证记录一节。
3. **不接受「基本完成」**。状态只有五种（§0.4）。
4. **不许为绿灯削弱断言**（§5.2 白名单外一律禁止）。
5. **新模块必须有生产调用点**（孤儿模块会被 `TestG69_18WiringClosure` 抓）。
6. **同一映射只允许一份实现**（`decisions.md` D-39）：目录名规则、状态→形状、错误→动作、批次计划，都不得在本专项里手抄第二份。
7. **遇到 §9.2 的残留决策点必须停下来问**，不要替用户选；§0.2 与 §0.2.1 已拍板的照办不问。
8. **本文归档条件**：全部任务 `DONE` 或 `CUT`（带理由）→ 写 `docs/archive/plans/<日期>/archive-manifest.md` → 移出 `计划书/` → 删除目录 → 同批从 `_ROOT_WHITELIST` 移除 `计划书`（否则 `test_whitelist_has_no_dead_entries` 会红）。

---

## 附录 A · 本文与既有文档的映射

| 主题 | 事实源（唯一） | 本文只做 |
|------|--------------|---------|
| 版本 | `gcm/__init__.py` | 引用，不写死 |
| 分层与模块职责 | `docs/architecture.md` | 引用 |
| 视觉系统四层 | `gcm/ui/{palettes,typography,qss_build,status_pill}.py` 的模块 docstring | **扩展**，不回退 |
| 可复用范式（单写者/原子写/契约/纯校验） | `docs/code-gems.md` | 引用 |
| 交付门禁 | `policy.md` | 引用 |
| 接缝注册表 | `test_ledger.md` | 落地后补登记 |
| v10.0.0 计划 | `docs/plans/.rev10-part1..5.md` | 划边界（§1.5），不覆盖 |
| G75 UI 计划（已归档） | `docs/archive/plans/20261023/UI视觉系统重构指南.md` | 承接其 3 项未闭环（§4 W5） |

## 附录 B · 本轮实测命令清单（可复现）

```
python scripts/dev_metrics.py                     # 版本 / 规模 / 超限模块 / 长函数
git log --oneline -1 ; git status --porcelain     # 基线 f6b913e，仅 docs/plans/ 未跟踪
Get-ChildItem reports/ui-audit                    # 34 张截图，2026-11-03
```
