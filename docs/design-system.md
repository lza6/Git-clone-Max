# Git-clone-Max 设计系统（Rev15 · 活文档）

> 这不是计划，是**当前实现的设计事实源**。改颜色/字号/间距/QSS 前先读这里；
> 代码与本文冲突时以代码为准，并在同一次提交里修本文。

## 1. 设计立场

- **仪器，不是模板**：近墨黑/暖纸底，骨白文字，唯一强调色只表达“正在发生”。
- **桌面优先**：目标是最小 900×480 逻辑像素窗口；不为不存在的手机端造断点。
- **系统字体**：Bahnschrift（显示/数字）、Microsoft YaHei UI（中文）、Cascadia Mono（数据/日志）。
- **无阴影**：Qt QSS 不支持 `box-shadow`；层次用 1px 边框 + 1px 顶边 `glow` 内嵌高光表达。
- **形状编码**：状态不只靠颜色；禁止 `✓ ✕ ⚠ ⊘` 等字符（微软雅黑缺字形会变 tofu）。

## 2. 颜色

四套主题，键集合必须完全一致（守卫 `tests/test_g75_visual_system.py`）：

| 主题 | 定位 | `bg` | `text` | `accent` |
|---|---|---|---|---|
| `deep`（默认） | Ink 墨黑 + 骨白 + 示波器青 | `#0b0e10` | `#e8e3da` | `#35c8b4` |
| `light` | Paper 暖纸 + 墨字 | `#f4f0e8` | `#16191b` | `#0f7c70` |
| `nord` | 北欧低饱和 | `#2e3440` | `#d8dee9` | `#88c0d0` |
| `hc` | 高对比 AAA | `#000000` | `#ffffff` | `#00e5ff` |

强调色 6 档：`teal` / `blue` / `violet` / `cyan` / `indigo` / `magenta`；深底取 `dark`、浅底取 `light`。
`theme_with_accent()` 会重算 `on_accent`；**任何色值改动都要重跑 4 主题 × 6 强调色 = 24 组对比度矩阵**
（`tests/test_g70_ui_upgrade.py` + `tests/test_g75_visual_system.py`）。

硬约束：

- 正文 / 弱文本 ≥ 4.5:1；输入边界 ≥ 3:1；`on_cta/cta` ≥ 4.5:1；`hc` 文本 ≥ 7:1。
- `accent` 只用于“正在发生”：进度 chunk、焦点环、运行中 pill；不用于静态装饰、页签选中、kicker。
- 每屏至多 1 个 `panel_primary` 卡面；普通分组用 `panel`。
- 自绘 delegate / 日志高亮 / 托盘 / 状态 pill 全部读 `theme.current_palette()`，禁止第二份色值。

## 3. Token

唯一来源：`gcm/ui/palettes.py::TOKENS`。每个叶子必须有生产消费点，或在
`DELIBERATELY_UNUSED` 明确登记；守卫 `tests/test_rev14_feedback.TestTokensHaveNoDeadEntries`。

| Token | 值 | 消费方 |
|---|---|---|
| `radius.card` | 6 | QGroupBox / 卡面 |
| `radius.control` | 5 | 输入框 / 按钮 |
| `radius.bar` | 5 | 进度条 / 齿条 |
| `radius.tab` | 8 | 页签容器 |
| `radius.checkbox` | 4 | 复选框 / 单选 |
| `spacing.xs` | 4 | 紧凑行内 |
| `spacing.sm` | 8 | 控件/表格间距 |
| `spacing.md` | 12 | 卡片内边距 |
| `spacing.lg` | 16 | 页面 gutter / 区块外边距 |
| `border.default` | 1 | 常规描边 |
| `border.focus` | 2 | 焦点环 |
| `row.default` | 26 | 主流表格行高 |
| `row.wide` | 28 | L 档表格行高 |
| `row.touch` | 34 | 触摸屏表格行高下限 |
| `row.manage` | 34 | 管理页表格行高 |
| `control.touch` | 44 | 触摸屏主操作最小高度（Rev15 R15-08） |
| `shadow.focus` | `"outline"` | 刻意不提供 QSS 消费点（Qt 无 box-shadow） |

**间距哲学**：4pt 网格；新/改组件只使用 4/8/12/16。现有代码里的 6/10/14 不批量替换 ——
只在被本轮触碰的组件里顺手收敛；需要 24/32 时先有真实消费点再加入，禁止“规格里有、代码里没人读”。

## 4. 排版

| 角色 | px @1.0 | 字重 | 字族 | 用途 |
|---|---:|---:|---|---|
| `display` | 28 | 600 | Bahnschrift | 批次结论大读数 |
| `h1` | 20 | 700 | Bahnschrift | 页面/对话框标题 |
| `metric` | 18 | 600 | Bahnschrift | 统计卡数值 |
| `h2` | 15 | 600 | 正文字族 | 分组标题 |
| `body` | 13 | 400 | Microsoft YaHei UI | 正文 |
| `mono` | 12 | 400 | Cascadia Mono | URL / 路径 / 时间 / 计数 |
| `small` | 11 | 500 | 正文字族 + 0.5px 字距 | 铭牌/辅助说明 |

- 基体 `FONT_BASE_PX = 13`；缩放 `font_scale ∈ [0.9, 1.6]`，DPI≥150% 时 `clamp_scale_for_dpi()` 抬到 ≥1.0。
- QSS 的全局 `QWidget { font-size }` 会覆盖 `apply_type()` 的 QFont；普通 QLabel 不要迷信 `apply_type()`，
  需要角色字号时用 `#pageTitle` / `#display` / `#metric` / `#kicker` / `#monoLabel` / `#emptyHint` 等 objectName。
- 不打包 ttf、不联网下载；缺字族按栈回退到 `sans-serif` / `monospace`。

## 5. 动效

唯一来源：`gcm/ui/motion.py`。

| Token | 值 | 用途 |
|---|---:|---|
| `BASE_MS` | 220 | 状态切换（进度条填充） |
| `PULSE_MS` | 180 | 终态脉冲 |
| `SLOW_MS` | 600 | 行完成消隐 |
| `FAST_MS` | 120 | 微反馈预留（当前无消费点） |

- 统一缓动 `OutCubic`。
- `prefers_reduced_motion=True` 时 QSS 去掉 hover/pressed 块，所有 `QPropertyAnimation` 跳过。
- 不做持续 shimmer；骨架屏静态占位。桌面工具不加 400ms 模态动画；要加也用 180ms。

## 6. 组件规则

| 组件 | 规则 |
|---|---|
| 主按钮 | 每屏最多 1 个 `#primary`；桌面最小 38px，触摸屏 44px |
| 次按钮 | `#secondary` 用于取消/暂停/有替代路径的动作 |
| 低频按钮 | `#ghost` 用于工具行、行内操作 |
| 危险按钮 | 只描边不填红底；破坏性 `QMessageBox` 默认按钮必须是 No |
| 分组卡 | 标题 h2 15px；`panel` 底；`radius.card`；内边距 10–12 |
| 表格 | 行 26/28/34；数字列 mono 右对齐；选中 `accent` 12%/18% alpha；hover `panel2` |
| 空态 | 只走 `build_empty_state`；过滤空态与真为空文案不同 |
| 对话框 | 最大 90% 宽 / 85% 高；最小 460×320；`apply_parent_theme` |
| 日志 | `Cascadia Mono` 12px；最多 4000 block；过滤只影响显示层 |
| 状态 pill | 7 种几何形状；颜色 + 形状双编码；不使用字符 |

## 7. 图标与插画

- 16 个语义 `QStyle.StandardPixmap` 映射；尺寸 16/20/24。
- 新增品牌图标用单色 `QPainter` 路径，颜色读 `current_palette()` 的 `text_dim`；不引入 SVG/PNG 资源包。
- 多 DPI 由 Qt `QIcon` 内建多尺寸承担，不做手工切图。

## 8. 主题实现

- 主题状态在 `gcm/ui/theme.py`：`_current_theme` / `_current_accent`；`current_palette()` 是唯一颜色来源。
- `theme="auto"` 由 `resolve_theme_name()` 解析：Qt `QStyleHints.colorScheme()` → 注册表兜底；
  设置里仍保存 `auto`，不写死成 light/deep。
- `QStyleHints.colorSchemeChanged` 只在 `auto` 时重刷；手动主题不响应系统变化。
- QSS 只由 `qss_build.py` 生成并缓存；不引入 CSS modules / CSS-in-JS / Tailwind。

## 9. 布局与断点

- 档位：XS（`w<1020` 或 `h<580`）/ S（`w<1100` 或 `h<820`）/ M / L（`w≥1440 且 h≥1000`）。
- 设置页：设置区 / 日志区默认 **72/28** 可调 QSplitter；日志区可拖到折叠。
- 窄窗（`<1000`）：设置分组导航由左侧列表改为顶部下拉；`900×480` 仍可定位分组。
- 管理页：`<1100` 隐「健康/大小」两列，宽度还给仓库名/路径；≥1100 恢复。
- 触摸屏：主操作 44px、表格行 34px；离屏测试平台不误判触摸屏。

## 10. 守卫与变更纪律

- 改色值：`tests/test_g70_ui_upgrade.py` + `tests/test_g75_visual_system.py` 必须绿；
  24 组对比度矩阵不能抽样。
- 改 token：`tests/test_rev14_feedback.TestTokensHaveNoDeadEntries` 必须绿；
  新增叶子必须同时有消费点。
- 改字号/字族：`tests/test_g46_theme.py` / `tests/test_rev11_visual.py` 必跑。
- 改布局：`tests/test_rev13_adapt.py` / `test_ui_compact_visibility.py` /
  `test_g57_responsive.py` / `test_r15_settings_workspace.py` 必跑。
- 改触摸：`tests/test_r15_touch.py`，并确认真机触摸屏（本机无触摸屏时标 `待验证`）。