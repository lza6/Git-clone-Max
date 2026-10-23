# 归档清单 — G75 UI 视觉系统重构（2026-10-23）

> 归档对象：`UI视觉系统重构指南.md`（原在 `docs/计划书/`）。
> 归档条件（`docs/计划书/README.md` §1）：条目全部闭环并逐条复验 → 写本清单 → 才可移出计划目录。
> **本清单只记事实与证据；标 `partial` / `未执行` 的条目不得读成"已完成"。**

## 1. 逐条状态

| 任务 | 计划内容 | 结论 | 证据（可复现） |
|------|---------|------|---------------|
| T-UI-00 | 基线截图 38 张 | **done** | `reports/ui-baseline-983/`（38 PNG，2026-10-23 生成，基线 `0018628`） |
| T-UI-01 | 拆 `theme.py`（零行为变化） | **done** | `theme.py` 598→314 行；新增 `gcm/ui/typography.py`(107) `gcm/ui/palettes.py`(211) `gcm/ui/qss_build.py`(262)；22 个再导出符号由 `tests/test_g46_theme.py`/`test_themes.py`/`test_g70_ui_upgrade.py` 覆盖 |
| T-UI-02 | 七级字号阶梯 + 三字族 | **done** | `TYPE_SCALE` 7 级（28/20/18/15/13/12/11）；`type_font`/`apply_type` 有生产调用点（`progress_delegate.py`、`status_pill.py`、`status_strip.py` 铭牌）；守卫 `test_g75_visual_system.TestTypeScale`(7) + `TestFontFamilies`(4) |
| T-UI-03 | 色板重制（Ink/Paper，nord/hc 对齐） | **done** | `palettes.py`；新增 `cta`/`on_cta`/`glow`；逐主题对比度断言 `TestPaletteContrast`(7)；顺带修复 nord success 4.23:1→4.70:1（历史 AA 失败） |
| T-UI-04 | QSS 层级重排 | **done** | `qss_build.py`；`QWidget#rootBg` 渐变、GroupBox `border-top` 内嵌高光、页签选中态、幽灵次级按钮、`QToolTip`；4 主题 × 3 缩放 × 2 动效 = 24 组 `qss_validate` 全绿 |
| T-UI-05 | 仪表齿条 | **done（口径修订）** | `gcm/ui/status_strip.py`(224) + `main_window._build_ui` 接线 + `_launch` 起表 + `progress_table` 活跃/终态/收尾钩子；**未合并 `batch_total_lbl`**（见 §2 D6） |
| T-UI-06 | 状态 pill + 等宽数字 + 刻度 | **done（口径修订）** | `gcm/ui/status_pill.py`(162)：7 种形状（QPainter 几何，与字形无关）+ `StatusPillDelegate`（只绘制不改文本）；进度表行高 26；**未做"25% 刻度"**（见 §2 P3-1） |
| T-UI-07 | 设置导航与表单 | **done（部分）** | 导航 `01–07` 编号 + 左指示条（QSS）+ 去掉「G53」工程代号（`settings_groups.py`）；**表单标签的全角冒号未改**（见 §2 P3-2） |
| T-UI-08 | 空态/首启统一视觉 | **partial** | 空态改 `#emptyHint`（h2 字号）；首启向导沿用 `#pageTitle`（已随排版自动升级）；未做「示例 chip / kicker」细化 |
| T-UI-09 | 动效编排 | **partial** | 齿条起表/停表逻辑完成；**未做** 3 处 QPropertyAnimation 编排，**未删除**逐行消隐动效（见 §2 D4） |
| T-UI-10 | 验收与登记 | **done** | `tests/test_g75_visual_system.py`(43)；`docs/验证记录-2026-10-23-G75UI.md`；`__changelog__` 9.9.0 一行；`docs/architecture.md` §4；`README.md`；`CLAUDE.md` §6 |

## 2. 计划与现实的偏离（必须留痕，不得静默）

| ID | 偏离 | 原因（实测） | 处置 |
|----|------|-------------|------|
| **D6** | 齿条**没有**合并批次总览条 | `batch_total_lbl` 的文本被 `tests/test_g46_nav_batch.py:59` 与本窗口托盘逻辑复用；且两者语义不同（"这一批跑完没有" vs "进程现在在干什么"），合并会丢信息 | 改为职责划分：总览条抽成 `gcm/ui/batch_overview.py`，齿条只管运行态；两个 docstring 互相写清边界 |
| **D4** | **没有**删除逐行 1.2s 完成消隐 | `tests/test_g36_ui.py:162` 直接测该动效，且它是用户可见设置项 `ck_animations`（删=功能回退） | 保留原样；如需删除须用户明确决策 |
| **P3-1** | 进度条未叠 25% 刻度 | 属装饰性增强，对"读数"无信息增益；先保证 pill/等宽数字落地 | 记入 `下一步改进指南.md` 待办 |
| **P3-2** | 表单标签仍带全角冒号/单位 | 标签由 40+ 处 `_add_*` 内联字符串构成，改动面大且易与 `test_g68_settings_ui` 的标题断言冲突 | 记入待办 |

## 3. 本轮新增的洞察（已写进 `CLAUDE.md` §6 / `.agent/memory/change-index.md`）

1. **颜色必须单一来源**：`_themed_qss` 应用主动预设，但 `current_palette()` 未应用 → 批次条旧蓝、行内条新青。
   修法：`apply_accent_preset()` 把预设写进模块状态。
2. **状态不得用字符符号**（G74 tofu 教训的推论）：改 `QPainter` 几何形状。
3. **强调色预设必须深/浅成对**：单一十六进制无法同时满足深底够亮、浅底够暗（浅色主题焦点环曾仅约 2:1）。
4. **CI 在 Linux/macOS 没有目标字族**：字体存在性断言必须条件化。

## 4. 证据索引

- 截图对照：`reports/ui-baseline-983/`（改前 38 张）↔ `reports/ui-g75/`（改后 38 张）
- 测试：`tests/test_g75_visual_system.py`（43 项）
- 构建与运行：`docs/验证记录-2026-10-23-G75UI.md`
- 台账：`evidence/G75-ui.json`
