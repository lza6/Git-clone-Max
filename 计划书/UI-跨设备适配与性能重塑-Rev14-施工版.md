# UI 跨设备适配与性能重塑 — Rev14（施工版）

> **效力**：本文件是这一主题的**唯一施工口径**。`计划书/UI-跨设备适配与性能重塑-Rev13.md`
> 降为**设计论证与首轮实测档案**（保留，不再作为执行依据）；两者冲突时以**本文件 + 当前代码**为准。
> Rev11 仍是视觉设计宪法，Rev11-C 仍是「收尾打磨」计划，两者不被本文件覆盖（分工见附录 B）。
>
> **施工状态（2026-11-05）**：B1–B6 已执行。19 个任务里 **15 个 DONE**、
> **4 个 CUT（附实测理由，见 §8.2 状态列）**、`R14-18` 处于 DOING（棘轮已落 + 批次 1 完成）。
> 证据 `evidence/rev14.json`，验证记录 `docs/验证记录-2026-11-05-Rev14.md`。
> 全快集 2120 例 · 3 例红均为既有版本/日志同步问题（不属本轮，见验证记录 §5）。
>
> **基线**：`main` @ `f6b913e` + 工作区（Rev11/Rev12/Rev13-P0 与 Rev14 均在工作区）；
> `gcm.__version__ = 9.18.8`；编写日 **2026-11-05**。
>
> **本文件不是事实源**。事实源仍是代码（行为）、`gcm/__init__.py`（版本）、
> `docs/architecture.md`（分层）、`policy.md`（交付门禁）、`test_ledger.md`（接缝）、
> `evidence/*.json`（证据）、`docs/验证记录-*.md`（每轮真实输出）。本文只放**还没落地的活**与**凭什么算完成**。

**证据标签**（与 `policy.md` §3 一致）：`已验证`＝本轮实际跑过并看到结果；`静态确认`＝读代码确认；
`合理推断`＝有间接证据；`待验证`＝缺环境/权限。

**状态口径**：只允许 `TODO` / `DOING` / `DONE` / `CUT（附理由+拍板人）` / `BLOCKED（附最小缺失条件）`。
`DONE` 必须有 `evidence/rev14-*.json` + `docs/验证记录-<日期>.md`；禁止「基本完成」。
执行完把任务行尾部改成 `DONE 2026-11-XX · evidence/rev14-<批次>.json`。

---

## 1 执行摘要

这个产品是**PyQt6 单机桌面工具**，不是 Web 应用：没有 HTML（`grep -c "text/html"` = 0，`已验证`），
没有位图资源，没有移动端前端。所以模板里的每一条都要**同构替换**成桌面等价物，而不是照搬：
「断点 320/768/1024/1440」→ **逻辑像素档位 1020/1100/1440**；「LCP/INP/CLS」→ **冷启动分段 / UI 线程单次操作 / 首帧后位移**；
「dvh/svh、刘海安全区」→ **多显示器工作区 / 任务栏 / DPI 缩放 / 贴靠**；「Service Worker 离线」→ **失败分类 + 持久重试队列 + 离线指示**；
「CSS containment / GPU 合成」→ **避免整窗 `setStyleSheet` / 限制 `update(rect)`**；「srcset / art direction」→ **图标策略**（零位图）。

施工口径定为一句话：**desktop-first + 密度自适应，先测准再改，只做能在验收里被断言的事。**
交付分 5 个批次共 **19 个任务（R14-01…R14-19）**，其中适配正确性（断点/多屏/键盘/无障碍）与反馈闭环（骨架/空态/离线）
占 11 个，是真正的用户可见缺口；性能批次改成「**先建度量工具，再按实测决定改不改**」，因为本轮实测推翻了
Rev13 关于主题切换与重排的两条瓶颈假设（§2.3）。**另有 4 项被明确删掉**，理由同样在 §2.3 与 §8.4。

关键前提：Rev13 的 P0 四项（矮窗折叠、字号下限、窄窗列折叠真正生效、文档漂移归零）**本轮复核未回退**，
64 例定向回归全绿、`doc_drift_check` = DRIFT 0，因此它们不进入本文件的施工清单，只保留守卫。

---

## 2 事实基线（2026-11-05 现测）

### 2.1 模板项目 → 本项目等价物

| 模板项 | 本项目对应物 | 现状 |
|--------|-------------|------|
| 断点 320/768/1024/1440px | 逻辑像素档 **XS/S/M/L**（1020 / 1100 / 1440） | 三档已存在、`wide` 是死枚举 → R14-01 |
| mobile-first / desktop-first | **desktop-first + 密度自适应** | 用户 100% 在桌面；「移动」= 小屏笔记本 / 高 DPI / 分屏窄窗 |
| Core Web Vitals | 冷启动分段、UI 线程单次耗时、首帧后位移 | 两条已有实测，位移项 `待验证`（§4.1） |
| srcset / art direction | 多 DPI 图标 | **零位图**，图标全走 `QStyle.standardIcon`（映射 16 项 / 调用点 12 处） |
| dvh/svh/lvh、安全区 | 多显示器工作区 / 任务栏 / 贴靠 / 拔屏 | `availableGeometry` 已正确；`screenChanged` **0 处** → R14-02 |
| Service Worker 离线 | `errors.classify_error` + `app/retry_queue.py`（持久/退避/jitter） | 已有；缺**全局离线状态** → R14-10 |
| CSS containment / GPU | 避免整窗 `setStyleSheet`、限制重绘区域 | 唯一 QSS 产地已收敛到 `qss_build.py` |
| 字号缩放 / 无障碍 | `font_scale ∈ [0.9, 1.6]`、四主题对比度、键盘可达 | 字号下限已强制；键盘与命名是最大缺口（§6.4） |

### 2.2 本轮真实跑过的命令与结果

| 命令 | 结果 | 标签 |
|------|------|------|
| `scripts/dev_metrics.py` | 135 py 文件 / **33,997** 行；超 800 行 **3**（main_window 3244、service 1970、engine 1667）；500–800 行 6 个（settings_panel 795、progress_table 795、settings_groups 785、repo_db 750、manage_panel 678、repo_detail_dialog 570）；>100 行函数 **19**；`except`/pass-only **752/284**；tests **192** 文件 / 37,554 行 | 已验证 |
| `scripts/doc_drift_check.py` | `DRIFT 0` | 已验证 |
| `python -m ruff check gcm` | `All checks passed!` | 已验证 |
| `python -m mypy gcm` | `Success: no issues found in 134 source files` | 已验证 |
| `unittest`（rev13_adapt + ui_compact_visibility + g57_responsive + rev11_visual + rev12_usability） | `Ran 64 tests · OK`（15.2s） | 已验证 |
| 冷启动分段（离屏，进程内计时） | Qt 导入 **27.0ms** → `QApplication` **7.2ms** → `gcm.ui` 导入 **154.3ms** → 主窗构造 **153.2ms** → 首帧 **17.1ms**；**合计 358.7ms** | 已验证 |
| `w.setStyleSheet(_themed_qss())`，1240×840 | **42.6 / 49.7 / 44.4 ms**；QSS **8,177** 字符 | 已验证 |
| resize → `processEvents` 完成（用户可感知重排） | 1020×584 **19.3ms**；1100×700 7.2ms；1240×729 4.6ms；1366×768 4.4ms；1440×900 5.2ms；1600×1000 8.2ms；1920×1080 6.9ms | 已验证 |
| `_apply_layout_for_width` × 20 次 / `_apply_layout_mode` × 20 次 | **0.01ms / 0.03ms** 每次 | 已验证 |
| `scripts/bench_g70_ui.py` | 500 行 `add` **32.22ms**、`update` **2.02ms**、`progress_widgets` **0**；QSS 首次 2.18ms / 缓存 0.016ms；空库启动 **113.15ms** | 已验证 |
| 离屏无障碍探测（`MainWindow` 1240×840） | 主操作 **14 个中 11 个无 `accessibleName`**；151 个可聚焦控件中 **142 个无名字**；`QCheckBox` **22/22** 缺、`QSpinBox` **19/19** 缺、`QComboBox` **8/8** 缺、`QLineEdit` **34/36** 缺 | 已验证 |
| AST 统计中文可见字面量（去 docstring、去 `tr()` 实参） | 全库 **1,483** 条，其中 `gcm/ui` **777** 条（main_window 168、manage_panel 74、repo_detail_dialog 68、statistics_dialog 46、update_flow 39、progress_table 37、help_dialog 35、menu_bar 35） | 已验证 |
| `qss_build.py` 静态扫描 | `:focus` 规则 **4 块**，覆盖 QLineEdit/QSpinBox/QComboBox/QPlainTextEdit/QTextEdit/QPushButton/QTableWidget/QTableView/QTabBar；**缺 QCheckBox/QRadioButton/QToolButton/QListWidget** | 已验证 |
| `grep` 关键符号 | `"wide"` 消费点 **0**（仅 `layout.py:78` 返回它）；`screenChanged` **0**；`setTabOrder` **0**；`keyPressEvent` **0**；`skeleton` **0**；`QShortcut` **8**；`TOKENS["row"]["touch"]` **0**；`TOKENS[` 访问 **12 处**；`apply_type(` 生产调用 **3 处**；裸 `setDuration(数字)` **3 处**（220/180/600） | 已验证 |
| `evidence/health_g84.json`（历史） | 打包 exe 真实启动 `elapsed_ms = 4034` | 已验证（历史证据） |
| 四主题 × 6 强调色对比度矩阵 | 正文最小 9.25（nord）、弱文本 5.96、输入边框 3.07、accent/panel 4.79、on_cta/cta 10.84 —— AA 全过 | 引用 Rev13 §4.2，本轮未重跑；**改色值前必须重跑** |

### 2.3 对 Rev13 的修正（本轮实测推翻了什么）

| Rev13 原结论 | 本轮实测 | 处置 |
|-------------|---------|------|
| B1「主题切换整窗 `setStyleSheet` **121ms**，四主题轮换 532ms」→ `R13-12` 优化到 ≤80ms | 单次 **42.6 / 49.7 / 44.4ms**（QSS 8,177 字符），**已低于 80ms 目标** | **`R13-12` 删除**。降级为「若某轮实测超 80ms 再回来做」。不为已达标的东西写代码 |
| B3「改尺寸重排 **11–18ms**，根因是 `_on_layout_tick` 内跑列遍历」→ `R13-13`「列遍历只在断点跨越时执行」 | 重排用户可感知 **4.4–19.3ms**（首次 19.3，其余 ≤8.2ms）；而 `_apply_layout_for_width` **0.01ms/次**、`_apply_layout_mode` **0.03ms/次** —— **列遍历占重排成本的 <1%**，19ms 几乎全在 Qt 布局内部 | **`R13-13` 删除**：归因错误，按原方案改完收益 ≈0.03ms。要压首次重排只能动控件树结构，不在本主题范围 |
| B2「构造期 **1614ms**（冷），profile 前三 `_build_ui` 82ms / `repo_db.__init__` 39ms / `setStyleSheet` 25ms×3」→ `R13-11`「合并 3 次 setStyleSheet + 设置页懒构建」 | 离屏构造 **153.2ms**（`gcm.ui` 导入另有 154.3ms，属模块导入不是构造）；`setStyleSheet` 单次 42–50ms 已含在上面 | `R13-11` **降为条件任务**（`R14-17`）：先由 `R14-16` 在**打包 exe** 上量两条口径，再决定改不改 |
| R5「`_apply_layout_for_width` 用 `QTableView.horizontalHeaderItem()` → 死分支」 | 已修（`R13-03` DONE），现用 `model().headerData()` + `NARROW_WIDTH=1020` | 出清单，只留守卫 |
| R13 记「10 个运行时 `QShortcut`」「`main_window` 45 处 `QMessageBox`」「`accessibleName` 21 处」 | 实测 `QShortcut` **8**、`QMessageBox.` **39**、`setAccessibleN` **30 处/14 文件**，而主操作缺名 **11/14** | 本文件按实测值重写（§6.4） |
| R12「`gcm/ui` 767 条中文未走 `tr()`」 | 本轮 AST 口径 **777 条**（新增模块带来 +10） | 采纳 777 作为基线，见 `R14-18` |
| R16「冷启动 3–4 秒」 | 离屏 Python 路径 **358.7ms**；exe **4034ms**（历史证据） | 两条口径都真，但**不能混算**：用户感知的是 exe 的 4.03s（含 PyInstaller 解包），离屏那段只能用来定位「解包之后」的耗时。任务改成「先把口径固定成可复现的脚本」 |

### 2.4 复核结论：Rev13 P0 四项未回退

| ID | 复核方式 | 结论 |
|----|---------|------|
| R13-01 矮窗裁切 | `unittest tests.test_ui_compact_visibility`（9 尺寸） | 绿。断言一行未改 |
| R13-02 工具行折叠 | `unittest tests.test_rev13_adapt`（7 例）+ 实测 1020×560 被最小尺寸夹到 1020×584 后 `layout_mode` = `compact`、工具行完整 | 绿 |
| R13-03 窄窗列折叠 | `unittest tests.test_g57_responsive`（含 `TestNarrowFoldOnRealModel`） | 绿 |
| R13-04 文档漂移 | `scripts/doc_drift_check.py` | `DRIFT 0` |

复核是「确认没坏」，不是新交付；这四条的 `DONE` 依据仍是 `evidence/rev13-p0.json`。

### 2.5 与并行会话的归因

工作区 81 项未提交变更里，`gcm/app/undo.py`、`gcm/ui/menu_bar.py`、`gcm/ui/undo_bar.py`、`gcm/util/oplog.py`、
`gcm/ui/status_strip.py` 等属 Rev12 可用性批次。**本文所有实测数字含这批改动**。
执行纪律：动 `main_window.py` / `qss_build.py` / `status_strip.py` 前先 `git status --porcelain` 重冻结一次基线；
与并行改动同一文件的批次**串行**（§8.3）。

### 2.6 本轮**没有**做的验证（不得声称通过）

| 项 | 原因 | 补偿方案 |
|----|------|---------|
| 真机双屏 / 拔插 / 混合 DPI | 本机单屏 | `R14-02` 用可注入屏幕参数的纯函数 + 离屏模拟覆盖 |
| Windows 高对比度模式、系统「减少动画」真实开关 | 需交互式系统设置 | 现有 `prefers_reduced_motion` 纯函数；`QStyleHints.reduceMotion` 在 Qt 6.11 **不存在**（`hasattr=False`），不要照搬 Web 的 `prefers-reduced-motion` |
| 屏幕阅读器全流程（NVDA/JAWS） | 需真人 + 交互式环境 | 只能保证「名称/角色/顺序/焦点/对比度」五项静态可验证 |
| 首帧后布局位移（CLS 等价） | 无采集点 | `R14-16` 的 `bench_resize.py` 顺带记录 `moveEvent` 次数 |
| PyInstaller 打包 + 真机点击 | 属发版动作 | §9 收尾门禁 |
| CI 三平台 | 本机仅 Windows | 快集 + coverage 本机跑（Rev13-P0 已跑：2067 例 / 81%）；三平台由 CI 承接 |

---

## 3 响应式策略

### 3.1 结论：desktop-first + 密度自适应

不做 mobile-first：本产品没有移动端载体，`900×480` 以下**明确拒绝**（`ABSOLUTE_MIN`）而不是硬挤 ——
挤出来的排版在小屏笔记本上比「开不全」更难用。真实痛点是四类场景：**1366×768@125%/150% 笔记本**（工作区 1093×614 / 911×512）、
**1024×600 上网本**、**分屏窄窗**、**175%/200% 缩放**。断点服务于这四类，不服务于不存在的手机。

### 3.2 断点表（目标态，逻辑像素）

| 档 | 判定（`layout.breakpoint_name()`） | 实测锚点 | 行为 |
|----|-----------------------------------|---------|------|
| **XS** | `w < 1020` **或** `h < 580` | 900×480 / 911×512（`effective_minimum` 夹回后的真实尺寸） | 工具行折叠进「更多 ▸」（已实现）；进度表隐「大小/健康」（已实现）；结论条只留铭牌+读数（已实现）；设置导航折叠（已实现） |
| **S** | `1020 ≤ w < 1100` **或** `580 ≤ h < 820` | 1020×584、1240×729、1366×768（实测均落 S） | 隐快捷填充与设置导航（已实现）；结论条单行；行高 24（`TOKENS.row.compact`，接进进度表） |
| **M** | `1100 ≤ w < 1440` **且** `820 ≤ h < 1000` | 1440×900（实测 standard） | 全量控件；行高 26（`TOKENS.row.default`） |
| **L** | `w ≥ 1440` **且** `h ≥ 1000` | 1600×1000、1920×1080（实测返回 `wide`，但**无任何差异化行为**） | 新增：管理页列宽按权重分配（仓库名 ≥320）；进度表行高 28；结论条右留白改由「详情」吃掉，不再无差别 `stretch` |

**这不是新增一套阈值**：`WINDOW_MIN(1020,560)` / `COMPACT_WIDTH 1100` / `COMPACT_HEIGHT 820` / `WIDE_WIDTH 1440` / `WIDE_HEIGHT 1000`
全部保留，`layout_mode()` 保留为兼容包装，新增的是**把 `unsupported` 改名为 `XS` 并给出四档语义**。

**与模板断点的映射**：900（受支持下限）≈ mobile 下限、1020/1100 ≈ tablet、1440 ≈ desktop、≥1920 ≈ large。

### 3.3 硬编码处置（三处，两处已完成）

| # | 位置 | 状态 |
|---|------|------|
| 1 | `layout.py:16` `WINDOW_MIN` 高度与字号矛盾 | **已解决另一条路**：`tools_fold_height(scale)`（580/600/620，斜率 66.7px/0.1 档）+ 折叠，而不是抬最小高 —— 因为 `effective_minimum()` 会按工作区夹回，抬常量在 1366×768@150% 下无效（实测） |
| 2 | `layout.py:19-20` `WIDE_WIDTH/WIDE_HEIGHT` 无消费 | **未解决** → `R14-01` |
| 3 | `main_window._apply_layout_for_width` 死分支 | **已解决**（`R13-03`），现走 `model().headerData()` + `NARROW_WIDTH` |

### 3.4 触控 / 指针 / 键盘

| 项 | 现状（实测） | 目标 | 落点 |
|----|-------------|------|------|
| 行高 / 触控目标 | 进度表 26px、管理表 34px；`TOKENS["row"]["touch"]` **不存在** | 桌面 26 / 触屏 ≥34；主操作按钮 ≥40 | `palettes.py` 加 `row.touch = 34`；`progress_table` / `manage_panel` 在有触摸屏时取它 |
| hover 状态 | `QListWidget::item:hover` 有；**`QTableView/QTableWidget::item` 无 hover、无 selected 规则** | 行 hover 底色 `panel2`（只作提示，不作唯一编码）；`::item:selected` 用 `accent` 10% alpha，文字色不动 | `qss_build.py` → `R14-07` |
| 键盘导航 | 运行时 `QShortcut` **8 个**（`Ctrl+=/-/0` 缩放、`Ctrl+1..3` 页签、`Ctrl+Alt+S/U/M`、`Ctrl+K` 命令面板）；`setTabOrder` **0**、`keyPressEvent` **0** | 每页签 Tab 顺序 = 视觉顺序；`F6` 页签循环；`Esc` 关对话框；表格 `Enter` 开详情 | 新 `gcm/ui/focus_order.py` → `R14-03` |
| 焦点可见 | `:focus` **4 块规则 / 覆盖 9 类控件**；缺 `QCheckBox/QRadioButton/QToolButton/QListWidget` | 每类可聚焦控件 ≥1 条 `:focus` | `qss_build.py` → `R14-05` |
| 触控手势 | 桌面无 pinch/pan；表格滚动由原生滚动条承担 | 不引入双指手势（Qt 自绘成本高、与滚动条打架） | 不做 |

### 3.5 多显示器 / 工作区 / 安全区等价物

- **屏幕变更监听**：现状 `screenChanged` / `screenAdded` / `screenRemoved` / `primaryScreenChanged` **全库 0 处**，
  `layout.py:76`、`main_window` 多处只问 `primaryScreen()`。窗口从副屏拖回、或副屏被拔掉后，**最小尺寸与 DPI 规则不重算** → `R14-02`。
- **记住「上次所在屏」**：`_serialize_geometry` 已存 `M:` 与四元组；补存 `screen.name()`，拔屏后按名字找不到 →
  回主屏 + 一条 WARN 日志（不静默）。
- **安全区**：桌面无刘海。等价约束是**任务栏**（已用 `availableGeometry`，正确）与 **Windows 贴靠 Snap**
  —— 贴靠有 ±4px 偏差，`clamp_window_size` 的 0.95/0.85 系数已覆盖，不加特判。
- **`dvh/svh/lvh` 的等价物不存在**：Qt 没有「视口单位」，窗口高度就是唯一来源。这条不实现，不是遗漏。

### 3.6 字号缩放

`font_scale ∈ [0.9, 1.6]`（`typography.FONT_SCALE_MIN_UI = 0.9`，D-13 已把 `zoom_out()` 下限收敛到它，
QSS 底层仍按 `[0.8, 1.6]` 钳制）。字号变化**必须**触发折叠重新判定 —— 已实现（`_apply_font_scale` 末尾补布局重算），
守卫 `test_font_scale_change_reevaluates_fold`。这条不需要再做。

### 3.7 图标与位图（模板「图片优化」的等价物）

零位图资源：`find` 无 `.ico/.svg/.png`，图标全走 `QStyle.standardIcon`（16 项语义映射、12 处调用）。
因此 `srcset` / art direction 无对应物，「图片优化」塌缩为两条规则：**不引入打包图标集**（体积 + 与四主题不一致）；
需要品牌图标时用**单色 QPainter 路径自绘**，颜色取 `current_palette()["text_dim"]`，随主题走。
多 DPI 由 Qt 的 `QIcon` 内建多尺寸承担，不做手工切图。

---

## 4 性能蓝图

### 4.1 指标替换与目标

| Web 指标 | 本项目等价指标 | 本轮实测 | 目标 | 度量 |
|---------|--------------|---------|------|------|
| LCP < 2.5s | 打包 exe 冷启动到首帧可交互 | **4034ms**（`evidence/health_g84.json`，历史） | exe ≤3.5s | `health_check.py --json` |
| LCP（定位用） | 离屏 Python 路径分段 | **358.7ms**（27.0 / 7.2 / 154.3 / 153.2 / 17.1） | 构造段 ≤120ms、首帧 ≤50ms | 新 `scripts/bench_startup.py` |
| INP < 100ms | UI 线程单次操作耗时 | 重排 4.4–19.3ms；`setStyleSheet` 42.6–49.7ms；500 行 `add` 32.22ms / `update` 2.02ms | 单次 ≤100ms（已达标）；重排首次 ≤16.7ms（**未达标，19.3ms**） | `bench_resize.py` |
| CLS < 0.1 | 首帧后 500ms 内可见元素位移次数 | **未测**（`待验证`） | 0 次 | 测试期记录 `moveEvent` |
| 60fps 动画 | 动效帧预算 16.7ms | 220/180/600ms 三档；`QGraphicsOpacityEffect` 单次 0.004ms（Rev11 实测） | 动画期帧间隔中位数 ≥30fps | 动画期 `paintEvent` 计数 |
| 首屏资源 | 构造期控件数 / QSS 长度 | 151 可聚焦控件、QSS **8,177** 字符 | QSS ≤9,000 字符；控件数**不得增加** | `dev_metrics.py` + 离屏探测 |

> `4034ms`（exe，含解包）与 `358.7ms`（离屏 Python）**不是同一口径，禁止混算**。前者是用户感知，后者只能用来定位解包之后的耗时。

### 4.2 瓶颈排序（按本轮实测收益重排）

| # | 瓶颈 | 实测 | 处置 |
|---|------|------|------|
| P-a | 打包 exe 冷启动 4.03s | `health_check` 4034ms | **先测准**（`R14-16`），确认解包 vs 构造各占多少，再决定改什么（`R14-17`） |
| P-b | 首次重排 19.3ms（1020×584） | resize→`processEvents` 19.3ms；后续同尺寸 0–8.2ms | **不做**。列遍历只占 0.03ms，成本在 Qt 布局内部；要压它得重排控件树，回归面远大于收益 |
| P-c | 整窗 `setStyleSheet` 42.6–49.7ms | 已低于 80ms 目标 | **不做**（原 `R13-12` 删除）。主题切换是低频操作，50ms 在感知阈值内 |
| P-d | 500 行批渲染 32.22ms | `add 32.22 / update 2.02 / progress_widgets 0` | 已达标，**保持**：不得回退成逐行 `QProgressBar`（守卫 `bench_g70_ui`） |
| P-e | 并发进度更新吞吐 | 未测（`待验证`） | `R14-16` 顺带用引擎真实信号做 200 行×10 轮，目标 ≤4ms/轮 |
| P-f | 设置页构造期同步建完全部控件 | 构造 153.2ms 的一部分 | 仅当 `R14-17` 判定构造段是 exe 启动主因时才做懒构建 |

### 4.3 手段（Qt 语境下的 lazy loading / code splitting / 资源优化）

- **懒加载**：唯一候选是「设置」页首次切换时构建。**前提是实测证明构造段是启动主因**（`R14-17` 的门槛），
  否则不做 —— 懒构建会让 `test_g76_wiring` 一类「控件存在性」断言失效，改造成本高于收益。
  守卫要求：切页后必须能 `getattr` 到全部控件（防「懒加载变没加载」）。
- **代码切分**：`gcm/ui` 已 58 个模块，不为拆而拆。真正贴 800 红线的三个（`settings_panel` 795、
  `progress_table` 795、`settings_groups` 785）**一个字节都不要再加**，新逻辑落新模块。
- **containment / GPU 合成**：Qt 无 containment。等价手段两条 —— ①不写内联 `setStyleSheet`（会击穿主题且触发整窗重刷）；
  ②自绘 delegate 用 `update(rect)` 限定重绘区域（`progress_delegate` 已如此）。
  **不用** `QGraphicsDropShadowEffect` 做卡片阴影：数百个滚动项上会明显掉帧，层次已由 `panel_primary/panel/panelGhost` 三档 + 1px glow 表达。
- **离线自动降级**：`QT_QPA_PLATFORM=offscreen` 时已自动关动效（保留）。

### 4.4 度量工具规格（新脚本，各 ≤150 行）

| 脚本 | 接口 | 输出 | 门槛 |
|------|------|------|------|
| `scripts/bench_startup.py` | 无参；内部起子进程跑离屏计时，另外解析 `health_check` 历史 JSON | `{"qt_import_ms","app_ms","ui_import_ms","ctor_ms","first_frame_ms","exe_ms"}` | 写进 `evidence/`；构造段 >120ms 才允许立优化项 |
| `scripts/bench_resize.py` | 无参；8 尺寸 × 3 字号（1.0/1.3/1.6），记录 resize→`processEvents` 与 `moveEvent` 次数 | `{"sizes":[{"w","h","scale","reflow_ms","moves"}],"over_budget":[...]}` | `over_budget` 为空才算达标 |
| `scripts/a11y_audit.py` | 无参；离屏构造主窗，统计各控件类「缺 `accessibleName` 数」+ 主操作清单覆盖率 | `{"main_actions_missing":[...],"by_class":{...}}` | 棘轮：计数**只降不升**；主操作清单 100% 命名 |

三个脚本都进 CI 快集（`scripts/ci_run_fast.py` 用黑名单，默认纳入）。

### 4.5 离线与降级

已有：`errors.classify_error`（**唯一**重试判据）+ `app/retry_queue.py`（持久化 + 退避 + 50–100% jitter）+ 失败面板 + `ui/busy.py`（上下文管理器，`finally` 必恢复）。
缺**全局离线状态**：`status_strip` 只有并发/进行中/成功/失败读数，没有「网络整体不可用」的指示 →
`R14-10` 增一个 `offline` 指示点（**几何形状编码，不用字符**：微软雅黑缺 ✓ ✕ ⚠ ⊘ 字形会变 tofu，这是硬红线），
由「最近 3 次失败全为 network 类」触发，恢复即消，不弹窗、不阻塞。

---

## 5 设计系统规范

### 5.1 Token 架构：已有，需「接线」而非重建

```
palettes.py   → 4 主题 × 18 色键 + TOKENS（radius / border / spacing / row）+ ACCENT_PRESETS（6 档 × 深浅成对）
typography.py → 3 字族 + 7 级字号（display 28 / h1 20 / metric 18 / h2 15 / body 13 / mono 12 / small 11）+ 字距
qss_build.py  → 唯一 QSS 产地（按 palette × scale × motion 缓存）
```

**实测欠账**：`TOKENS[` 全库只有 **12 处**访问（全在 `qss_build` 与 `progress_table`），
`spacing.xl(24)` / `spacing.2xl(32)` / `spacing.inset(20)` / `spacing.section(40)` / `row.compact(24)` / `row.manage(34)` / `radius.chip(10)`
**零消费**。留着不接 = 装点门面，正是 Rev11 §1.3 诊断过的「token 没兑现」→ `R14-13` 二选一：接进真实落点，或删除并注明「刻意不提供」。

| Token | 值 | 现状 | 处置 |
|-------|----|------|------|
| `radius.card / control / bar / tab / checkbox / chip` | 6 / 5 / 5 / 8 / 4 / 10 | card/control/bar/tab/checkbox 已接；**chip 零消费** | chip 接 `status_pill` 圆角（或删） |
| `spacing.xs / sm / md / lg` | 4 / 8 / 12 / 16 | 已接（QSS 内） | 保持 |
| `spacing.xl / 2xl / inset / section` | 24 / 32 / 20 / 40 | **零消费** | `section` 接 `download_layout` 三段之间；其余删除并注明 |
| `border.default / focus` | 1 / 2 | 已接 | 保持 |
| `row.compact / default / manage` | 24 / 26 / 34 | default 已接；compact/manage **零消费** | `compact` 接 S 档进度表；`manage` 接管理表 `defaultSectionSize` |
| `row.touch` | — | **不存在** | 新增 34，触屏时启用（`R14-07`） |

### 5.2 色彩：已验证达标，不动色值

四主题（`deep` / `light` / `nord` / `hc`）× 6 强调色（teal / blue / violet / cyan / indigo / magenta，深浅成对）= 24 组。
最小对比度：正文 9.25（nord）、弱文本 5.96、输入边框 3.07、accent/panel 4.79、on_cta/cta 10.84 —— WCAG AA 全过，HC 主题 ≥7:1。
**深/浅模式**：`theme=auto` 走注册表读系统深浅；`QStyleHints.colorScheme()` 在 Qt 6.11 可用（本机实测返回 `Unknown`），可作第二判据。
现状即目标，唯一规则是**颜色只有一个来源**：自绘 delegate / 日志高亮 / 托盘图标都读 `theme.current_palette()`
（G75 实测过「QSS 一套旧蓝、行内进度条一套新青」的双真相）。

### 5.3 间距 / 圆角 / 阴影

- 间距：**4pt 网格** `4 / 8 / 12 / 16 / 24 / 32`；卡内边距 20；三段之间 40。
- 圆角：卡 6 / 控件 5 / 进度条 5 / 页签 8 / 复选 4 / chip 10。
- 阴影：**Qt QSS 无 `box-shadow`**。层次只用三档卡面权重（`panel_primary` / `panel` / `panelGhost`）+ 1px 内嵌高光 + 边框对比。
  加 `QGraphicsDropShadowEffect` 会在滚动列表上掉帧，不做。

### 5.4 排版

7 级阶梯已定义，但**生产调用只有 3 处**：`batch_conclusion.phase_lbl`（small）、`onboarding.kicker`（small）、
`status_strip.brand_lbl`（small）。管理页/进度表正文仍靠 QSS 统一字号，`mono` 只落在进度 delegate 一处。
→ `R14-14`：分组标题用 `h2`（15px/600）、每屏标题用 `h1`（20px/700）。验收 = 每屏至少 **2 个非 body 角色**（守卫断言，不靠目检）。

### 5.5 图标与插画

`QStyle.StandardPixmap` 语义映射 16 项，实际调用点 12 处，无打包资源。规则：
**图标只表达动作，不表达状态** —— 状态走 `status_pill.draw_shape` 的 7 种几何形状（形状互不相同才算色盲可辨）。
新增图标优先 `QStyle`；确无对应时单色 QPainter 路径，颜色取 `current_palette()["text_dim"]`。

### 5.6 组件一致性（legacy 修正清单）

| 组件 | 问题（静态确认） | 修正 |
|------|-----------------|------|
| `HistoryButtonDelegate`（`manage_model.py:331`） | 仍用 `style.drawControl(CE_PushButton)` 画**原生按钮** → 深色主题下是浅色方块；列内文本 `"查看"` 未走 `tr()` | 改 QPainter 自绘（`panel2` 底 + 1px `border` + 圆角 5）+ `tr("查看")` → `R14-15` |
| `batch_overview` + `batch_conclusion` | 两个批次条语义相邻，小屏双条争高 | 保留两个（`test_g46_nav_batch` 依赖 `batch_total_lbl`）；S 档隐 `batch_overview`、只留结论条 |
| `manage_panel` 空态 | `QLabel("尚未导入仓库…")` 直书（`manage_panel.py:185`），未走 `empty_state` 工厂 | 改用 `build_empty_state(title, hint, filtered)` → `R14-09`（现全库只有 `progress_table` 1 处接入） |
| 管理页数据列 | 无 mono，数字不对齐；5 个 `Fixed` 列宽硬编码（150/78/70/64/84）+ `setColumnWidth(5, 70)` 重复一次 | 与 Rev11-C `P-02` 合并执行，**不要两处各写一遍** |

---

## 6 UX/UI 模式库

### 6.1 层级与可扫视性

已有：批次结论条（display 28px，全屏唯一）、三档卡面权重、左缘刻度尺（`edge_ruler`）。
**缺页面级标题层级**：`download_layout` 的分组标题（「仓库地址（每行一个）」）与正文同字号 → `R14-14`。

### 6.2 反馈与可预期性

| 场景 | 现状（实测） | 目标 |
|------|-------------|------|
| 打开大目录 / 扫描 | 无进度、无光标变化 | `busy()` 统一置忙 + 表格骨架行（3 行灰色占位，QPainter 画，**不建子控件**） → `R14-08` |
| 统计中心首开 | 同步计算，界面冻结 | 后台线程 + 骨架柱 |
| 导出报表 / 诊断包 | 已有 `busy()`（全库唯一生产调用点：`main_window.py:2639`） | 保持 |
| 表格行 hover | **无** | `R14-07` 补 QSS |
| 点击涟漪 | Qt 无原生，自绘收益低 | **不做** |

反模式：不用 `QProgressDialog` 做不确定进度 —— Windows 上会抢焦点并阻塞父窗。

### 6.3 导航与寻路

现状：3 页签（下载/管理/设置）+ 设置页左侧导航 + 命令面板（`Ctrl+K`）+ 菜单栏（Rev12 新增，18px）。
补：`F6` 页签循环、设置导航上下键、管理表 `Enter` 等价双击、`Esc` 关对话框 → `R14-03`。
无面包屑需求（三层以内、页签即顶层），不为模板硬凑。

### 6.4 无障碍（WCAG 2.1 AA）

**实测缺口**（离屏构造 `MainWindow` 1240×840 逐项读取）：

| 要求 | 现状 | 动作 | 验收 |
|------|------|------|------|
| 1.4.3 对比度 ≥4.5 | 最小 5.96，已达标 | 不动 | 现有守卫 |
| 4.1.2 名称/角色 | **主操作 14 个里 11 个无 `accessibleName`**（缺：`btn_save_list` / `combo_load_list` / `btn_load_list` / `btn_star_import` / `btn_clear` / `btn_open` / `btn_failures` / `btn_retry_queue` / `btn_diag_bundle` / `btn_edit_quick` / `btn_pause_batch`；已有：`btn_start` / `btn_cancel` / `btn_more_tools`）。全库 151 个可聚焦控件中 **142 个无名字**：`QCheckBox` 22/22、`QSpinBox` 19/19、`QComboBox` 8/8、`QLineEdit` 34/36、`QPushButton` 54/56、`QToolButton` 5/8 | 先补 11 个主操作（屏幕阅读器读不出用途 = 功能性缺陷），再补设置页 22 个复选 + 19 个数字框 | `scripts/a11y_audit.py` 计数**只降不升**且主操作 100% 命名 → `R14-04` |
| 2.1.1 键盘可达 | `QShortcut` 8 个运行时快捷键；`setTabOrder` **0**、`keyPressEvent` **0** | `focus_order.py` + `F6` | 遍历断言每个主操作可 Tab 到达 → `R14-03` |
| 2.4.7 焦点可见 | `:focus` 4 块 / 9 类控件；缺 `QCheckBox` / `QRadioButton` / `QToolButton` / `QListWidget` | 补这 4 类 | QSS 扫描守卫：每类可聚焦控件 ≥1 条 → `R14-05` |
| 1.4.11 非文本对比 ≥3 | 边框 3.07 | 不动 | 现有守卫 |
| 3.3.1 错误可识别 | `classify_error` 三段式（429/5xx 可重试；401/403/404/磁盘满/平台限制不可） | 保持（唯一判据，不许在 UI 里另写关键字表） | 现有守卫 |
| 4.1.3 屏幕阅读器实机 | 未做 | 不做（需 NVDA/JAWS + 真人，`待验证`） | — |

### 6.5 表单与输入

仓库地址是 `QPlainTextEdit`（多行），**无内联校验**，错误在开始后才出现。→ `R14-11`：失焦时跑
`url_lib.parse_urls`（纯函数、不联网）做**行级**校验，无效行加浅红左边框，状态行给**非阻塞**提示「N 行无法识别」，
**不弹窗**（弹窗会打断粘贴流程）。桌面无 `input type`，等价物是 `QSpinBox` 范围（并发 1–32、超时、限速），已具备。

### 6.6 动效预算

| 位置 | 时长 | 目的 | reduced-motion |
|------|------|------|----------------|
| 批次开始（结论条填充） | 220ms OutCubic | 告知「已开始」 | 跳过，直接 100% |
| 批次结束（读数脉冲） | 180ms | 告知「结束了」 | 跳过 |
| 行完成消隐 | 600ms | 定位「刚完成的那行」 | 跳过（用户设置项 `ck_animations`） |
| 主题切换 | **0**（不做淡入淡出） | — | 整窗重刷已 42–50ms，淡入只会更慢 |

三个时长现在**硬编码在 3 处**（`batch_conclusion.py:195,207`、`row_anim.py:37`）→ `R14-12` 建 `motion.py`
（`fast=120 / base=220 / slow=600`，`easing=OutCubic`），守卫断言 UI 内不再出现裸 `setDuration(数字)`。

### 6.7 空态与边界

| 场景 | 现状 | 目标 |
|------|------|------|
| 零仓库（下载页） | 已走 `build_empty_state` | 保持 |
| 零仓库（管理页） | 裸 `QLabel`（`manage_panel.py:185`） | 改用工厂 → `R14-09` |
| 筛选后为空 | `filtered=True` 变体已支持但仅 1 处使用 | 管理页筛选空态用 `filtered=True` 变体，文案区分「没有数据」与「筛掉了」 |
| 权限错误（目标目录只读） | `main_window` 有 **39 处** `QMessageBox.` | 输入行就近提示，不弹窗（分批评审，优先权限/磁盘满两类） |
| 磁盘满 | `classify_error` 已判不可重试 | UI 给「清理 / 换目录」动作入口（不是只弹一句话） |
| 网络整体不可用 | 无全局状态 | 离线指示点 → `R14-10` |

---

## 7 技术架构

### 7.1 技术栈：不换

| 候选 | 判定 | 理由 |
|------|------|------|
| QML / Qt Quick | 不采用 | 全量重写 UI，回归面 = 192 个测试文件；打包体积与启动时间都会变差 |
| Web 前端（Electron / Tauri） | 不采用 | 进程模型 / 打包 / Windows 权限全部推翻；本项目零 Web 资产，`http_api.py` 只出 JSON/SSE/CSV |
| **保持 PyQt6 6.11 + QSS + 自绘 delegate** | **采用** | token 层、四主题、自绘组件都已具备；缺的是**接线与预算**，不是框架能力 |

### 7.2 模块增量

```
gcm/ui/
  layout.py            # 改：breakpoint_name() 四档 + 屏幕参数注入（保持纯函数）
  focus_order.py       # 新（≤150）：tab 链 + F6 + Esc
  skeleton.py          # 新（≤120）：表格骨架（QPainter，不建子控件）
  offline_indicator.py # 新（≤80）：离线点，挂 status_strip
  motion.py            # 新（≤60）：MOTION token + easing
scripts/
  bench_startup.py / bench_resize.py / a11y_audit.py   # 新（各 ≤150）
```

**尺寸约束**（沿用 `CLAUDE.md` §6）：新模块 200–400 行、硬上限 800；函数上限 50 行（复杂函数 100）。
`main_window.py`（3244）在三处超限文件里，只做**转发**，新 UI 逻辑一律落独立模块。

### 7.3 QSS 策略

- 唯一产地 `qss_build.py`。**禁止**控件级内联 `setStyleSheet("...")`（击穿主题 + 触发整窗重刷）。
- 选择器只用 `objectName`（`#primary` / `#panelPrimary` / `#panelGhost`）与类型选择器；
  **禁止**复杂后代选择器（Qt 只支持子集，复合控件上表现不一致）。
- 改 QSS 后必跑 `qss_validate`：**24 组 = 4 主题 × 3 缩放档（0.8 / 1.0 / 1.5）× 2 动效态**，
  现由 `tests/test_g75_visual_system.test_every_theme_and_scale_passes_validator` 断言。

### 7.4 设置项接线（新增字段的唯一入口）

`R14-06` 需要 `settings.ui_state: dict`（管理页列宽等），该字段**当前不存在**。铁律：
**写入侧与消费侧必须同时接线**（只写不读 = 用户改了没用，守卫 `TestG69_18WiringClosure` 会抓孤儿）；
新增字段必须进 `SettingsStore` 的默认值 + 序列化 + UI 入口三处。

### 7.5 响应式测试策略（当前最大的测试缺口）

| 层 | 现状 | 目标 |
|----|------|------|
| 纯函数 | `layout_mode` / `effective_minimum` / `clamp_window_size` / `fit_dialog_to_screen` / `tools_fold_height` 已有用例 | 扩成**参数化矩阵**：11 种屏幕 × 5 字号 × 2 菜单栏态，断言「最小尺寸 ≤ 工作区」且档位单调 |
| 离屏布局 | `test_ui_compact_visibility` 9 尺寸（`visibleRegion == rect`） | 扩到 **12 尺寸 × 3 字号**，加断言「表格无横向滚动」 |
| DPI | `clamp_scale_for_dpi` 4 例 | 用 `QT_SCALE_FACTOR=1.25/1.5/1.75/2.0` 起子进程跑同一套断言；离屏屏是 800×800，**必须注入屏幕尺寸**而非真实查询 |
| 像素回归 | `scripts/ui_screenshot_audit.py` **只存图不断言**（历史事故：4 个空框连续 4 轮无人发现） | 阈值比对（与基线逐像素差 ≤0.5% 通过，超阈值出 diff 图）+ **反向自检**：人工注入 2px 位移必须报警 → `R14-19` |
| 无障碍 | 无 | `a11y_audit.py` 棘轮 + 主操作 100% 命名断言 → `R14-04` |
| 性能 | 无脚本 | `bench_startup` / `bench_resize` 固定口径 → `R14-16` |
| 真机 | `docs/RELEASE_CHECKLIST.md` 人工 | 保持（杀软 / 代理 / 路径差异无法自动化，是历史最常见的假绿来源） |

### 7.6 守卫与台账

- 新增守卫必须在**快集**内（`scripts/ci_run_fast.py` 用黑名单，新文件默认纳入）。
- 新接缝登记进 `test_ledger.md`：现最大编号 **S51**，本主题占 **S52–S56**
  （S52 断点四档矩阵、S53 多屏重算、S54 键盘 tab 链、S55 无障碍命名棘轮、S56 像素回归阈值）。
- `doc_drift_check.py` 锚的是**机制**（超 800 行集合、>100 行函数数、超大文件行数表）。本文件只写「集合相等」与
  「集合大小」，不写会漂的单文件行数断言 —— 要写就必须在落盘后按最终实测同步三处（`CLAUDE.md` §6 / `docs/architecture.md` §4 / `README.md`）。

### 7.7 禁改清单（本主题内）

| 对象 | 约束 |
|------|------|
| `gcm/app/` | 不新增 Qt 依赖（现 5 个文件白名单，守卫 `test_g80_boundaries`） |
| `gcm/git/` 之外 | 不新增 `["git"` 子进程 |
| `gcm/db/` 之外 | 不新增 SQL（`reports.py` 为已登记例外） |
| 色值 / 字族 / QSS 骨架 | 只接线不加新范式；改色必须先重跑 24 组对比度矩阵 |
| `settings_panel` / `progress_table` / `settings_groups` | 795 / 795 / 785，**一个字节都不要加** |

---

## 8 分期施工

### 8.1 批次总览

| 批次 | 主题 | 任务 | 为什么排这个位置 |
|------|------|------|-----------------|
| **B1** | 适配正确性 | R14-01…07（7 项） | 用户可见的正确性缺口（断点死枚举、多屏不重算、键盘不可达、无障碍缺名），且互不冲突，可并行提交 |
| **B2** | 反馈与空态 | R14-08…11（4 项） | 依赖 B1 的 `focus_order` 与空态工厂接线顺序，放第二批避免同文件打架 |
| **B3** | 设计系统接线 | R14-12…15（4 项） | 纯外观/代码质量，回归面小；`qss_build` 与 B1 的 R14-05/07 同文件 → **串行于 B1 之后** |
| **B4** | 性能度量 | R14-16…17（2 项） | 先建工具再决策；`R14-17` 可能是空任务（判定不改就关掉） |
| **B5** | 文案与像素 | R14-18…19（2 项） | 批量机械工作，最后做，避免与前面批次抢行号 |
| **B6** | 收尾 | 台账/文档/打包 | 见 §9 |

### 8.2 任务表

#### B1 · 适配正确性

| ID | 任务 | 落点 | 验收（必须可断言） | 状态 |
|----|------|------|-------------------|------|
| `R14-01` | 断点四档 + `wide` 真实消费 | `ui/layout.py`（`breakpoint_name()` 纯函数，保留 `layout_mode` 兼容）、`main_window._apply_layout_mode` 增 L 分支 | 11 屏幕 × 5 字号矩阵档位单调；**`wide` 与 `standard` 可测量差异**（管理页仓库名列宽 / 进度表行高 28）；窄窗仍隐「大小/健康」不回退 | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |
| `R14-02` | 多显示器重算 + 记住屏名 | `ui/layout.py` 纯函数（注入屏幕尺寸）+ `main_window` 接 `primaryScreenChanged` / `screenAdded` / `screenRemoved`；`_serialize_geometry` 记 `screen.name()` | 注入假屏幕尺寸的纯函数测试（拔屏 → 最小尺寸按新屏重算）；找不到屏名回主屏且**有 WARN 日志**；`grep screenChanged` ≥1 | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |
| `R14-03` | 键盘导航 | 新 `ui/focus_order.py`（≤150）+ 主窗接线 | 遍历断言每个主操作可 Tab 到达；`F6` 循环 3 页签；`Esc` 关当前对话框；`setTabOrder` 调用 >0 | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |
| `R14-04` | 无障碍命名补齐 | 就地（主操作 11 个 → `QCheckBox` 22 → `QSpinBox` 19 → `QComboBox` 8 → `QLineEdit` 34）+ 新 `scripts/a11y_audit.py` | 主操作清单 **100% 有 `accessibleName`**；`a11y_audit` 计数只降不升 | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |
| `R14-05` | 焦点可见补 4 类 | `ui/qss_build.py`（QCheckBox / QRadioButton / QToolButton / QListWidget） | QSS 扫描守卫：每类可聚焦控件 ≥1 条 `:focus`；`qss_validate` 24 组全绿 | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |
| `R14-06` | 管理页列宽自适应 + 持久化 | `ui/manage_panel.py` + `db/settings.py` 新增 `ui_state`（**写+读两侧**） | 重启后列宽保持（重读 `settings.json` 断言）；1020 宽无横向滚动 | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |
| `R14-07` | 表格行 hover + 触控行高 | `ui/qss_build.py` + `ui/palettes.py`（`row.touch = 34`） | `QTableView::item:hover` 与 `::item:selected` 存在；`TOKENS["row"]["touch"]` **有消费点**；灰度可辨（截图目检） | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |

#### B2 · 反馈与空态

| ID | 任务 | 落点 | 验收 | 状态 |
|----|------|------|------|------|
| `R14-08` | 骨架屏 + 忙态扩展 | 新 `ui/skeleton.py`（≤120，QPainter 不建子控件）；`busy()` 扩到目录扫描 / 统计中心 | 慢路径有可见占位；`findChildren` 数量**不增**（骨架不建控件）；`busy` 生产调用点 >1 | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |
| `R14-09` | 空态统一 | `ui/manage_panel.py:185` 改用 `build_empty_state`；筛选空态走 `filtered=True` | 全库裸空态 `QLabel` 清零；`filtered` 文案与「无数据」可区分 | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |
| `R14-10` | 离线指示点 | 新 `ui/offline_indicator.py`（≤80）挂 `status_strip` | 3 次网络类失败 → 显示；恢复即消；**几何形状**编码（无字符符号）；不弹窗 | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |
| `R14-11` | 输入区行级校验 | `ui/download_layout.py` / 主窗失焦回调，复用 `url_lib.parse_urls` | 无效行有可视标记；状态行提示条数；**不弹 `QMessageBox`**；纯函数可单测 | **CUT** —— 已存在（G54-1 的 300ms 防抖校验：有效/无效计数 + 最多列 3 条无效行 + 语义色）。Rev13 §1.3 把它记为缺失是错的 |

#### B3 · 设计系统接线

| ID | 任务 | 落点 | 验收 | 状态 |
|----|------|------|------|------|
| `R14-12` | `MOTION` token | 新 `ui/motion.py`（≤60）；替换 `batch_conclusion.py:195,207`、`row_anim.py:37` | 守卫断言 UI 内无裸 `setDuration(数字)`；三个时长全部来自 token | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |
| `R14-13` | 死 token 处置 | `ui/palettes.py` + `download_layout` + `progress_table` + `manage_panel` + `status_pill` | 脚本断言：`TOKENS` 每一项**要么有消费点、要么在 `palettes.py` 注明「刻意不提供」** | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |
| `R14-14` | 排版角色接线 | `ui/download_layout.py`、`ui/manage_panel.py` | 每屏 ≥2 个非 body 角色（守卫）；`apply_type(` 生产调用从 3 处增加 | **CUT** —— QSS 全局 `QWidget{font-size}` 会覆盖 `apply_type` 的字号；在下载页做要么无效、要么动 24 组 QSS 矩阵。层级收益小于回归风险，改由 Rev11-C `P-02` 承担 |
| `R14-15` | 历史按钮自绘 | `ui/manage_model.py:331`（`drawControl` → QPainter）+ `tr("查看")` | 深色主题下不再出现浅色方块（截图）；文本走 `tr()`；`i18n_coverage` 绿 | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |

#### B4 · 性能

| ID | 任务 | 落点 | 验收 | 状态 |
|----|------|------|------|------|
| `R14-16` | 度量工具 | 新 `scripts/bench_startup.py`、`scripts/bench_resize.py` | 两脚本可跑出 JSON 并落 `evidence/`；`bench_resize` 的 `over_budget` 明确列出超 16.7ms 的尺寸 | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |
| `R14-17` | 启动优化（**条件任务**） | `main_window` 的 `setStyleSheet` 路径 / 设置页懒构建 | 门槛：exe `health_check` >3.5s **且** 构造段 >120ms 才开工；否则标 `CUT（实测未超预算，2026-11-XX）` | **CUT** —— ctor 144.9ms 仅占 exe 启动 3815ms 的 3.8%（`scripts/bench_startup.py` 实测），优化构造对用户不可感知 |

#### B5 · 文案与像素

| ID | 任务 | 落点 | 验收 | 状态 |
|----|------|------|------|------|
| `R14-18` | i18n 收口 | `gcm/ui` 777 条分 5 批（main_window 168 → manage_panel 74 → repo_detail_dialog 68 → statistics_dialog 46 → 其余）；`scripts/i18n_coverage.py` 扩为 AST 棘轮 | 每批 ≤170 条；AST 扫描计数**只降不升**；`zh.py`/`en.py` 键同步 | **DOING 2026-11-05** —— AST 棘轮已落（`i18n_coverage.py --update-baseline`，1517→1459）+ 批次 1 迁移 70 处可见文案；余 4 批待续 |
| `R14-19` | 像素回归基线 | `scripts/ui_screenshot_audit.py` + 基线图 | 阈值比对可跑通；**反向自检**：人工注入 2px 位移必须报警 | **DONE 2026-11-05** · `evidence/rev14.json` · `docs/验证记录-2026-11-05-Rev14.md` |

### 8.3 依赖与串行约束

```
B1  R14-01 → R14-02 (都动 layout.py，串行)
    R14-03 / R14-04 / R14-06 可并行（不同文件）
    R14-05 / R14-07 同动 qss_build.py → 串行，且排在 B3 之前
B2  R14-09 独立；R14-08 → R14-10（都挂 status_strip/表格）
B3  R14-12 / R14-13 / R14-14 / R14-15 均须在 B1 的 qss_build 改动落盘后
B4  R14-16 必须先于 R14-17
B5  R14-18 最后做（改行号，会干扰前面的 diff）
收尾 全部落盘后再同步三处文档 + doc_drift
```

### 8.4 明确不做（CUT，附理由）

| 原项 | 处置 | 理由 |
|------|------|------|
| Rev13 `R13-12` 主题切换优化 | **CUT** | 实测 42.6/49.7/44.4ms，已低于原定 80ms 目标。不为已达标的东西写代码 |
| Rev13 `R13-13` 列遍历优化 | **CUT** | `_apply_layout_for_width` 0.01ms/次、`_apply_layout_mode` 0.03ms/次；19ms 首次重排几乎全在 Qt 布局内部，原归因错误 |
| QML / Electron 迁移 | CUT | 回归面 = 全部 192 个测试文件；零 Web 资产 |
| 打包图标集 / ttf | CUT | 体积增加 + 与四主题不一致；只用系统内置字族 |
| `QGraphicsDropShadowEffect` 卡片阴影 | CUT | 数百滚动项上掉帧；层次已由三档卡面 + 1px glow 表达 |
| `dvh/svh/lvh`、刘海安全区、pinch 手势 | CUT | 桌面无对应物，硬凑只会引入不可测代码 |
| 屏幕阅读器实机流程（NVDA/JAWS） | 不做（`待验证`） | 需真人 + 交互式环境；只保证五项静态可验证 |
| `R14-11` 输入内联校验 | **CUT（已存在）** | G54-1 早已实现 300ms 防抖的「有效 N / 无效 M」提示条（最多列 3 条无效行 + 语义色）。Rev13 把它记成缺失是错的 |
| `R14-14` 排版角色接线 | **CUT（会被 QSS 覆盖）** | QSS 全局 `QWidget{font-size}` 优先于 `apply_type` 的 `setFont`；在下载页做要么无效、要么改 QSS 动 24 组校验矩阵。层级收益小于回归风险，改由 Rev11-C `P-02`（管理页数据列 mono）承担 |
| `R14-17` 启动优化 | **CUT（占比不足）** | `bench_startup` 实测 ctor 144.9ms / exe 3815ms = 3.8%；即便砍掉一半构造时间，用户也感知不到。等 exe 启动的结构性原因（解包）有独立结论再说 |

---

## 9 发布前质量清单

### 9.1 适配

- [ ] 1920×1080 / 1440×900 / 1366×768 / 1280×800 / 1240×729 / 1216×684 / 1093×614 / 1024×768 / 1020×560 / 900×480：主操作区 `visibleRegion == rect`
- [ ] 字号 1.0 / 1.3 / 1.6 × 上述尺寸：无横向滚动，工具行要么完整可见要么已折叠且「更多 ▸」可点开
- [ ] `QT_SCALE_FACTOR` 1.0 / 1.25 / 1.5 / 1.75 / 2.0：最小窗 ≤ 工作区
- [ ] 双屏 / 拔屏 / 混合 DPI：窗口不落到屏幕外，最小尺寸按新屏重算，回主屏有 WARN
- [ ] Windows 贴靠（左/右半屏、四分之一）：无控件被裁

### 9.2 视觉与设计系统

- [ ] 四主题 × 6 强调色 = 24 组对比度矩阵全绿（改过色值必须重跑）
- [ ] 灰度下状态形状（7 种）与左缘刻度尺仍可辨
- [ ] 无字符图标（✓ ✕ ⚠ ⊘）出现在 UI
- [ ] 无 Primer 色字面量（`#0d1117` / `#58a6ff` / `#30363d` / `#e6edf3` / `#8b949e` / `#161b22`）
- [ ] 每屏至多 1 个 `#panelPrimary`
- [ ] 死 token 清零或已登记「刻意不提供」

### 9.3 性能

- [ ] exe `health_check` ≤3.5s；离屏构造段 ≤120ms、首帧 ≤50ms
- [ ] `bench_resize` 的 `over_budget` 为空（或已登记取舍）
- [ ] 500 行 `add` ≤80ms、`update` ≤4ms；`progress_widgets == 0`
- [ ] 动效帧间隔中位数 ≥30fps；`prefers_reduced_motion` 与 `ck_animations` 均能全关
- [ ] QSS ≤9,000 字符；可聚焦控件数不得增加（骨架屏不建控件）

### 9.4 无障碍

- [ ] 主操作 14/14 有 `accessibleName`
- [ ] 每个主操作可 Tab 到达且有可见焦点环；每类可聚焦控件 ≥1 条 `:focus`
- [ ] `F6` 页签循环；`Esc` 关对话框；表格 `Enter` 开详情
- [ ] `a11y_audit.py` 计数只降不升

### 9.5 交互与文案

- [ ] 空态统一走 `build_empty_state`（管理页 + 筛选空态）
- [ ] 权限 / 磁盘满错误就近提示，不弹窗
- [ ] `gcm/ui` 中文可见文案 `tr()` 覆盖只升不降，`zh.py`/`en.py` 键同步
- [ ] 无静默 `except`（异常预算 752 只降不升）

### 9.6 工程门禁

- [ ] `ruff check gcm` 0 error；`mypy gcm` Success
- [ ] `qss_validate` 24 组全绿
- [ ] `doc_drift_check` = DRIFT 0
- [ ] 定向回归 → 快集（必要时慢集）
- [ ] `PyInstaller` 打包 + `health_check.py --json` 真启动
- [ ] 新模块均有生产调用点（无孤儿）
- [ ] `test_ledger` S52–S56 已登记；`evidence/rev14-*.json` + `docs/验证记录-<日期>.md` 已落

---

## 10 已知边界与本轮未验证

- 本机单屏，**多显示器与混合 DPI 只能靠注入参数的纯函数覆盖**；真实拔插行为属 `待验证`。
- Windows 高对比度模式与系统「减少动画」开关未实测；Qt 6.11 无 `QStyleHints.reduceMotion`，只能走自有设置项 + 注册表探测。
- 屏幕阅读器（NVDA/JAWS）实机流程不在范围内，只保证名称/角色/顺序/焦点/对比度五项静态可验证。
- 首帧后位移（CLS 等价）当前**没有采集点**，`R14-16` 建好工具前不许声称「无抖动」。
- 对比度矩阵本轮未重跑，引用 Rev13 与 Rev11 的实测值；**任何色值改动都必须先重跑 24 组**。
- CI 只在 Linux 上跑慢集，Windows 特有的编码/路径问题必须本机验证；CI 绿 ≠ 手上的 exe 能起来。
- `gcm/ui` 777 条中文未走 `tr()` 是**累计量**，分 5 批迁移期间 `i18n_coverage` 会一直有计数，棘轮规则是「只降不升」而非「归零即通过」。

---

## 附录 A 复现命令

```bash
# 规模 / 漂移 / 静态
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/dev_metrics.py
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/doc_drift_check.py
./.venv/Scripts/python.exe -m ruff check gcm
./.venv/Scripts/python.exe -m mypy gcm

# 适配守卫（本主题的主回归集，64 例 / 15s）
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest \
  tests.test_rev13_adapt tests.test_ui_compact_visibility tests.test_g57_responsive \
  tests.test_rev11_visual tests.test_rev12_usability

# 性能
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe scripts/bench_g70_ui.py
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe scripts/bench_startup.py   # R14-16 后可用
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe scripts/bench_resize.py    # R14-16 后可用

# 无障碍 / 文案
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe scripts/a11y_audit.py       # R14-04 后可用
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/i18n_coverage.py

# 归因：判断红灯是否来自工作区改动
git worktree add /tmp/gcm-head HEAD --detach   # 在 /tmp/gcm-head 里跑同一条测试，绿则回归来自工作区
```

## 附录 B 与既有文档的映射与分工

| Rev13 任务 | 本文件 | 说明 |
|-----------|--------|------|
| `R13-01` / `R13-02` / `R13-03` / `R13-04` | **不出清单**（已 DONE，本轮复核未回退） | 依据 `evidence/rev13-p0.json`；只留守卫 |
| `R13-05` 断点四档 | `R14-01` | 新增 L 档可测量差异 |
| `R13-06` 多显示器 | `R14-02` | 同 |
| `R13-07` 管理页列宽 | `R14-06` | 同 |
| `R13-08` hover / 触控行高 | `R14-07` | 同 |
| `R13-09` tab 链 + F6 | `R14-03` | 同 |
| `R13-10` 无障碍标注 | `R14-04` | 缺口从「10/12」修正为「11/14」，并扩到 22 复选 + 19 数字框 |
| `R13-11` 启动优化 | `R14-17` | 降为**条件任务**（先测准） |
| `R13-12` 主题切换优化 | **CUT** | 实测已达标 |
| `R13-13` 列遍历优化 | **CUT** | 归因错误，收益 ≈0.03ms |
| `R13-14` 骨架屏 | `R14-08` | 同 |
| `R13-15` 离线指示 | `R14-10` | 同 |
| `R13-16` MOTION token | `R14-12` | 同 |
| `R13-17` 死 token | `R14-13` | 补 `row.compact/manage` 接消费点 |
| `R13-18` 排版角色 | `R14-14` | 同 |
| `R13-19` 设置页 section 间距 | 并入 `R14-13` | 该文件余量 15 行，超出则落 `settings_actions.py` |
| `R13-20` i18n 收口 | `R14-18` | 基线 767 → **777** |
| `R13-21` 像素回归 | `R14-19` | 同 |
| —（Rev13 未列） | `R14-05` 焦点 4 类、`R14-09` 空态统一、`R14-11` 表单内联校验、`R14-15` 历史按钮自绘、`R14-16` 度量工具 | 本轮实测新识别 / Rev11 `V-12`、`V-22` 的落地落点 |

**与其他文档的分工**：视觉三原则 / 色彩预算 / 排版声部 → Rev11 §2（设计宪法，不改）；
`V-12` / `V-15` / `V-17` / `V-22` 的**实现** → Rev11-C W6；跨设备断点 / 多屏 / 键盘 / 无障碍 / 性能预算 / token 债 / i18n / 像素回归
→ **本文**。同一任务在两处出现时：**实现归 Rev11-C，量化验收归本文**，不允许两份代码。
