# UI 跨设备适配与性能重塑指南 — Rev13（响应式 · 性能 · 设计系统 · UX/无障碍）

> **交付模式（两阶段）**：
> ①**第一阶段只写文档** —— 基线 `main` @ `f6b913e`、`gcm.__version__ = 9.18.0`（写作基线，
> 当前值以 `gcm/__init__.py` 为准，实施期间并行会话已 bump）、编写日期 **2026-11-05**；
> ②**第二阶段（同一会话，用户 D-12 拍板「立刻做 P0 止血」）落 P0 代码** ——
> R13-01/02/03/04 已实施，证据见 `evidence/rev13-p0.json` 与
> `docs/验证记录-2026-11-05-Rev13.md`。**P1/P2/P3 一行代码都没动。**
> **注意**：工作区存在**另一条并行会话**未完成的 Rev12 可用性改动（未跟踪 `gcm/app/undo.py`、
> `gcm/ui/menu_bar.py`、`gcm/ui/undo_bar.py`、`gcm/util/oplog.py`、`tests/test_rev12_usability.py`
> + 若干未暂存修改）。本文的实测数字**含这批改动**，会在 §1.4 单独标注归因。
>
> **本文件不是事实源**。事实源仍是 `gcm/__init__.py`（版本）、代码本身（行为）、
> `docs/architecture.md`（分层）、`policy.md`（交付门禁）、`test_ledger.md`（接缝）、
> `evidence/*.json`、`docs/验证记录-*.md`。本文只放「**还没落地的适配/性能/可访问性改进项**」
> 与「凭什么算完成」。

---

## 0. 这份文档怎么用

### 0.1 读者

| 读者 | 读哪几节 | 用法 |
|------|---------|------|
| **你（产品负责人）** | §0.2 → §1.3 → 各节「P0」标记 → §7 | 只需看：现有哪里是坏的、我要做什么、按什么顺序、风险挡没挡住 |
| **执行 AI / 执行人** | §1 → §2/§3/§4/§5 对应批次 → §6 → §8 | 一次只做一个 Wave；开工先 `git status --porcelain` 重冻结基线 |
| **下一轮审计** | §1.2 证据表 → 各任务「状态」行 | **不许拿本文当已完成证据**，逐条重跑 |

### 0.2 用户本轮明确要求（授权记录，2026-11-05）

按给出的 8 段结构产出「可执行开发计划」，并落到 `计划书/` 下的**新文件**；
若同名旧文件已存在则须先判定是否过期。

**第二轮拍板（2026-11-05，三项，已据此改代码）**：

| # | 决策点 | 你的选择 | 落地方式 |
|---|--------|---------|---------|
| **D-12** | 执行时机 | **立刻做 P0 止血（R13-01~03）** | 已实施并跑通定向回归（见 §9 实施记录） |
| **D-13** | 字号下限 | **强制 0.9** | `zoom_out()` 改用 `typography.FONT_SCALE_MIN_UI`，不再有第二套口径 |
| **D-14** | 矮窗降级 | **第二行折叠进「更多 ▸」菜单** | `download_layout.set_tools_row_folded()` + `main_window._apply_fold_and_min_heights()` |

> D-14 的实际实现比原计划（R13-02 只写 `min_height_for()`）**多做了一层**：
> 实测发现只调最小高度在 1366×768@150%（工作区 911×512）下会被
> `effective_minimum()` 夹回 480/504，工具行照样被裁 —— 所以折叠是必需的，
> 抬高最小高度只是配套。结论已回填到 R13-02 与 §2.3。

### 0.3 证据标签（与 `policy.md` §3 一致）

- `已验证`：本轮实际运行并观察到结果（附命令与输出摘要）
- `静态确认`：读代码确认，未运行
- `合理推断`：有间接证据
- `待验证`：缺环境/权限/时间

### 0.4 状态回填规则

任务 ID 形如 `R13-07`。执行完把对应行 `状态:` 改成：
`状态: DONE 2026-11-XX · evidence/rev13-<wave>.json · docs/验证记录-<日期>-Rev13.md`

只允许 `TODO` / `DOING` / `DONE` / `CUT（附理由+拍板人）` / `BLOCKED（附最小缺失条件）`。
**没有 evidence 文件一律不许标 DONE。** 禁止「基本完成」。

---

## 1. 事实基线（2026-11-05 现测）

### 1.1 这是什么产品，决定了哪些「Web 指标」不适用

Git-clone-Max 是 **PyQt6 桌面单机工具**，不是 Web 应用（`gcm/http_api.py` 只输出 JSON / SSE / CSV，
`grep -c "text/html"` = **0**，无 `gcm/web/` 静态资源，`已验证`）。

因此你模板里的每一项都要做**同构替换**，不能照搬：

| 你的模板项 | 本项目对应物 | 说明 |
|-----------|-------------|------|
| 断点 320/768/1024/1440px | **逻辑像素档位** 900 / 1020 / 1100 / 1440 | Qt 已做 DPI 缩放，断点按逻辑像素定义（`ui/layout.py` 文档已如此声明） |
| mobile-first / desktop-first | **desktop-first + 密度自适应** | 目标用户是挂机刷几百仓库的个人开发者，手机端不存在（无 Web 前端）；"移动"对应的是**小屏笔记本 / 高 DPI / 分屏窄窗** |
| Core Web Vitals（LCP/INP/CLS） | **冷启动首帧 / UI 线程帧预算 / 布局抖动** | 无页面加载，等价指标见 §3.1 |
| srcset / art direction | **多 DPI 图片与图标** | 本项目**零位图资源**（`find` 无 `.ico/.svg`，图标全走 `QStyle.standardIcon`，`已验证`），所以"图片优化"塌缩为"图标策略" |
| dvh/svh/lvh、刘海安全区 | **多显示器工作区 / 任务栏 / 缩放 / 分屏** | 桌面无刘海；真正会咬人的是「恢复的窗口落在已拔掉的显示器上」 |
| Service Worker / 离线 | **失败分类 + 重试队列 + 无网降级文案** | 已有 `app/errors.classify_error` + `app/retry_queue.py`（持久化 + 退避 + jitter） |
| CSS containment / GPU 合成 | **QSS 全量重应用成本 / 自绘 delegate 重绘范围** | Qt 无 containment；对应手段是**避免整窗 setStyleSheet** 与**限制 update(rect)** |
| 字号缩放 / 无障碍 | **font_scale 0.8–1.6 + 四主题对比度 + 键盘可达** | 这是本项目真实且可测的等价物 |

### 1.2 本轮真实跑过的命令与结果

| 命令 | 结果 | 标签 |
|------|------|------|
| `python scripts/dev_metrics.py` | 135 文件 / 33,657 行（写作快照；收尾复测 33,769 行，并行会话仍在增长）；超 800 行 3 个（与登记一致）；>100 行函数 19；`except/pass-only` 751/285；tests 191 文件 | 已验证 |
| `python -m ruff check gcm` | `All checks passed!` | 已验证 |
| `python -m mypy gcm` | `Success: no issues found in 134 source files` | 已验证 |
| `python scripts/doc_drift_check.py` | 写作时 **DRIFT 5**，收尾复测 **DRIFT 3**（并行会话仍在改代码，见 §1.4） | 已验证 |
| `python scripts/i18n_coverage.py` | `I18N OK`（但它只校验 `tr()` 的键，见 §5.4 / R12） | 已验证 |
| `QT_QPA_PLATFORM=offscreen unittest tests.test_ui_compact_visibility` | **FAILED (failures=1)**，连续 3 次稳定复现 | 已验证 |
| 同上，在 `HEAD` 干净副本 / 仅暂存区内容副本 | **OK** | 已验证 |
| `python scripts/bench_g70_ui.py` | 500 行 `add 65.41ms` / `update 3.08ms` / `progress_widgets 0`；QSS 7,588 字符，缓存后 0.032ms；空库启动 249ms | 已验证 |
| 离屏计时（导入 Qt / 导入 UI / 构造 / 首帧） | 776 / 617 / 1614 / 80 ms（冷）；重复构造 321→176→152 ms | 已验证 |
| 窗口改尺寸后 `_on_layout_tick()` 直接调用 | 11.5 / 12.3 / 13.9 / 15.2 / **18.1 ms**（1300→1440 宽） | 已验证 |
| 单次全窗 `setStyleSheet(_themed_qss())` | **121 ms**；四主题轮换 **532 ms** | 已验证 |
| `evidence/health_g84.json` | `started_ok=true`，**`elapsed_ms = 4034`**（打包 exe 真实启动） | 已验证（历史证据） |
| 对比度矩阵（四主题） | body 15.15/15.54/9.25/21.00；dim 6.67/6.04/5.96/15.91；`accent/panel` 8.72/4.79/5.03/12.80；`on_cta/cta` 全 ≥10.84 | 已验证（AA 全过） |

### 1.3 现测缺口（R1–R16）—— 每条都能被截图/命令/代码复现

| # | 问题 | 证据 | 严重度 |
|---|------|------|--------|
| **R1** | **交付回归：主操作区在 1020×560 被裁 15px**（`btn_open/btn_failures/btn_retry_queue/btn_diag_bundle` 可见高 27/42） | `test_ui_compact_visibility` 稳定红；隐藏菜单栏后立刻转绿 → **根因是并行会话新增的 18px 菜单栏** | **P0** |
| **R2** | 窗口最小高 560 与「工具行完整可见」矛盾：字号 1.0 需 **580**、1.3 需 **600**、1.6 需 **620**。**而且单纯抬高最小高不管用**：`effective_minimum` 会按屏幕工作区夹回 —— 1366×768@150% 工作区 911×512 → 最小窗仍是 **(900, 480)**，逐尺寸实测 900×480 / 911×512 下工具行依旧 27/42 | 逐档实测（20px 步长扫描）+ `effective_minimum(911,512)` 实算 | **P0** |
| **R3** | 断点档位**没有中段**：`WINDOW_MIN=1020` 与 `COMPACT_WIDTH=1100` 之间只有 80px 余量，900–1019（`ABSOLUTE_MIN` 允许）落在 `unsupported` 却仍可被 `effective_minimum` 返回 | `layout.py:16-35` + `effective_minimum` 实算：1024×600 屏（工作区 1024×560）→ 最小窗 (921,480)，`layout_mode` = **`unsupported`** | P1 |
| **R4** | **`wide` 档是死枚举**：`layout_mode` 会返回 `"wide"`，全库唯一消费点是 `layout_mode(...) in ("compact","unsupported")` —— 宽屏与大屏**行为完全一致** | `grep '"wide"' gcm/` = 1，且仅 `layout.py:35` 的 `return "wide"` 本身；`_apply_layout_mode` 只判 `in ("compact","unsupported")` | P1 |
| **R5** | **窄屏折叠是死分支**：`_apply_layout_for_width` 用 `QTableView.horizontalHeaderItem()`（该 API 只存在于 `QTableWidget`）→ `AttributeError` 被 `except` 吞掉，**永远不折叠任何列**；且它找的表头 `"标签"` 列**不存在**（现 8 列，无标签列）；触发条件 `w<768` 在 `effective_minimum` 下限 900 下**不可达** | 运行时实测：`hasattr=False`、调用抛 `AttributeError`；`min(effective_minimum(...))` 恒 ≥900 | **P1（死代码 + 静默吞异常）** |
| **R6** | 管理页列宽仍是 5 个硬编码 `Fixed`（150/78/70/64/84），窄窗靠 stretch 挤压，**无持久化、无自适应下限** | `manage_panel.py:161`；1020×560 实测内容宽 653 vs 视口 638（差 15px，靠 stretch 吸收） | P1 |
| **R7** | 多显示器只问 `primaryScreen()`：窗口从副屏拖回/副屏拔掉后，最小尺寸与 DPI 规则**不重算**（`_layout_timer` 只处理宽高，不监听 `screenChanged`） | `grep screenChanged` = 0；`layout.py:76`、`main_window.py:698,712`、`theme.py:149` 全是 primaryScreen | P1 |
| **R8** | **设计 token 三成是死的**：`spacing.xl/2xl/inset/section`、`row.compact/manage`、`radius.chip` 全库**零消费**（`TOKENS[...]` 只剩 12 处访问，全在 `qss_build` 与 `progress_table`） | `grep "TOKENS\["` 全量比对 | P2 |
| **R9** | **排版角色只有 3 个调用点**：`apply_type()` 仅用于 `batch_conclusion.phase_lbl` / `onboarding.kicker` / `status_strip.brand_lbl`；管理页/进度表正文仍是 QSS 统一字号，`mono` 只落在进度 delegate 一处 | `grep "apply_type("` | P2 |
| **R10** | **动效时长没有 token**：220ms / 180ms / 600ms 分散硬编码；无统一 `MOTION` 表，`prefers_reduced_motion` 只在 4 处消费 | `batch_conclusion.py:195,207`、`row_anim.py:37` | P2 |
| **R11** | **无骨架/加载态**：全库无 skeleton/shimmer，无 `WaitCursor` 统一入口；耗时入口靠 `busy()` 置灰（仅 3 处）。慢机械盘/杀软下用户看到的是"空白表格" | `grep skeleton` = 0；`busy()` 3 处 | P2 |
| **R12** | **i18n 存在「假绿」**：`i18n_coverage.py` 只校验 `tr()` 用到的键，**未走 `tr` 的中文可见文案有 767 条**（AST 去 docstring/去 `tr` 参数后统计；`main_window` 164、`manage_panel` 73、`repo_detail_dialog` 68）。英文模式下这些直接显示中文 | AST 统计脚本 | P2 |
| **R13** | **无障碍标注稀疏**：`setAccessibleName/Description` 共 **21 处 / 14 个文件**，**12 个主操作里 10 个没有 `accessibleName`**（`btn_clear` / `btn_pause_batch` / `btn_open` / `btn_failures` / `btn_retry_queue` / `btn_diag_bundle` / `btn_save_list` / `btn_star_import` / `btn_load_list` / `btn_edit_quick`）；全库 `setTabOrder` **0 处**；`keyPressEvent` **0 处**（键盘仅靠 **10 个运行时 `QShortcut`**：`Ctrl+=/-/0` 缩放、`Ctrl+1..3` 页签、`Ctrl+Alt+S/U/M`、`Ctrl+K` 命令面板）；主窗子控件共 **56 个 QPushButton、36 个 QLineEdit、22 个 QCheckBox** | 运行时 `findChildren` + 逐项 `accessibleName()` 检查 | **P1（WCAG 2.1 AA 相关）** |
| **R14** | **表格项无 hover 反馈**：QSS 只给 `QListWidget::item:hover`，`QTableView/QTableWidget::item` 无 hover/selected 规则；行高 26px（`TOKENS.row.default`）低于触控板友好阈值 | `qss_build.py:113,252-254` | P2 |
| **R15** | **主题切换是整窗重刷**：拖一次 `setStyleSheet` 121ms，四主题轮换 532ms，切换瞬间有可感知停顿 | 实测；profile 显示 110/121ms 全在 `setStyleSheet` 内 | P2 |
| **R16** | **冷启动 3–4 秒**：离屏冷路径 776（Qt）+617（UI）+1614（构造）≈ 3.0s；打包 exe `health_check` 实测 **4034ms**（含解包，属历史证据）。用户感知为"点了没反应" | 离屏计时 + `evidence/health_g84.json` | P2 |

### 1.4 与并行会话的归因（不掩盖、不抢功、不误伤）

| 事实 | 处置 |
|------|------|
| `doc_drift_check` 红：写作时 `main_window.py` 声称 2980 / 实测 3110，测试文件 190 vs 191（DRIFT 5）；**收尾复测已变 3147 / DRIFT 3** —— 数字在本文写作期间持续移动 | **不是本文造成**。本文不追这些数字（追一个动一个）；由**最后落盘的一方**按真实值三处同源修正（`CLAUDE.md` §6 / `docs/architecture.md` §4 / `README.md` + `_EXPECTED_TEST_FILES`）。执行 `R13-04` 前必须重新冻结一次基线 |
| `R1` 的裁切回归 | **由并行新增菜单栏引入**（隐藏 `menuBar()` → 立即转绿，反证成立）。修法有两条：①菜单栏行内合并（推荐给并行方）；②`WINDOW_MIN` 高度按字号换算（本文 R2 方案）。**不要用"改宽断言"让它绿** |
| 未跟踪 `gcm/app/undo.py`、`ui/menu_bar.py`、`ui/undo_bar.py`、`util/oplog.py` | **不删、不改、不暂存**。本文所有任务都要等这批收敛后再动同一文件 |

### 1.5 对既有计划文档的时效性判定（用户本轮明确要求）

| 文件 | 是什么 | 判定 | 本文处置 |
|------|--------|------|---------|
| `计划书/UI-视觉重设计-Rev11.md`（668 行） | **已批准的设计宪法**（三原则 / 色彩预算 / 排版声部 / 守卫账）+ W0–W5 任务书，多数 `DONE` | **不过时**。§2 设计原则继续有效；`V-12`（历史按钮自绘）、`V-15`（动作三区）、`V-17`（列宽持久化）、`V-18` 残留、`V-22` 四处接入仍 `TODO`/部分完成 | 只读引用，**不重复实现**；与本文 R6/R9 有交集时**以本文的量化口径为准，实现落点仍按 Rev11** |
| `计划书/UI-视觉重设计-Rev11-收尾与追加打磨.md`（1,051 行，2026-11-05 05:56） | Rev11-C：W6（R-01…R-07）+ W7（P-01…P-04）收尾计划，**计划态、未执行** | **新鲜，但主题是「收尾打磨」**；其 §1.4 已声明与并行会话的文件级避让 | **并列存在，不覆盖**。本文不重写 R-01…P-04 任何一条，专注**跨设备适配 / 性能预算 / 无障碍 / token 债**四类 —— 它们未被那两份文档系统覆盖 |
| `docs/archive/plans/20261023/UI视觉系统重构指南.md` | G75 已归档计划 | 只读追溯，不复活 | 引用其 `T-UI-08/09` 的未闭环结论即可 |
| `docs/plans/.rev10-part1..5.md` | v10.0.0 草稿（未跟踪） | 主题是稳定性/反馈闭环，不含视觉与适配 | 不并入、不删除 |

**冲突消解顺序**：代码事实 > 本文（本轮实测） > Rev11-C（最新计划） > Rev11（设计原则） > 历史归档。

### 1.6 本轮**没有**做的验证（不得声称通过）

| 项 | 原因 | 补偿 |
|----|------|------|
| 真实多显示器（双屏/拔插/混合 DPI） | 本机单屏 | `R13-06` 用可注入屏幕参数的纯函数 + 离屏模拟覆盖 |
| Windows 高对比度模式 / 系统「减少动画」真实开关 | 需交互式系统设置 | 用现有 `prefers_reduced_motion` + 注册表探测的**纯函数**覆盖 |
| PyInstaller 打包 + 真机点击 | 属发版动作 | 列入 §8 收尾门禁 |
| CI 三平台 | 本机只有 Windows | 快集 + coverage 已在本机跑过（见 §0.2 第二阶段）；三平台由 CI 承接 |
| 快集全量 / coverage | **写作阶段**（第一阶段）未跑 | **P0 阶段已跑**：快集 2067 例（第 2 次 failures=0）、coverage 81%（见 `evidence/rev13-p0.json`） |

---

## 2. 响应式策略（Responsive Strategy）

### 2.1 结论先行

**采用 desktop-first + 密度自适应（density adaptive）**，断点从"两个半档"改成**四个可测档 + 一个受支持下限**。
理由（不是偏好）：用户 100% 在桌面（无 Web 前端，`已验证`）；真实痛点不是"手机排版"，而是
**1366×768@125%/150% 笔记本、1024×600 上网本、分屏窄窗、175%/200% 缩放**四类场景。

### 2.2 断点表（目标态，逻辑像素）

| 档位 | 范围（宽 × 高） | 语义 | 行为 |
|------|----------------|------|------|
| `XS`（受支持下限） | 900 ≤ w < 1020 **或** 480 ≤ h < 580 | 极窄/极矮 | 工具行折叠为「更多 ▸」菜单；进度表隐藏「本次」列；管理页折叠到 4 列 |
| `S`（compact） | 1020 ≤ w < 1100 **或** 560 ≤ h < 700 | 小笔记本 / 分屏 | 快捷填充与设置导航隐藏（现有行为）；结论条单行；行高 24 |
| `M`（standard） | 1100 ≤ w < 1440 **且** 700 ≤ h < 1000 | 主流办公 | 全量控件，行高 26 |
| `L`（wide） | w ≥ 1440 **且** h ≥ 900 | 大屏/竖屏副显 | 新增：管理页 8 列全展开；进度表右留白改由「详情」吃掉，**不再无差别 stretch** |

**与模板的映射**：900 ≈ mobile 下限（更小窗口**明确拒绝**而非硬挤）、1020/1100 ≈ tablet、1440 ≈ desktop、≥1920 ≈ large。

### 2.3 必须改的三处硬编码

1. **`ui/layout.py:16-21`**：`WINDOW_MIN` 的高度改为函数 `min_height_for(font_scale) -> int`
   （实测 1.0→580、1.3→600、1.6→620，取整到 20 的倍数再 +40 余量 → **620 / 640 / 660**）。
   `effective_minimum()` 增加 `font_scale` 与 `menu_bar` 两个可选参数（默认 1.0 / True，保持向后兼容）。
2. **`ui/layout.py:19`**：`WIDE_WIDTH/WIDE_HEIGHT` 接上真实消费点（`_apply_layout_mode` 增加 `wide` 分支）。
   **注意**：抬高 `WINDOW_MIN` 不能单独解决问题 —— 它会被 `effective_minimum()` 按工作区夹回（实测 1366×768@150% → 仍 (900,480)），
   所以「高度不够时把工具行折叠进「更多 ▸」」这条**必须做**，不能只调常量。
3. **`main_window.py:2844-2854`**：删掉 `horizontalHeaderItem` 死分支，改为按模型列名隐列 ——
   `model().headerData(i, Horizontal)` 取到 `"大小"/"健康"` 再隐，且 `narrow = w < 1020`（可达）。

### 2.4 触控 / 指针 / 键盘

| 项 | 现状 | 目标 | 做法 |
|----|------|------|------|
| 触控目标 | 行高 26px、按钮 `sizeHint` 42px、`btn_start` minH 38 | 可点行高 ≥32px；主操作 ≥40px | 新增 `TOKENS.row.touch = 34`，平板模式（`screen().isTouchscreen()`）或 `--touch` 时启用 |
| hover 状态 | 表格项**无** hover | 行 hover 底色 = `panel2`（仅作提示，不作唯一编码） | `qss_build.py` 增 `QTableView::item:hover`；`::item:selected` 用 `accent` 10% alpha，不动文字色 |
| 键盘导航 | 10 个运行时 `QShortcut`，0 个 `setTabOrder` | 每页签 Tab 顺序 = 视觉顺序；`F6` 页签循环；`Esc` 关对话框 | 新增 `ui/focus_order.py`（≤150 行）集中声明 tab 链 |
| 焦点可见 | QSS 有 **4 个 `:focus` 规则块**（覆盖 9 类控件：行编辑/数字框/下拉/多行文本/按钮/表格/页签） | 保持 + 补 `QCheckBox/QRadioButton:focus` | `qss_build.py` + 守卫断言"每类可聚焦控件 ≥1 条 focus 规则" |

### 2.5 多显示器 / 工作区 / 安全区等效物

- **屏幕变更监听**：`QGuiApplication.primaryScreenChanged` + `screenAdded/screenRemoved` → 重算
  `effective_minimum` 并 `clamp_window_size`。落点：`ui/layout.py` 纯函数 + `main_window` 只做接线。
- **记住"上次所在屏"**：`_serialize_geometry` 已存 `M:` 与四元组；补存**屏幕名**（`screen.name()`），
  拔屏后按名字找不到则回主屏并记一条 WARN 日志（不静默）。
- **安全区**：桌面无刘海；等价约束是**任务栏**（已用 `availableGeometry`，正确）与
  **Windows 贴靠（Snap）** —— 贴靠有 ±4px 偏差，`clamp_window_size` 的 0.95/0.85 系数已覆盖。

### 2.6 字号与缩放

- 用户可调 `font_scale ∈ [0.8, 1.6]`，但 `FONT_SCALE_MIN_UI = 0.9` 这个「UI 层下限」**全库零强制**
  （只在 `tests/test_g46_theme.py` 断言常量值）。`Ctrl+-` 可直达 0.8。
- **建议**：`zoom_out()` 下限改为 `FONT_SCALE_MIN_UI`；若要保留 0.8，则**同时**把 §2.3 的
  `min_height_for()` 下限再抬 20px，并在设置页提示"更小字号可能裁切"。二选一，不要两头都松。

---

## 3. 性能蓝图（Performance Blueprint）

### 3.1 指标替换表（Web → 桌面，可测）

| Web 指标 | 本项目等价指标 | 当前实测 | 目标 | 度量方式 |
|---------|--------------|---------|------|---------|
| LCP < 2.5s | **冷启动到首帧可交互** | 离屏 ≈3.0s；exe 4034ms | 离屏 ≤2.0s；exe ≤3.5s | `scripts/bench_startup.py`（新增）+ `health_check.py` |
| INP < 100ms | **UI 线程单次操作 ≤100ms** | 改尺寸重排 11–18ms（**1440 宽已超 16.7ms 帧预算**）；主题切换 121ms | 重排 ≤16ms；主题切换 ≤80ms | 离屏计时 + `QElapsedTimer` |
| CLS < 0.1 | **布局抖动**：首帧后可见元素位移次数 | 未测（`待验证`） | 首帧后 500ms 内位移 = 0 | 测试期记录 `moveEvent` 次数 |
| 60fps 动画 | **动效帧预算 16.7ms** | 220/180/600ms 三档；`QGraphicsOpacityEffect` 单次 0.004ms（Rev11 实测） | 实际采样 ≥30fps | 动画期 `paintEvent` 计数 / 帧间隔中位数 |
| 首屏资源 | **构造期控件数 / QSS 长度** | 56 按钮 + 36 输入 + 22 复选；QSS 7,588 字符 | QSS ≤9,000 字符；构造 ≤120ms | `dev_metrics.py` 扩展 |

> `4034ms` 是**打包 exe 解包 + 启动**的历史证据（`evidence/health_g84.json`），与离屏 Python 路径不是同一口径，**不要混算**。

### 3.2 现有瓶颈（已定位，按收益排序）

| # | 瓶颈 | 证据 | 建议 |
|---|------|------|------|
| B1 | 主题切换整窗 `setStyleSheet` | 121ms/次，110ms 全在 Qt 内部 | 改为按控件子树应用；或最小改动：切换时先隐藏窗口 → 应用 → 显示（视觉"瞬切"） |
| B2 | 构造期 1614ms（冷） | profile 前五：`_build_ui` 82ms、`repo_db.__init__` 39ms、`setStyleSheet` 25ms×3、`_build_download_tab` 24ms、`_build_settings_tab` 23ms | 3 次 `setStyleSheet` 合成 1 次；`_migrate` 18ms 延后到首个 `list_repos` |
| B3 | 改尺寸重排 11–18ms | `_on_layout_tick` 内跑 `_apply_layout_mode` + 列遍历 | 列遍历改为**只在断点跨越时**执行（缓存上次档位） |
| B4 | 500 行 `add` 65ms | `bench_g70_ui.py` | 已达标（<100ms）；保持 `progress_widgets=0`（不得回退成逐行 QProgressBar） |
| B5 | 并发进度更新吞吐 | 未测（`待验证`） | 用引擎真实信号做 200 行×10 轮压测，目标 ≤4ms/轮 |

### 3.3 技术手段（Qt 语境下的 lazy loading / code splitting / 资源优化）

- **懒加载**：「设置」页现在构造期就建完；改为首次切换到该页时构建（`currentChanged` 一次性触发），
  预计省 23ms 构造 + 大量控件。守卫：切页后断言控件存在（防"懒加载变没加载"）。
- **代码切分**：`gcm/ui` 57 个模块已足够细，不为拆而拆。真正该注意的是
  `settings_panel`(795) / `progress_table`(795) / `settings_groups`(785) —— 都贴 800 红线，
  任何新增功能必须落新模块。
- **资源优化**：零位图，图标全走 `QStyle.standardIcon`（映射表 16 项 / 调用 12 处）。**不引入打包图标集**（体积 + 主题不一致）。
  若未来要品牌图标，用**单色 SVG 路径自绘**，随 `current_palette()` 着色。
- **离屏/降级**：`QT_QPA_PLATFORM=offscreen` 时已自动关动效（保留）。

### 3.4 新增度量工具（各 ≤150 行，纯脚本）

| 脚本 | 用途 | 输出 |
|------|------|------|
| `scripts/bench_startup.py` | 冷/热启动分段（import-qt / import-ui / ctor / first-frame） | JSON，可直接进 `evidence/` |
| `scripts/bench_resize.py` | 9 尺寸 × 3 字号的重排耗时 | JSON + 超预算标红 |
| `scripts/a11y_audit.py` | 可聚焦控件是否缺 `accessibleName`、是否缺 focus QSS | 文本清单，CI 可断言"不得变多" |

### 3.5 离线 / 降级（不是 Service Worker）

- 已有：`errors.classify_error`（唯一重试判据）+ `retry_queue`（持久化/退避/jitter）+ 失败面板。
- **缺**：网络彻底不可用的**全局状态**。建议 `status_strip` 增一个 `offline` 指示点（形状编码，不用字符），
  由最近 3 次失败全为 network 类触发，恢复即消；不弹窗、不阻塞。

---

## 4. 设计系统规范（Design System Specification）

### 4.1 Token 架构（已有，需"接线"而非重建）

```
palettes.py   → 色板（4 主题 × 18 键）+ TOKENS（radius/border/spacing/row）+ ACCENT_PRESETS（6 档）
typography.py → 3 字族 + 7 级字号（display/h1/metric/h2/body/mono/small）+ 字距
qss_build.py  → 唯一 QSS 产地（按 palette × scale × motion 缓存）
```

**必做（R8 / R10）**：

1. **删或接死 token**：`spacing.xl(24)/2xl(32)/inset(20)/section(40)`、`row.compact(24)/manage(34)`、
   `radius.chip(10)` 目前零消费。二选一：①接进 `download_layout` 的区块间距（推荐 `section` 用于
   「输入区 / 选项区 / 动作区」三段之间）；②删除并在 `palettes.py` 注明"刻意不提供"。
   **不要留着装点门面** —— 那正是 Rev11 §1.3 诊断的"token 没兑现"。
2. **新增 `MOTION` token**（新 `ui/motion.py`）：`fast=120ms / base=220ms / slow=600ms / easing=OutCubic`，
   替换 3 处硬编码；守卫断言"UI 里不再出现裸 `setDuration(数字)`"。

### 4.2 色彩策略（已验证达标，勿动色值）

| 检查 | 实测最小值 | 门槛 |
|------|-----------|------|
| 正文/背景 | 9.25（nord） | ≥4.5 |
| 弱文本/背景 | 5.96（nord） | ≥4.5 |
| 输入边框/输入底 | 3.07（light） | ≥3.0 |
| accent/panel | 4.79（light） | ≥3.0（非文本） |
| on_cta/cta | 10.84（nord） | ≥4.5 |

**深/浅模式**：四主题 + `theme=auto` 走注册表读系统深浅。`QStyleHints.colorScheme()` 在本机 Qt 6.11
可用（实测返回 `Unknown`），可作**第二判据**；`reduceMotion` 在 6.11 **不存在**（实测 `hasattr=False`），
所以"减少动效"只能靠用户设置项 + 平台注册表，**不要**照搬 Web 的 `prefers-reduced-motion`。

### 4.3 间距 / 圆角 / 阴影（写死口径）

- 间距：**4pt 网格**，`xs4 / sm8 / md12 / lg16 / xl24 / 2xl32`；卡内边距 20；三段之间 **40**（`section`）。
- 圆角：卡 6 / 控件 5 / 进度条 5 / 页签 8 / 复选 4 / chip 10。
- 阴影：Qt QSS **无 box-shadow**。层次只靠 `1px glow 内嵌高光 + 边框对比 + 三档卡面权重`
  （`panel_primary` / `panel` / `panelGhost`）。**不要**用 `QGraphicsDropShadowEffect` 做卡片阴影 ——
  数百个滚动项上会明显掉帧。

### 4.4 图标与插画

- 现状：`QStyle.StandardPixmap` 映射表 16 个语义图标（实际调用点 12 处）；无打包资源。
- 规则：**图标只表达动作，不表达状态**（状态走 `status_pill.draw_shape` 的 7 种几何形状 ——
  微软雅黑缺字形，字符符号会变 tofu，这是硬红线）。
- 新增图标：优先 `QStyle`；确无对应时用**单色 QPainter 路径**，颜色取 `current_palette()["text_dim"]`。

### 4.5 组件一致性（legacy 修正清单）

| 组件 | 问题 | 修正 |
|------|------|------|
| `HistoryButtonDelegate`（`manage_model.py:299-339`） | 用 `style.drawControl(CE_PushButton)` 画**原生按钮**，深色主题下是浅色方块；文本 `"查看"` 未走 `tr()` | 改 QPainter 自绘（`panel2` 底 + 1px `border` + 圆角 5），文本 `tr("查看")` —— **Rev11 `V-12` 仍 TODO，本文只做量化验收，不另立实现** |
| `batch_overview` + `batch_conclusion` | 两个批次条并存，语义相邻 | 保留（`test_g46_nav_batch` 依赖 `batch_total_lbl`）；`S` 档隐藏 `batch_overview`、只留结论条，避免小屏双条争高 |
| `manage_panel` 空态 | `QLabel("尚未导入仓库…")` 直书，未走 `empty_state` 工厂 | 改用 `build_empty_state()`（Rev11 `V-22` 四处接入之一） |
| 管理页数据列 | 无 mono，数字不对齐 | 与 Rev11-C `P-02` 合并执行，**不要两处各写一遍** |

---

## 5. UX/UI 最佳实践（UX/UI Pattern Library Plan）

### 5.1 层级与可扫视性

- 已有：批次结论条（display 28px，全屏唯一）、三档卡面权重、左缘刻度尺。
- **缺**：页面级标题层级。`download_layout` 的分组标题（"仓库地址（每行一个）"）与正文同字号。
  改：分组标题用 `h2（15px/600）`，经 `apply_type(lbl, "h2")`；每屏标题用 `h1（20px）`。
- **验收**：截图目检 + 守卫断言"下载页至少出现 2 个非 body 排版角色"。

### 5.2 反馈与可预期性（R11）

| 场景 | 现状 | 目标 |
|------|------|------|
| 打开大目录 / 扫描 | 无进度、无光标变化 | `busy()` 统一置忙 + 表格骨架行（3 行灰色占位，`QPainter` 画，不建控件） |
| 统计中心首开 | 同步计算，界面冻结 | 后台线程 + 骨架柱 |
| 导出报表/诊断包 | 已有 `busy()` | 保持 |
| 微交互 | 按钮 hover/pressed 有；表格无 | 补行 hover；**不加**"点击涟漪"（Qt 无原生，自绘动画收益低） |

> **反模式**：不要用 `QProgressDialog` 做不确定进度 —— Windows 上会抢焦点并阻塞父窗。

### 5.3 导航

- 现状：3 页签 + 设置页左侧导航 + 命令面板（`Ctrl+K`）+（并行会话新增）菜单栏。
- **补**：`F6` 页签循环；设置页导航上下键（`QListWidget` 原生支持，需确认焦点策略）；
  管理页双击进详情已有，补 `Enter` 等价（现由 eventFilter 实现，需断言不被懒加载破坏）。

### 5.4 无障碍（WCAG 2.1 AA 最低）

| 要求 | 现状 | 动作 | 验收 |
|------|------|------|------|
| 1.4.3 对比度 ≥4.5 | 已达标（最小 5.96） | 不动 | 现有守卫 |
| 2.1.1 键盘可达 | 10 个运行时快捷键，0 tab 链 | `focus_order.py` + `F6` | 遍历可聚焦控件，断言 Tab 能到达每个主操作 |
| 4.1.2 名称/角色 | 21 处标注；**12 个主操作里 10 个缺名** | 先补齐这 10 个（屏幕阅读器读不出按钮用途 = 功能性缺陷），再覆盖设置页 22 个复选 | `a11y_audit.py` 计数**只降不升**，且断言「主操作清单内 100% 有 accessibleName」 |
| 2.4.7 焦点可见 | 4 个 focus 规则块 / 9 类控件 | 补复选框与单选钮（下拉已有） | QSS 扫描守卫：每类可聚焦控件 ≥1 条规则 |
| 1.4.11 非文本对比 ≥3 | 边框 3.07 达标 | 不动 | 现有守卫 |
| 3.3.1 错误可识别 | `classify_error` 三段式 | 保持 | 现有守卫 |

> **诚实边界**：本项目**不做**屏幕阅读器全流程测试（需 NVDA/JAWS + 真人，`待验证`），
> 只能保证"名称/角色/顺序/焦点/对比度"五项静态可验证。

### 5.5 表单与输入

- 仓库地址输入是 `QPlainTextEdit`（多行），**无内联校验**，错误在开始后才出现。
- 改：失焦时**行级**校验（`url_lib.parse_urls`，纯函数、不联网），无效行加浅红左边框，
  并在状态行给**非阻塞**提示"N 行无法识别"，**不弹窗**（弹窗会打断粘贴流程）。
- 输入类型：桌面无 `input type`；等价物是 `QSpinBox` 范围（并发 1–32、超时、限速）已有。

### 5.6 动效（有预算，不堆）

| 位置 | 时长 | 目的 | reduced-motion |
|------|------|------|----------------|
| 批次开始（结论条填充） | 220ms OutCubic | 告知"已开始" | 跳过，直接 100% |
| 批次结束（读数脉冲） | 180ms | 告知"结束了" | 跳过 |
| 行完成消隐 | 600ms | 定位"刚完成的那行" | 跳过（用户可见设置项 `ck_animations`） |
| 主题切换 | **0**（不做淡入淡出） | —— | 整窗重刷已 121ms，淡入只会更慢 |

### 5.7 空态与边界

| 场景 | 目标 |
|------|------|
| 零仓库（下载页/管理页） | 统一 `build_empty_state(title, hint, filtered)`；管理页当前是裸 QLabel |
| 筛选后为空 | `filtered=True` 变体（工厂已支持），文案区分"没有数据"与"筛掉了" |
| 权限错误（目标目录只读） | 在**输入行**就近提示，而非弹 `QMessageBox`（现 `main_window` **45 处**） |
| 磁盘满 | `classify_error` 已判定不可重试；UI 需给"清理/换目录"动作链接 |

---

## 6. 技术架构（Technical Architecture）

### 6.1 技术栈结论：**不换**

| 候选 | 判定 | 理由 |
|------|------|------|
| QML / Qt Quick | 不采用 | 全量重写 UI，回归面 = 全部 191 个测试文件；打包体积与启动时间都会变差 |
| Web 前端（Electron/Tauri） | 不采用 | 进程模型、打包、Windows 权限全部推翻；本项目零 Web 资产 |
| 保持 PyQt6 6.11 + QSS + 自绘 delegate | **采用** | 已具备 token 层、四主题、自绘组件；缺的是**接线与预算**，不是框架能力 |

### 6.2 目录与组件架构增量

```
gcm/ui/
  layout.py            # 断点纯函数（改：min_height_for / 屏幕参数注入）
  focus_order.py       # 新增：tab 链与 F6（≤150 行）
  skeleton.py          # 新增：表格骨架（QPainter，不建控件，≤120 行）
  offline_indicator.py # 新增：离线指示（挂 status_strip，≤80 行）
  motion.py            # 新增：MOTION token + 统一 easing（≤60 行）
scripts/
  bench_startup.py / bench_resize.py / a11y_audit.py   # 新增度量
```

**硬约束（沿用 `CLAUDE.md` §6）**：`settings_panel`(795) / `progress_table`(795) / `settings_groups`(785)
**一个字节都不许再加**；任何新逻辑落新模块。`main_window.py` 写作时 3110 行、收尾复测 **3147 行**（并行会话仍在增长），继续只做"转发"。

### 6.3 CSS 策略

- 唯一 QSS 产地：`qss_build.py`。**禁止**在控件上写内联 `setStyleSheet("...")`（会击穿主题）。
- 选择器只用 `objectName`（`#primary` / `#panelPrimary` / `#panelGhost`）与类型选择器；
  **禁止**复杂后代选择器（Qt 只支持子集，在复合控件上表现不一致）。
- 改 QSS 后必跑 `qss_validate`（**24 组 = 4 主题 × 3 缩放档(0.8/1.0/1.5) × 2 动效态**，
  现由 `tests/test_g75_visual_system.test_every_theme_and_scale_passes_validator` 断言）。

### 6.4 响应式测试策略（当前最大的测试缺口）

| 层 | 现状 | 目标 |
|----|------|------|
| 纯函数 | `layout_mode/effective_minimum/clamp` 7 例 | 扩到**参数化矩阵**：11 种屏幕 × 5 种字号 × 2 种菜单栏，断言"最小尺寸 ≤ 工作区" |
| 离屏布局 | `test_ui_compact_visibility` 9 尺寸（**现红**） | 扩到 **12 尺寸 × 3 字号**；断言 `visibleRegion == rect` 且**表格无横向滚动** |
| DPI | 只有 `clamp_scale_for_dpi` 4 例 | 用 `QT_SCALE_FACTOR=1.25/1.5/1.75/2.0` 子进程跑同一套断言（offscreen 屏是 800×800，需**注入**屏幕尺寸而非真实查询） |
| 像素回归 | 截图脚本**只存图不断言**（`ui_screenshot_audit.py`） | 阈值比对：与基线图逐像素差 ≤0.5% 通过；超阈值产出 diff 图 |
| 真机 | `docs/RELEASE_CHECKLIST.md` 人工 | 保持（杀软/代理导致的假绿无法自动化） |

### 6.5 守卫与文档同步（不破现有 CI）

- 新增守卫必须进**快集**（`scripts/ci_run_fast.py` 用黑名单，默认纳入）。
- `doc_drift_check` 锚的是**机制**：本文不新增"某行多少行"的声明，避免自己变成漂移源。
- 任何新增模块必须有生产调用点（`TestG69_18WiringClosure` 会抓孤儿）。

---

## 7. 分期落地计划（Phased Rollout）

> 执行纪律：一次一个 Wave；每 Wave 收口必须有 `evidence/` + 定向测试；跨 Wave 不并行改同一文件。

### P0 · 止血（必须先做，1 个批次）

| ID | 任务 | 落点 | 验收 | 状态 |
|----|------|------|------|------|
| `R13-01` | 修主操作区裁切（R1）。**主因已由并行会话的 Rev12 U-10 修掉**（最小高度 +24 扣菜单栏），本轮补上「小屏夹回 480/504 时」的兜底（即 R13-02 的折叠） | `main_window._build_ui`（既有）+ 折叠链路 | `test_ui_compact_visibility` 9 尺寸全绿（断言一行未改） | **DONE 2026-11-05 · `evidence/rev13-p0.json`** |
| `R13-02` | 矮窗折叠：`layout.tools_fold_height(字号)` 判定 + `download_layout.set_tools_row_folded()` 折叠 + 折叠态同步压缩顶部滚动区/进度表最小高 | `ui/layout.py`、`ui/download_layout.py`、`main_window._apply_fold_and_min_heights()`（51 行，独立方法） | `tests/test_rev13_adapt.py` 7 例：阈值随字号单调、900×504 折叠且四入口在菜单里可用、高窗展开、折/展往返不累积误差、**字号变化会重新判定折叠** | **DONE 2026-11-05 · `evidence/rev13-p0.json`** |
| `R13-03` | 删死分支：`_apply_layout_for_width` 改用 `model().headerData`、阈值 `layout.NARROW_WIDTH=1020`、只折叠「大小/健康」 | `main_window._apply_layout_for_width()`（LBYL，不再吞 `AttributeError`） | `test_g57_responsive.TestNarrowFoldOnRealModel` 真实模型反向自检：窄窗真隐藏、宽窗真恢复、非次要列不动 | **DONE 2026-11-05 · `evidence/rev13-p0.json`** |
| `R13-04` | 修 `doc_drift`（写作时 5 处；**本轮改动落盘后按最终实测同步三处**：`main_window.py` 3244、测试文件 192） | `CLAUDE.md` §6、`docs/architecture.md` §4、`README.md`、`_EXPECTED_TEST_FILES` | `doc_drift_check` = DRIFT 0 | **DONE 2026-11-05 · `evidence/rev13-p0.json`** |

### P1 · 适配正确性（2 个批次）

| ID | 任务 | 落点 | 验收 | 状态 |
|----|------|------|------|------|
| `R13-05` | 断点重构：XS/S/M/L 四档 + `wide` 真实消费 | `layout.py` + `_apply_layout_mode` | 5 尺寸 × 3 字号矩阵；`wide` 有大屏专属断言 | TODO |
| `R13-06` | 多显示器：`screenChanged` 重算 + 记住屏幕名 | `layout.py`（纯函数）+ `main_window` 接线 | 注入假屏幕尺寸的纯函数测试；拔屏回主屏有 WARN 日志 | TODO |
| `R13-07` | 管理页列宽自适应 + 持久化（Rev11 `V-17` 落地） | `manage_panel.py` + **新增** `settings.ui_state` 键（该键当前不存在，须同时补写入侧与消费侧） | 重启后列宽保持；1020 宽无横向滚动 | TODO |
| `R13-08` | 表格行 hover + 触控行高 token | `qss_build.py` + `palettes.py` | 截图目检 + 灰度可辨；`TOKENS.row.touch` 有消费点 | TODO |
| `R13-09` | 键盘：tab 链 + F6 | `focus_order.py`（新） | 遍历断言每个主操作可 Tab 到达 | TODO |
| `R13-10` | 无障碍标注补齐（**先补 12 个主操作里缺的 10 个**，再补设置页 22 个复选） | 就地 | `a11y_audit.py` 基线锁定 +「主操作 100% 有名」断言 | TODO |

### P2 · 性能与反馈（2 个批次）

| ID | 任务 | 落点 | 验收 | 状态 |
|----|------|------|------|------|
| `R13-11` | 启动优化：合并 3 次 `setStyleSheet`；设置页懒构建 | `main_window` + `settings_panel` 入口 | `bench_startup` 冷 ≤2.0s（离屏）；懒构建不丢控件 | TODO |
| `R13-12` | 主题切换优化（隐藏→应用→显示，或子树应用） | `main_window._themed_qss` 路径 | 单次 ≤80ms；四主题轮换 ≤300ms | TODO |
| `R13-13` | 重排优化：列遍历只在断点跨越时执行 | `_apply_layout_mode` | 1440 宽重排 ≤16ms | TODO |
| `R13-14` | 骨架屏 + 忙态扩展（目录扫描/统计中心） | `skeleton.py`（新）+ `busy()` 调用点 | 慢路径有可见占位；骨架不建子控件（`findChildren` 不增） | TODO |
| `R13-15` | 离线指示点 | `status_strip` + `offline_indicator.py` | 3 次网络类失败 → 显示；恢复即消；不弹窗 | TODO |
| `R13-16` | `MOTION` token + 去裸毫秒 | `motion.py`（新） | 守卫断言 UI 内无 `setDuration(数字)` | TODO |

### P3 · 打磨（1 个批次）

| ID | 任务 | 落点 | 验收 | 状态 |
|----|------|------|------|------|
| `R13-17` | 死 token 处置（接进布局或删除） | `palettes.py` + `download_layout` | 脚本断言全库无零消费 token | TODO |
| `R13-18` | 排版角色接线（分组标题 h2 / 页面标题 h1） | `download_layout`、`manage_panel` | 每屏 ≥2 个非 body 角色 | TODO |
| `R13-19` | 设置页分组间用 `section=40` 间距 | `settings_groups.py`（**只改常量引用，不增行数**） | 该文件余量仅 15 行；若超，落 `settings_actions.py` | TODO |
| `R13-20` | i18n 假绿收口：767 条中文可见文案分批迁 `tr()` | 分 5 批，每批 ≤170 条 | 扩展 `i18n_coverage` 为 AST 扫描，计数只降不升 | TODO |
| `R13-21` | 像素回归基线 | `scripts/ui_screenshot_audit.py` + 基线图 | 阈值比对可跑通，且**能对人工注入的 2px 位移报警**（反向自检） | TODO |

### 依赖与顺序

```
R13-01/02/03/04 (P0)
      ├→ R13-05/06 (断点/多屏)
      ├→ R13-08/09/10 (触控/键盘/a11y)
      └→ R13-11/12/13 (性能)   ← 必须先有 R13-02 的 min_height 才能测准
R13-07 独立（等 manage_panel 的并行改动收敛）
R13-14/15/16 → R13-17/18/19 → R13-20/21
```

---

## 8. 质量清单（发布前逐条过）

### 8.1 适配

- [ ] 1920×1080 / 1440×900 / 1366×768 / 1280×800 / **1240×729** / **1216×684** / **1093×614** / 1024×768 / **1020×560** / **900×480**
- [ ] 字号 1.0 / 1.3 / 1.6 × 上述尺寸：主操作区 `visibleRegion == rect`，**无横向滚动**
- [ ] **`911×512`（1366×768@150% 工作区）与 `900×480`**：工具行要么完整可见，要么已折叠进「更多 ▸」且该菜单可点开
- [ ] `QT_SCALE_FACTOR` 1.0 / 1.25 / 1.5 / 1.75 / 2.0 下最小窗 ≤ 工作区
- [ ] 双屏 / 拔屏 / 混合 DPI：窗口不落到屏幕外；最小尺寸按新屏重算
- [ ] Windows 贴靠（左/右半屏、四分之一）：无控件被裁、无横向滚动

### 8.2 视觉与设计系统

- [ ] 四主题 × 6 强调色 = 24 组对比度矩阵全绿
- [ ] 灰度下：状态形状（7 种）与左缘刻度尺仍可辨
- [ ] 无字符图标（✓ ✕ ⚠ ⊘）出现在 UI
- [ ] 无 Primer 色字面量（`#0d1117/#58a6ff/#30363d/#e6edf3/#8b949e/#161b22`）
- [ ] 每屏至多 1 个 `#panelPrimary`
- [ ] 死 token 清零或登记为"刻意不提供"

### 8.3 性能

- [ ] `bench_startup`：冷 ≤2.0s（离屏）/ exe ≤3.5s（`health_check`）
- [ ] `bench_resize`：所有尺寸重排 ≤16ms
- [ ] 主题切换 ≤80ms；四主题轮换 ≤300ms
- [ ] 500 行 `add` ≤80ms、`update` ≤4ms；`progress_widgets == 0`
- [ ] 动效期间帧间隔中位数 ≥30fps；`prefers_reduced_motion` 与 `ck_animations` 均能全关
- [ ] 长时挂机内存有界（`mem_soak`）

### 8.4 无障碍

- [ ] 每个主操作可 **Tab 到达**且有可见焦点环
- [ ] `F6` 页签循环；`Esc` 关对话框；`Enter` 在表格上打开详情
- [ ] `a11y_audit.py` 计数只降不升
- [ ] 对比度矩阵全绿（含 HC ≥7:1）

### 8.5 交互与文案

- [ ] 空态统一走 `build_empty_state`（4 处）
- [ ] 权限/磁盘满错误就近提示，不弹窗
- [ ] 中文可见文案全部走 `tr()`（新守卫只降不升）
- [ ] 无静默 `except`（异常预算棘轮只降不升）

### 8.6 工程门禁

- [ ] `ruff check gcm` 0 error；`mypy gcm` Success
- [ ] `qss_validate` 24 组（4 主题 × 3 缩放 × 2 动效态）全绿
- [ ] `doc_drift_check` = DRIFT 0
- [ ] 定向回归 → 快集 →（必要时）慢集
- [ ] `PyInstaller` 打包 + `health_check.py --json` 真启动
- [ ] 新增模块均有生产调用点（无孤儿）
- [ ] `evidence/rev13-*.json` + `docs/验证记录-<日期>-Rev13.md` 已落

---

## 附录 A · 关键行号（执行时以代码为准复核）

| 主题 | 位置 |
|------|------|
| 断点常量 | `gcm/ui/layout.py:16-21` |
| 屏幕尺寸推导 | `gcm/ui/layout.py:39-48`、`main_window.py:698,712,824-827` |
| 布局模式 | `main_window.py:720+`（`_apply_layout_mode`） |
| 死分支 | `main_window.py:2844-2854` |
| 设计 token | `gcm/ui/palettes.py:128-140` |
| 排版阶梯 | `gcm/ui/typography.py:43-53` |
| QSS 状态 | `gcm/ui/qss_build.py:113,156,170,183-188,252-254` |
| 历史按钮（未自绘） | `gcm/ui/manage_model.py:299-339` |
| 管理页列宽 | `gcm/ui/manage_panel.py:161` |
| 空态直书 | `gcm/ui/manage_panel.py:185` |
| 动效时长 | `gcm/ui/batch_conclusion.py:195,207`、`gcm/ui/row_anim.py:37` |
| 进度表 6 列 | `gcm/ui/progress_table.py:91-110,130-131` |
| 主操作区 | `gcm/ui/download_layout.py:295,315` |
| 截图脚本（只存不断言） | `scripts/ui_screenshot_audit.py:82-90,132` |

## 附录 B · 复现命令

```bash
# 规模 / 漂移 / 静态
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/dev_metrics.py
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/doc_drift_check.py
./.venv/Scripts/python.exe -m ruff check gcm
./.venv/Scripts/python.exe -m mypy gcm

# 布局与性能
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest tests.test_ui_compact_visibility -v
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe scripts/bench_g70_ui.py

# 归因（判断是否是工作区改动造成）
git worktree add /tmp/gcm-head HEAD --detach
# 在 /tmp/gcm-head 里跑同一条测试；若绿 → 回归来自工作区改动
```

## 附录 C · 与既有文档的分工（防两套要求打架）

| 主题 | 归属 |
|------|------|
| 视觉三原则 / 色彩预算 / 排版声部 | Rev11 §2（**设计宪法，不改**） |
| V-12 / V-15 / V-17 / V-22 的实现 | Rev11-C W6（**不重复实现**） |
| 跨设备断点 / 多屏 / 触控 / 键盘 / 性能预算 / a11y / token 债 / i18n 假绿 / 像素回归 | **本文（Rev13）** |

> 若某任务在两处都出现：**实现归 Rev11-C，量化验收归本文**；不允许两份代码。
