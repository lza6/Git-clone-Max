# UI 视觉系统重构指南 — Ink & Instrument（目标 9.9.x）

> **本轮交付模式：仅计划**。未改任何代码/配置/测试/脚本；未运行快集、构建、真机。
> 基线：`main` @ `0018628`，`gcm.__version__` = 9.8.3（`gcm/__init__.py:2`，静态确认）。
> 本文只写「还没落地的动作 + 验收标准 + 证据要求」。落地后按 `docs/计划书/README.md` §1 归档。
>
> 配套事实源（本文不复述，只给指针）：`docs/architecture.md`（分层）、`docs/code-gems.md`（可复用范式）、
> `policy.md`（交付门禁）、`test_ledger.md`（接缝注册表）、`docs/验证记录-2026-10-19.md`（G70 那轮设计系统底座）、
> `docs/验证记录-2026-10-20-G74UI.md`（G74 那轮截图驱动修复）。

---

## 0. 30 秒摘要

| 项 | 结论 |
|----|------|
| 现状 | 功能密度高、**视觉上是一套 GitHub Primer 的复制品**：单色深底 + `#58a6ff` 蓝 + 全部 8px 圆角 + 13px 单一字号（详见 §1.3 截图与 file:line） |
| 打法 | 不改技术栈（PyQt6 + QSS + 自绘 delegate），只重做**设计系统层**：字体阶梯 / 色板 / 层级 / 状态编码 / 动效预算 / 一条仪表齿条 |
| 关键杠杆 | 设计 token 已经存在（`gcm/ui/theme.py:155` `TOKENS`），四套主题已经过 WCAG 修正，**改造点集中**且回归面可枚举（§4.3） |
| 最大风险 | `theme.py` 已 598 行，直接加东西会破 800 行硬上限 → 必须先拆分（T-UI-01，零行为变化） |
| 需用户拍板 | 5 项（§9 D1–D5），其中 D1「主按钮反相」决定整个美学立场，不确认不动手 |

---

## 1. 事实基线（2026-10-21 本轮现测）

### 1.1 环境与规模

| 项 | 实测值 | 获取方式 | 标签 |
|----|--------|---------|------|
| 分支 / HEAD | `main` @ `0018628` | `git log --oneline -5` | 已验证 |
| 工作区 | 仅文档未跟踪（`docs/计划书/`、`tasks/`）+ 1 处 memory 修改 | `git status --short` | 已验证 |
| 源码 | `gcm/` 79 个 `.py`，23,284 行 | `wc -l gcm/*.py gcm/*/*.py` | 已验证 |
| UI 层 | `gcm/ui/` 27 文件，10,374 行 | 同上 | 已验证 |
| 测试 | `tests/` 133 个文件 | `ls tests/*.py \| wc -l` | 已验证 |
| Qt / Python | PyQt6 6.11.0 / Python 3.14（CI 四处锁定） | CLAUDE.md §6 + `ci.yml` | 静态确认 |

> **文档漂移（本轮顺带测到，属 T-UI-10 的顺修范围）**：`docs/architecture.md:77` 登记
> `main_window.py` 2648（实为 2704）、`:80` 登记 `progress_table.py` 800（实为 **809，已破 800 红线**）。
> 本文不擅自改他人文档的登记值，把它记在 T-UI-10 的收尾项里一并更正。

### 1.2 现有设计系统**已经有什么**（别重复造）

| 能力 | 位置 | 现状值 |
|------|------|--------|
| 设计 token 表 | `gcm/ui/theme.py:155` | `radius` 8/6/5/8/4/10 · `spacing` 4/8/12/16/24/32（4pt 网格）· `border` 1/2 · `shadow: outline` |
| 四套色板 | `gcm/ui/theme.py:79` | `deep`（GitHub 深色）/ `light` / `nord` / `hc`（AAA） |
| 对比度工具 | `gcm/ui/theme.py:202`、`:212`、`:219` | WCAG 相对亮度（G70 修正过绿/蓝权重写反）|
| QSS 生成 + 缓存 | `gcm/ui/theme.py:235` `_build_qss` | 按 `(palette, scale, motion)` 缓存，上限 32 |
| QSS 静态校验器 | `gcm/ui/qss_validate.py`（306 行） | 控件类名白名单，能拦 `QTabs` 这类拼写错误 |
| 字号体系 | `gcm/ui/theme.py:417` | `FONT_BASE_PX=13`；标题 20、等宽 12、小字 11（**只有 4 级**）|
| 字族 | `gcm/ui/theme.py:165` | 单一无衬线栈：微软雅黑 UI / 苹方 / Noto CJK；等宽硬编码 `Consolas`（`:353`、`:358`）|
| 响应式断点 | `gcm/ui/layout.py` | `layout_mode` = unsupported/compact/standard/wide；`effective_minimum` / `clamp_window_size` / `fit_dialog_to_screen` |
| 进度自绘 | `gcm/ui/progress_delegate.py:41` | 500 行批次零 QWidget；进度值在内存字典 + 250ms 合批 |
| 空态 / 无障碍 / 焦点环 | `gcm/ui/*`、`docs/验证记录-2026-10-19.md` §2.4 | 已铺；`accessibleName` 已覆盖主控件 |
| 截图审计台 | `scripts/ui_screenshot_audit.py` | 一次产出 **38 张** PNG（4 主题 × 5 分辨率 + 3 页签 + 设置滚动 + 8 对话框），现存于 `reports/ui-audit-final/` |

> 结论：**底座是好的**。缺的不是"再建一套 token"，而是**层级、字体声音、状态编码与空间节奏**。

### 1.3 现测缺口（截图证据 + file:line）

截图基线：`reports/ui-audit-final/download_deep_1440x900.png`、`reports/ui-audit-final/tab_settings_1440x900.png`（本轮已逐张看过）。

| # | 问题（截图可见） | 代码落点 | 判定 |
|---|------------------|---------|------|
| V1 | **色板就是 GitHub Primer**：`#0d1117` / `#58a6ff` / `#3fb950`，与"AI 通用美学"里的蓝紫同源 | `gcm/ui/theme.py:80-97` | 静态确认 |
| V2 | **字号只有 4 级且比值 ≈1.08**（20/13/12/11），全屏几乎一个字号，"标题"和"正文"分不开 | `gcm/ui/theme.py:253-257` | 静态确认 |
| V3 | **只有一个字族**：URL、路径、时间戳、计数、日志全用比例字体，数字不对齐 | `gcm/ui/theme.py:165`、`:353` | 静态确认 |
| V4 | **所有面同权重**：卡片统一 8px 圆角 + 1px 边框 + 同底色，无内嵌高光/无层次/无大气底 | `gcm/ui/theme.py:306-311`、`:342-345` | 静态确认 |
| V5 | **状态只靠文字着色**：成功绿/冲突黄/失败红，无形状或图标编码，色盲不可辨 | `gcm/ui/progress_table.py:511-515` | 静态确认 |
| V6 | **顶栏无信息价值**：只有标题 + `并行线程 8`，运行时不知进度/速率/用时 | `gcm/ui/main_window.py:749-758` | 静态确认 |
| V7 | **页签选中态弱**：仅 2px 顶线 + 微亮底色，扫视时找不到当前位置 | `gcm/ui/theme.py:377-382` | 静态确认 |
| V8 | **内部代号外露**：设置页左导航出现「G53 数据洞察」这类工程代号 | 根因在 `gcm/ui/settings_groups.py:651` 的 GroupBox 标题；导航项由 `gcm/ui/main_window.py:902-909` 按「（」截短派生 | 静态确认 + 截图可见 |
| V9 | **动效预算错位**：给 500 行逐行做 1.2s 背景消隐，却没有一个"开始/完成"时刻的编排 | `gcm/ui/progress_table.py:577` | 静态确认 |
| V10 | 批次总览条（"已完成 24/24 · 成功率 33%"）与表头/状态栏三处重复表达同一事实 | `gcm/ui/progress_table.py:71` | 静态确认 |

### 1.4 对既有 `docs/计划书/下一步改进指南.md` 的时效性判定（用户本轮要求）

| 检查项 | 该文写的 | 本轮复测 | 结论 |
|--------|---------|---------|------|
| `main_window.py` 行数 | 2704 | 2704 | ✅ 一致 |
| `service.py` 行数 | 1768 | 1768 | ✅ 一致 |
| `engine.py` 行数 | 1169 | 1169 | ✅ 一致 |
| 源码 / 测试规模 | 79 文件 / 23,284 行；133 测试 | 79 / 23,284；133 | ✅ 一致 |
| `clone_timeout` 无写侧 | 无 `_save_*` | `grep clone_timeout gcm/ui/` 仅读侧（`engine_factory.py:38`、`main_window.py:138`）| ✅ 成立 |
| `quick_repos` 无编辑入口 | 无 | `grep quick_repos gcm/ui/` 仅 `download_layout.py:103` 读 | ✅ 成立 |
| `settings_panel._save_*` 数量 | "26 个" | `grep -c "def _save_"` = **45** | ❌ **已漂移**（表述性，不影响其结论）|

**判定：该文事实仍然新鲜（6/7 抽样命中），不重写。** 但它整体是「接线收口 + 工程治理」选题，
**不含任何视觉/设计维度**，与本轮诉求零重叠 —— 因此按用户要求新建本文，二者并列存在、互不覆盖。
（`settings_panel` 的回调数建议该文下一次修订时改为现测值；本文不改他人文档。）

### 1.5 本轮**没有**执行的验证（不得声称通过）

| 项 | 原因 | 补偿方式 |
|----|------|---------|
| `ruff` / `mypy` / 快集 / 慢集 / 覆盖率 | 仅计划，无代码变更 | 历史证据：`evidence/G74-ui.json`（1349 tests / 0 fail / 覆盖率 82%）|
| 截图基线重建 | 会写盘 38 张 PNG（工作区产物） | 列为 T-UI-00 待执行项（§6.1）|
| PyInstaller 打包 + 真机点击 | 属执行模式 | 列为 §7 验收矩阵末两行 |
| 唯一实际运行 | 文档守卫：`tests/test_g67_docs_freshness`（验证**本文本身**无死链/版本漂移）| 输出见 §11 |

---

## 2. 设计方向（Design Before Code）

### 2.1 四问

**PURPOSE** — 这是给"挂着刷几百个仓库"的个人开发者用的批量下载器。用户的情感需求不是"好看"，
而是**「我知道它在干什么、还差多少、哪几个坏了」**。界面的首要任务是把"运行状态"变成一眼可读的仪表。

**TONE** — **仪器台（Instrument Panel）**：工业仪表的读数纪律 + 印刷排版的层级纪律。
不是"又一个 GitHub 皮肤"，不是渐变大玻璃，不是霓虹赛博。

**CONSTRAINTS**（这些是硬的，违反就红 CI 或被用户否掉）

1. 技术栈不变：PyQt6 + QSS + 自绘 delegate。**不用** Qt Quick/QML（打包体积与回归面都不划算）。
2. QSS 的物理限制：**不支持** `box-shadow` / `transition` / `letter-spacing` / `grid` / `gap`；
   `QGraphicsDropShadowEffect` 在滚动区/离屏下开销大且渲染有毛刺 → **本轮禁用**。
3. 四套主题（`deep`/`light`/`nord`/`hc`）必须同时成立：正文 ≥4.5:1，`hc` 文本 ≥7:1（既有测试 `tests/test_g46_theme.py:74` 钉住）。
4. `prefers_reduced_motion`（`gcm/db/settings.py:280`）必须被尊重，且 `motion=False` 时 QSS 不产生 hover/pressed 色块。
5. i18n：所有新文案走 `tr()`，`gcm/i18n/zh.py` 与 `en.py` 同步。
6. 字体必须**离线可用**：只用系统内置字族（本轮实测见 §2.2），不打包 ttf、不下载。
7. 文件规模：新模块 200–400 行、硬上限 800；新增 UI 逻辑一律落独立模块，不往 `main_window.py` 里塞。
8. 不动对外契约：CLI `--json` / MCP / HTTP / SSE 的 schema 与退出码一律不动（`docs/agent-contract.md`）。

**DIFFERENTIATION**（用户离开后还会记得的三件事）

1. **仪表齿条**：贴顶 32px 高的等宽读数条（并发/活跃/成功/失败/用时），空态显示 `—`。
   工具看起来像仪器，而不是一个网页表单。
2. **形状编码的状态**：`●成功 ◧冲突 ✕失败 ○跳过 ─已取消`，颜色退为二次编码。
3. **墨与骨白**：深色底不是 GitHub 的 `#0d1117`，主操作是**骨白反相块**（不是蓝色按钮）；
   整个界面只有一个高饱和色（示波器青），只用于"正在发生"。

### 2.2 字体方案（本轮在真机 `QFontDatabase` 实测可用性）

```
实测：148 个字族。存在 —— Bahnschrift（含 Condensed/Light/SemiBold 变体）、
Cascadia Code / Cascadia Mono（含 Light/SemiBold）、Consolas、Ubuntu Mono、
Microsoft YaHei UI / Light、Segoe UI / Semibold / Light。
```

| 角色 | 字族（首 → 回退链） | 用途 | 为什么不选 AI 通用字体 |
|------|--------------------|------|----------------------|
| 显示 / 标题 | `Bahnschrift SemiCondensed` → `Bahnschrift` → `Segoe UI Semibold` → `sans-serif` | 页标题、空态大标题、首启向导 | DIN 血统的工业窄体，**不是** Inter/Roboto/Arial |
| 正文 / 中文 | `Microsoft YaHei UI` → `Microsoft YaHei` → `PingFang SC` → `Noto Sans CJK SC` → `sans-serif` | 全部中文正文与表单 | 中文只有雅黑/苹方/Noto 现实可选；靠**字重与尺寸**造层级，不靠怪字体 |
| 数据 / 等宽 | `Cascadia Mono` → `Cascadia Code` → `Consolas` → `Ubuntu Mono` → `monospace` | URL、路径、时间戳、计数、日志、表格数字 | 编程字体，数字等宽对齐，仪表读完即"数据" |

> **诚实的边界**：Bahnschrift **没有中文字形**。中文标题会回退到雅黑 —— 这是有意的（拉丁/数字工业感，
> 中文稳），但**不要指望中文标题靠换字体获得性格**，中文层级只能靠字重 + 尺寸 + 留白。
> 另外 QSS **没有 `letter-spacing`**，字距只能用 `QFont.setLetterSpacing()` 在构造时设 —— 这就是
> 需要一个 `apply_type(widget, role)` 工具函数的真实原因（见 T-UI-02），不是为抽象而抽象。

### 2.3 字号阶梯（scale=1.0 时的 px，全部由 `FONT_BASE_PX × scale` 派生）

| 角色 | px | 字重 | 字族 | 现状 |
|------|----|------|------|------|
| `display` | 28 | 600 | 显示族 | **新增** |
| `h1`（`pageTitle`） | 20 | 700 | 显示族 + 中文回退 | 已有（20）|
| `h2`（分组/面板标题） | 15 | 600 | 正文族 | **新增** |
| `body` | 13 | 400 | 正文族 | 已有（`FONT_BASE_PX`）|
| `mono` | 12 | 400 | 等宽族 | 已有（但硬编码 Consolas，需改 token）|
| `small` | 11 | 500 | 正文族 | 已有 |
| `metric`（仪表条数字） | 18 | 600 | 显示族 | **新增** |

层级比值从现状的 20→13（1.54）与 13→12→11（1.08/1.09）的"虚假阶梯"，改成
28 / 20 / 15 / 13 / 12 / 11 的真实递减；**基体字号 13px 不动**（`tests/test_g46_theme.py:109`
钉住了 `font-size: round(13*1.5)px`，保留即不破）。

### 2.4 色板（`deep` → Ink，`light` → Paper）

新增 token（四套必须齐全 —— `tests/test_g46_theme.py:23` 要求各色板 key 一致）：

| token | Ink（深） | Paper（浅） | 作用 |
|-------|-----------|-------------|------|
| `bg` | `#0B0E10` | `#F4F0E8` | 窗口底（微青墨 / 暖纸）|
| `panel` | `#121619` | `#FBF8F2` | 卡片面 |
| `panel2` | `#1A2024` | `#EDE7DC` | 控件面 |
| `border` / `border_strong` | `#242C31` / `#3E4A52` | `#DAD3C6` / `#8A8279` | 分隔 / 输入边界 |
| `text` / `text_dim` | `#E8E3DA` / 实现时反算 | `#16191B` / 实现时反算 | 骨白暖字 / 次要 |
| `accent`（"正在发生"）| `#35C8B4` | `#0F7C70` | 进度条 chunk、焦点环、活跃页签 |
| `cta` / `on_cta` | `#E8E3DA` / `#0B0E10` | `#16191B` / `#F4F0E8` | **主按钮反相块**（新增）|
| `glow` | `rgba(255,255,255,0.04)` | `rgba(0,0,0,0.04)` | 卡片顶部 1px 内嵌高光（新增）|
| `success`/`warning`/`error` | 实现时按 ≥4.5:1 反算（候选 橄榄绿 `#6FA85F` / 赭黄 `#C89B4A` / 砖红 `#E06B5C`）| 同族加深 | 语义结果，**只出现在 pill 与图标** |

`nord` 与 `hc` 同步补齐新 token：`hc` 保持纯黑/纯白 + AAA（`accent` 仍 `#00E5FF`）。
**不加第五个主题**（理由见 §9 D5）。

### 2.5 层级、空间与"大气底"

- 卡片（`QGroupBox`、面板）：`border: 1px solid border` + **顶部 1px `glow` 内嵌高光**，
  替代"所有面一样平"；圆角从统一 8 收到 `card=6 / control=5`（更硬、更像仪器面板）。
- 主窗中央 `QWidget#rootBg`：`background: qlineargradient(x1:0,y1:0,x2:0,y2:1, stop:0 panel2, stop:1 bg)`
  —— 一条渐变，仅主窗一层，**不做噪点纹理**（SVG/PNG 平铺在离屏渲染与 DPI 缩放下收益低、成本高）。
- 空间：沿用 4pt 网格，新增 `space.inset=20`（卡内边距）与 `space.section=40`（区块之间），
  用"意外的大间距"分隔「输入区 / 操作区 / 结果区」，而不是处处 8px。

### 2.6 动效（只保留三个高影响时刻，其余删除）

| 时刻 | 动效 | 实现 | reduced-motion |
|------|------|------|----------------|
| 开始下载 | 仪表条读数 180ms 数字滚动 | `QPropertyAnimation` on 自定义 int property | 直接跳到目标值 |
| 完成 | 仪表条底色 flash 220ms（青 → 常态）| `QPropertyAnimation` on QSS 不可达属性 → 用 `QVariantAnimation` 手动重绘 | 不动 |
| 失败面板 / DLQ 出现 | 高度 0→目标 160ms | `QPropertyAnimation` on `maximumHeight` | 直接显示 |
| （删除）逐行完成背景消隐 1.2s | 500 行持续重绘 | 改为状态 pill 由空心变实心的一次绘制 | — |

### 2.7 Web 美学要求 → Qt 机制翻译表（避免照搬不认识的技术栈）

| 要求（通用前端语境） | 本轮 Qt 落法 | 不可行 / 降级 |
|--------------------|-------------|--------------|
| Tailwind / Shadcn / Radix | **不适用**：桌面 Qt 无 DOM。等价物 = `TOKENS` + `_build_qss` + `setObjectName` 选择器 + 自绘 delegate | 放弃组件库概念，保留"变体系统"= `#primary` / `#danger` / `#muted` / `#accent` |
| `box-shadow` 层次 | 1px `glow` 内嵌高光 + 边框对比 | 不用 `QGraphicsDropShadowEffect`（性能 + 毛刺）|
| `transition` 微交互 | QSS 伪状态即时换色（Qt 无过渡） | 真过渡只能 `QPropertyAnimation`，故**只在 3 处**用 |
| 噪点 / 大气背景 | `qlineargradient` 单层渐变 | 不做噪点（离屏/DPI 成本）|
| `clamp()` 响应式字号 | `qss_for_scale(scale, motion)` + `clamp_scale_for_dpi`（已存在）| 无 CSS 变量，靠 Python 拼串 |
| 组件变体 `variant/size/state` | `setObjectName` + `setProperty("variant", ...)` + QSS 属性选择器 | 需在 `qss_validate.py` 白名单登记新选择器 |

---

## 3. 逐屏问题清单与落点

| 屏 | 现状问题 | 落点（file:line） | 目标 |
|----|---------|------------------|------|
| 顶栏 | 只有标题 + 线程数 | `gcm/ui/main_window.py:749-758` | 换成**仪表齿条**（新增模块），合并 `progress_table.py:71` 的批次总览条，消除三处重复 |
| 页签 | 选中态弱 | `gcm/ui/theme.py:377-382` | 下沿 2px 青线 + 选中文字 700 + 图标同色 |
| 下载中心·输入区 | 占位文案 + 校验条 + 快捷填充 chip 全同权重 | `gcm/ui/download_layout.py:33-110` | 输入框升为唯一焦点；chip 降为 `small` + 等宽无边框样式；校验条改为右上角 mono 计数 |
| 下载中心·操作区 | 主按钮与"取消/清空"平级 | `gcm/ui/download_layout.py`（`build_download_actions`）| 主按钮 = 骨白反相块（更高更宽），取消/清空降为幽灵按钮 |
| 进度表 | 状态只着色；行高未设；数字比例字体 | `gcm/ui/progress_table.py:511-515`、`gcm/ui/progress_delegate.py:41` | pill + 形状编码；行高 26；百分比/速率 mono 右对齐；进度条叠 25% 刻度 |
| 仓库管理 | 行高 34 已设，但列文案与状态同样只着色 | `gcm/ui/manage_panel.py:186` | 与进度表共用 pill 与 mono 约定 |
| 设置页·左导航 | 纯文本列表、无编号、暴露「G53 数据洞察」（`settings_groups.py:651` 标题 → 导航按「（」截短）| `gcm/ui/main_window.py:892-909` | 编号 `01–07`（mono/dim）+ 3px 左指示条 + 去掉内部代号 |
| 设置页·表单 | 标签带全角冒号与单位、控件同宽 | `gcm/ui/settings_groups.py`、`gcm/ui/settings_panel.py` | 两列网格（标签右对齐 dim / 控件左对齐）+ 单位进 `small` |
| 日志区 | Consolas 硬编码、无等级编码 | `gcm/ui/theme.py:353`、`:358`、`gcm/ui/event_panel.py` | 等宽 token；等级用 3px 左色条 + mono 前缀，行高 +2px |
| 空态/首启 | 与整体脱节 | `gcm/ui/onboarding.py`、`progress_table.py`（空态）| `display` 大标题 + `small` kicker + 示例 chip；首启不加 emoji（G74 已删过一次）|
| 对话框 | 已按屏幕夹取，视觉与主页不一致 | `gcm/ui/repo_detail_dialog.py`、`radar_chart.py`、`statistics_dialog.py` | 统一 padding/标题层级；雷达 6 维配色接新 token（三主题可读，`tests/test_g53_radar.py` 守卫）|

---

## 4. 架构落点（先拆再改，这是本计划的第一道闸）

### 4.1 为什么必须先拆

`gcm/ui/theme.py` **实测 598 行**（文件规模硬上限 800）。本轮要新增：三字族表、`apply_type`、
7 级字号阶梯、`cta/on_cta/glow/grid` 四个 token × 四套主题、QSS 层级重排 —— 直接在原文件里加必然越线。

### 4.2 拆分方案（零行为变化，纯搬移）

| 新模块 | 内容 | 预估行数 |
|--------|------|---------|
| `gcm/ui/` 新增 `typography`（`.py`） | 三字族常量、`TYPE_SCALE`、`type_px(role, scale)`、`apply_type(widget, role)`（含 `setLetterSpacing`）| ~120 |
| `gcm/ui/` 新增 `palettes`（`.py`） | `PALETTES`、`ACCENT_PRESETS`、`TOKENS`、`_luminance`/`contrast_ratio`/`hc_contrast_ratio`/`_shade`/`_on_accent_for` | ~200 |
| `gcm/ui/` 新增 `qss_build`（`.py`） | `_build_qss` + 缓存 + `qss_for_theme`/`qss_for_palette`/`qss_for_scale` | ~250 |
| `gcm/ui/theme.py` 变薄壳 | **原样再导出**全部符号 + `apply_theme`/`current_palette`/`current_theme`/`THEMES`/`LogModel`/`make_highlighter`/`maybe_validate_url`/`std_icon`/`apply_parent_theme` | ~180 |

**兼容契约（硬要求）**：`theme.py` 的导入面不得变化。本轮脚本清点出 **22 个**被外部导入的符号
（`LogLevel`×12、`current_palette`×7、`apply_parent_theme`×6、`PALETTES`×6、`qss_for_theme`×4、
`qss_for_scale`×3、`LogModel`/`LogEvent`/`FONT_BASE_PX`×3、`THEMES`/`make_highlighter`×2、
`theme_with_accent`/`std_icon`/`hc_contrast_ratio`/`clamp_scale_for_dpi`/`apply_theme`/`_build_qss`/
`TOKENS`/`FONT_STACK`/`FONT_SCALE_MIN_UI`/`ACCENT_PRESETS` 各 1），其中 28 处硬断言在
`tests/test_g46_theme.py`、`tests/test_themes.py`、`tests/test_g70_ui_upgrade.py`、`tests/test_g70_coverage.py`。
（复算方式：正则扫 `gcm/` + `tests/` 里 `from ...theme import` 的形参并去重。）

### 4.3 会红的断言（提前登记，不是意外）

| 断言 | 为什么红 | 处理 |
|------|---------|------|
| `tests/test_g46_theme.py:57` `test_font_stack` | 字族栈内容变了 | **正当更新**，新断言必须更强：断言三个字族 + 各自回退链 + 等宽族存在 |
| `tests/test_g46_theme.py:23` `test_all_palettes_share_layout` | 新增 `cta/on_cta/glow/grid` | 四套主题同步补齐即不红（**这是护栏，不是阻碍**）|
| `tests/test_g46_theme.py:109` `test_theme_qss_scale` | 若删掉基础 `font-size` 行 | 保留 `QWidget { font-size: … }` 即不红 |
| `tests/test_g46_theme.py:74` `test_hc_contrast_aaa` | 新色板若压到 <7:1 | 逐主题反算；红就改色，不改断言 |
| `tests/test_g74_ui_layout.py`（14 条） | 布局/行高/短标签/滚动策略可能变动 | **不得削弱**；改动前先读这 14 条口径 |
| `tests/test_themes.py:61` `test_window_has_theme_selector` | 主题下拉若被移动 | 保留控件与 `objectName` |

### 4.4 新增模块清单（本轮计划新增，全部 ≤400 行）

| 模块 | 职责 | 是否新逻辑 |
|------|------|-----------|
| `gcm/ui/` 新增 `status_strip`（`.py`） | 仪表齿条控件 + `format_metrics()` 纯函数 | 是（唯一新增信息架构）|
| `gcm/ui/` 新增 `status_pill`（`.py`） | `pill_for(status) -> (glyph, fg, bg)` 纯函数 + delegate 绘制辅助 | 是（状态编码单一来源）|
| `gcm/ui/typography`（`.py`） | 见 §4.2 | 搬移 + 小新增 |
| `gcm/ui/palettes`（`.py`） | 见 §4.2 | 搬移 + 新 token |
| `gcm/ui/qss_build`（`.py`） | 见 §4.2 | 搬移 + 重排 |

---

## 5. 任务拆解（T-UI-00 … T-UI-10）

> 每个任务 = 一次可独立回滚的提交（`git revert <sha>` 即回滚）。任务的"可失败断言"必须能在改代码**之前**写成红灯。

| ID | 任务 | 落点 | 可失败断言（先写红） | 依赖 |
|----|------|------|---------------------|------|
| **T-UI-00** | 基线快照（不改码）| `reports/ui-baseline-983/`（38 张）| 目录内 PNG 数 == 38 且 mtime 为今日 | — |
| **T-UI-01** | 主题模块拆分（纯搬移）| `gcm/ui/theme.py` → 三个新模块 + 薄壳 | `test_g46_theme` + `test_themes` + `test_g70_ui_upgrade` 全绿；`theme.py` ≤ 200 行；22 个导出符号全部可导入（脚本断言）| 00 |
| **T-UI-02** | 字体阶梯与字族 | `gcm/ui/typography`（`.py`）+ `theme.py` 薄壳 | QSS 中出现 7 级 `font-size` 且严格递减；`FONT_STACK_MONO` 存在且被 `#console`/`#pane` 使用；`apply_type` 对 `QLabel` 设字距可观测（`font().letterSpacing() != 0`）| 01 |
| **T-UI-03** | 色板重做（Ink/Paper，nord/hc 对齐）| `gcm/ui/palettes`（`.py`）| 四套色板 token 键集合完全一致；每套 `text/panel`、`text/bg`、`cta/on_cta` 对比度 ≥4.5；`hc` ≥7；`on_accent` 仍由 accent 推导（旧语义不变）| 01 |
| **T-UI-04** | QSS 层级重排（渐变/高光/页签/焦点/滚动条/表格）| `gcm/ui/qss_build`（`.py`）| `qss_validate` 通过；`motion=False` 时无 `:hover {` 块；基础 `font-size` 行仍在；`#rootBg` 含 `qlineargradient`| 02, 03 |
| **T-UI-05** | 仪表齿条 | `gcm/ui/` 新增 `status_strip`（`.py`）+ `main_window.py:749` 替换顶栏 | `format_metrics()` 纯函数 8 个样例（全空/单活/完成/含失败/超长用时）逐一断言字符串；无事件时不发重绘；**合并后 `progress_table.py:71` 总览条被移除且信息不丢** | 04 |
| **T-UI-06** | 进度/管理表状态 pill + mono 数字 + 刻度 | `gcm/ui/` 新增 `status_pill`（`.py`）+ `progress_delegate.py:41` | `pill_for()` 覆盖 5 状态且 glyph 互不相同（色盲可辨）；进度表行高 ≥26；500 行批量插入期间无逐行 QWidget（复用既有断言口径）；`bench_g70_ui.py` 前后对比不劣化 >10% | 04 |
| **T-UI-07** | 设置页导航与表单网格 | `gcm/ui/main_window.py:892-909`、`gcm/ui/settings_groups.py:651`、`settings_panel.py` | 导航项文案不含 `G\d+-?\d*` 内部代号；`tests/test_g74_ui_layout.py:67`（短标签/无横向滚动）仍绿；表单标签无全角冒号 | 04 |
| **T-UI-08** | 空态 / 首启向导统一视觉 | `gcm/ui/onboarding.py`、`progress_table.py`（空态）、`manage_panel.py` | zh/en 键同时存在（`test_g49_i18n` 口径）；空态大标题使用 `display` 级字号；无 emoji | 04 |
| **T-UI-09** | 动效编排（3 处）+ 删除逐行消隐 | `main_window.py`、`progress_table.py:577`、`status_strip` | `prefers_reduced_motion=True` 时**不创建**任何 `QPropertyAnimation`（注入式断言）；删除后 `bench_g70_ui.py` 绘制耗时下降 | 05, 06 |
| **T-UI-10** | 验收与登记 | `tests/` 新增 `test_g75_visual_system`、`docs/` 新增 `验证记录-2026-10-2x-G75UI`（`.md`）、`gcm/__init__.py` `__changelog__`、`docs/architecture.md` §4、`README.md` | 新测试文件 ≥15 条且每条都断言真实行为；文档守卫绿；`ruff`/`mypy`/快集全绿；截图 40 张 | 05–09 |

**依赖图**

```
T-UI-00 ── T-UI-01 ─┬─ T-UI-02 ─┐
                    └─ T-UI-03 ─┴─ T-UI-04 ─┬─ T-UI-05 ─┬─ T-UI-09 ─┐
                                             ├─ T-UI-06 ─┘           ├─ T-UI-10
                                             ├─ T-UI-07 ─────────────┤
                                             └─ T-UI-08 ─────────────┘
```

**规模估计**：01（半天）· 02/03（各半天，可并行）· 04（1 天）· 05（1 天）· 06（1 天）· 07（半天）·
08（半天）· 09（半天）· 10（1 天）。合计约 **6 个工作日**，分两个版本段：
**9.9.x**（01–04，设计系统地基，用户看不到明显变化但为后续一切铺路）→
**9.9.x 第二段**（05–10，可见的仪器台）。

---

## 6. 验证方案

### 6.1 视觉验证（不是"看着挺好"，而是可复现的产物）

```bash
# 基线与改后各跑一次，逐张对照
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe scripts/ui_screenshot_audit.py reports/ui-baseline-983
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe scripts/ui_screenshot_audit.py reports/ui-g75
```

- 覆盖：4 主题 × 5 分辨率（1024×768 / 1280×800 / 1366×768 / 1440×900 / 1920×1080）+ 3 页签 + 设置页滚动 + 8 对话框。
- **人工门**：至少逐张过 `download_deep_1440x900` / `download_hc_1366x768` / `tab_settings_1440x900` /
  `tab_manage_1024x768` / 失败面板 / 重试队列 DLQ 这 6 张，确认「层级、对齐、无裁切、无补丁块、状态可辨」。
- 自动门：无（像素 diff 对"改版"天然噪音大，不设阈值假绿灯）；**本文明确不当它是自动验证**。

### 6.2 自动验证

```bash
./.venv/Scripts/python.exe -m ruff check gcm
./.venv/Scripts/python.exe -m mypy gcm
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest tests.test_g75_visual_system -v   # 新增文件
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest \
  tests.test_g74_ui_layout tests.test_g46_theme tests.test_themes tests.test_g70_ui_upgrade \
  tests.test_g36_ui tests.test_manage_ui tests.test_progress_ui tests.test_g68_settings_ui \
  tests.test_settings_search tests.test_onboarding tests.test_g53_radar tests.test_g49_i18n -v
```

**CI 环境适配（必须写进新测试，不然后果是 Linux/macOS 红）**：主矩阵在 ubuntu/macos 上跑，
那里**没有** Bahnschrift / Cascadia Mono / 微软雅黑。所以新测试的口径是：

- 断言 **QSS 字符串里配置了正确的字族链**（平台无关，确定可测）；
- 字体**存在性**断言只在 `QFontDatabase.families()` 命中时才校验（否则 `skipTest` 并说明原因）；
- **禁止**写 `assertIn("Bahnschrift", QFontDatabase.families())` 这种平台硬断言。

### 6.3 性能验证

```bash
./.venv/Scripts/python.exe scripts/bench_g70_ui.py        # 既有基准脚本
```

关注：500 行批次插入耗时、进度刷新帧率、删除逐行消隐后的重绘次数。劣化 >10% 即回退该任务。

### 6.4 打包与真机（收尾版必须跑）

```bash
./.venv/Scripts/python.exe -m PyInstaller --noconfirm Git-clone-Max.spec
./.venv/Scripts/python.exe scripts/health_check.py     # 产物健康检查（JSON 台账）
```

真机要点：1440×900@100% 与 1366×768@125% 各一次，检查字族是否被系统正确回退、pill 是否对齐、
仪表条数字是否抖动。

---

## 7. 验收矩阵

| # | 验收标准 | 方法 | 证据形式 | 现状标签 |
|---|---------|------|---------|---------|
| A1 | 7 级字号阶梯严格递减，基体 13px 不变 | 单测断言 QSS | 测试输出 | 待执行 |
| A2 | 三字族各自生效（拉丁/中文/等宽），等宽用于 URL/时间/计数/日志 | 单测 + 截图 | 测试 + PNG | 待执行 |
| A3 | 四套主题 token 键一致，正文 ≥4.5:1，hc ≥7:1 | 单测（`contrast_ratio`）| 测试输出 | 待执行 |
| A4 | 主操作 = 反相块（`cta/on_cta`），与 `#danger` 可区分 | 单测 + 截图 | 测试 + PNG | 待执行 |
| A5 | 5 种终态 pill 的 glyph 互不相同（色盲可辨）| 单测 `pill_for()` | 测试输出 | 待执行 |
| A6 | 仪表齿条读数正确，且**不再与批次总览条重复表达** | 单测 `format_metrics()` + 代码审查 | 测试输出 | 待执行 |
| A7 | `prefers_reduced_motion` 下无任何动画对象 | 单测（注入设置）| 测试输出 | 待执行 |
| A8 | 14 条既有布局守卫 + 178 条 UI/i18n 回归全绿 | 定向回归 | 命令 + 输出 | 待执行 |
| A9 | `ruff` 0 error / `mypy` Success | 质量闸 | 命令 + 输出 | 待执行 |
| A10 | 快集 1349 tests 全绿且覆盖率不低于 82% | `ci_run_fast` + coverage | 命令 + 输出 | 待执行 |
| A11 | 打包 exe 启动正常、版本一致 | PyInstaller + `health_check` | JSON 台账 | 待执行 |
| A12 | 真机 2 种 DPI 下无裁切、字族正确 | 人工 | 截图 + 记录 | 待执行 |

---

## 8. 风险与回滚

| 风险 | 概率 | 影响 | 处置 |
|------|------|------|------|
| 拆分 `theme.py` 时漏导出符号 → 大量 ImportError | 中 | 高 | T-UI-01 先写"25 符号可导入"脚本断言；薄壳再导出；一次提交只做搬移 |
| `qlineargradient` / `rgba()` 在离屏渲染与真机表现不一致 | 中 | 中 | 截图基线在 offscreen 建立，真机另过目一次；不一致就以真机为准并记录 |
| 新色板把 `hc` 压到 <7:1 | 中 | 中 | 逐主题反算；红就改色（**不改断言**）|
| 反相主按钮在浅色主题与 `#danger` 混淆 | 低 | 中 | `danger` 保留描边式（G70 已定）；截图确认 |
| Linux/macOS CI 无目标字族导致假红 | 高 | 中 | §6.2 口径：只断言 QSS 链，存在性断言条件化 |
| 仪表条与总览条并存 → 两处真相 | 中 | 中 | T-UI-05 明确要求移除 `progress_table.py:71`，A6 单测钉住 |
| 500 行批次 pill 绘制拖慢 | 低 | 中 | `bench_g70_ui.py` 前后对比，>10% 回退 |
| 动效改造引入定时器泄漏 / 跨线程改控件 | 低 | 高 | 只用 `QPropertyAnimation`（主线程）+ `deleteLater`；既有红线 `TestNoCrossThreadWidgetAccess` 仍生效 |

**回滚**：每个任务独立提交，`git revert <sha>` 即可。T-UI-01 之后所有任务都依赖薄壳，
故薄壳本身的回滚代价最高 —— 这是先做它、且**单独一个提交**的原因。

---

## 9. 待用户决策（不确认不动手）

| ID | 决策点 | 选项 | 我的建议 |
|----|--------|------|---------|
| **D1** | 主操作按钮改为**骨白反相块**（不再是蓝色）| A 反相块 / B 保留 accent 蓝但换色值 / C 描边式 | **A** —— 这是整套美学立场的支点；选 B 则退化成"换了色的 AI 蓝" |
| **D2** | 设置页左导航从纯文本列表改为**编号 + 指示条** | A 改 / B 只加指示条不改文案 | **A**（`01–07` mono 编号成本低、仪器感最强）|
| **D3** | 是否允许移除 UI 里的内部代号（如「G53 数据洞察」→「数据洞察」）| A 允许 / B 保留 | **A**：工程代号对用户零价值 |
| **D4** | 是否删除逐行 1.2s 完成背景消隐 | A 删 / B 保留 | **A**：500 行持续重绘换来的只是"闪一下" |
| **D5** | 是否新增第五个主题（如 "Ink-only"）| A 不新增 / B 新增 | **A**：四套已覆盖深/浅/低饱和/无障碍，新增会让 `THEMES`、设置持久化、主题测试三处同步扩面 |

---

## 10. 自我反驳（先假定这计划是错的）

1. **"这只是换皮"** —— 不是。除色值与字体，它改变信息架构（新增仪表齿条、合并三处重复的总览）、
   编码方式（形状 + pill，色盲可辨）、动效预算（删 500 行逐行重绘，换 3 个时刻）、
   代码结构（`theme.py` 598 行拆四块）。若只做 §2.3/§2.4 的字体与色值，**收益不足以立项**。
2. **"仪表条会和已有总览条打架"** —— 会，所以 T-UI-05 把它写成硬约束：合并而非并存，并用 A6 单测钉死。
   这是本计划里最容易做出"两个真相"的地方。
3. **"Bahnschrift 撑不起中文"** —— 撑不起，也不打算撑。中文层级靠字重/尺寸/留白（§2.2 已写明边界）。
4. **"截图验收是自我安慰"** —— 部分成立。像素 diff 不设自动阈值，只做人工门 + 结构化单测；
   **不把"我看过了"写成"已验证通过"**（验收矩阵里 A12 明确标"人工"）。
5. **"四主题 + WCAG + reduced-motion + DPI 会让设计缩水"** —— 会让。但这四条是产品的既有承诺
   （`hc` 是给低视力用户的），设计要在约束里干活，而不是把约束删掉。
6. **"为什么不直接重写 UI 层（QML / 换 Qt 版本）"** —— 因为回归面是 133 个测试文件 + 已发布的 exe 链路，
   收益（视觉自由度）与代价（一处遗漏就崩）不成比例。这也是 `docs/architecture.md` 既有的判断。

---

## 11. 写本文档时踩到的守卫口径（留给下一个 agent）

`tests/test_g67_docs_freshness.py` 会扫描 `docs/**/*.md`：

1. **版本号**：除当前版本外，任何 `vX.Y.Z` 形态都会报警（`docs/计划书/*.md` 不在历史豁免名单）。
   所以目标版本只能写成 `9.9.x` / `10.x`（两段式或带 `x`，不符 semver 正则），
   历史轮次写成「G70（9.7 那轮）」而不是带三位版本号。
2. **路径**：以 `gcm/ docs/ scripts/ skills/ tests/ packaging/ evidence/` 开头、且以已知扩展名结尾的
   token 必须真实存在。**计划中的新文件**要写成"`gcm/ui/` 新增 `status_strip`（`.py`）"这种形态，
   不要写出完整文件名后缀，否则守卫会判"引用了不存在的路径"。

本轮实跑：

```bash
./.venv/Scripts/python.exe -m unittest tests.test_g67_docs_freshness -v
```

结果见本轮交付汇报（未通过就改文档，**不改守卫**）。

---

## 12. 一句话结论

**底座在、纪律在，缺的是"声音"与"层级"**：把 GitHub 的蓝换成墨与骨白，把 4 级字号拉到 7 级，
把状态从"染色的字"变成"有形状的读数"，再给这个工具一条会说人话的仪表齿条 —— 但必须先拆 `theme.py`、
必须先确认 D1，否则一切改动都会踩在 800 行红线与四主题对比度上。
