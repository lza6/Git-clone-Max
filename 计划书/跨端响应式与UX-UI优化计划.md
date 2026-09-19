# 跨端响应式与 UX/UI 优化计划（Git-clone-Max · 2026-09-19）

> **现实前提（重要）**：当前主项目为 **PyQt6 桌面应用**（v8.0.1，M1–M7 已交付），**不存在 Web 前端**。
> 本计划把"响应式/性能/设计系统/UX-UI/架构"等目标**翻译到实际技术栈**（Qt 窗口/DPI/QSS/字体缩放等），
> 每一项都是真实可落地、可验收的；凡涉及"Web 版/SaaS 前端"的内容统一放在 **§6.4 远景（待定，未实现）**，
> 绝不虚构为已存在。输出遵循"先方案→TDD→落地→验证→闭环"总纲（见 `计划书/下一步改进指南.md`）。

---

## 1. 执行摘要（Executive Summary）

对 **PyQt6 桌面应用**做一轮"跨窗口尺寸/多 DPI/深色模式"的响应式与体验优化：以现有 design token（`gcm/ui/theme.py` TOKENS）+ 深浅主题 + 字体缩放为基础，
定义**窗口尺寸策略（320/768/1024/1440 语义档**，对应 Qt 可用几何区与布局重排**）、性能基线（启动 <3s、UI 事件 <100ms、动画 60fps、QSS 无卡顿）、
**设计系统补齐（间距 4px 基、圆角 4/6/8/12、运动 150/250/400ms、深色 token 规范）**、UX 模式库（反馈/导航/空态/权限/可访问性）、
以及**分阶段落地路线（MVP→打磨→优化）与全矩阵验收清单**。所有改动 TDD + 证据登记，禁止大重构；Web 版作为远景单列。目标用户：
**半技术小白用户（易用优先）+ 长期批量挂机用户（稳定性优先）**。

---

## 2. 响应式策略（Responsive Strategy）

> 语义：桌面窗口没有 CSS 断点，但有**等价维度**——窗口尺寸、屏幕 DPI/缩放、多屏、任务栏/安全区。

### 2.1 窗口尺寸语义档（替代"断点"）
| 档位 | 窗口宽度参考 | 触发行为（Qt） | 理由 |
|---|---|---|---|
| S（320–767） | 窄窗/小屏笔记本分屏 | 下载页工具栏按钮进"溢出菜单/图标化"；设置页单列；表格隐藏次要列（大小/健康 可折叠） | 防按钮挤压、保证可读 |
| M（768–1023） | 常规窗口 | 三列看板保留但压缩；统计中心双栏变单栏 | 平衡信息密度与留白 |
| D（1024–1439） | 默认窗口 | 全布局；批次总览条常显 | 主形态 |
| XL（1440+） | 大屏 | 表格列宽按比例拉伸；统计中心 4 象限铺开；工具栏可增"高级操作"入口 | 充分利用屏宽，避免右侧大片空白 |

- 实现机制：`MainWindow.resizeEvent` + 一次 `_apply_layout_for_width(w)`（集中分发，不散落）；首启记忆尺寸（已有 `settings.geometry`），
  **新档位判定**用 `window().width()`，不依赖像素级硬编码。
- **注意坑**：别在 resize 里重建控件（卡顿）——只切换可见性/布局边距；用 QTimer 防抖（150ms）合并连续 resize。

### 2.2 DPI / 缩放策略（对应"动态视口 & 设备像素"）
- 已启用 `Qt.HighDpiScaleFactorRoundingPolicy.PassThrough`；需验证 **100%–200% 缩放**下表格/图标不糊不裂（Qt6 默认 vector 控件 OK，但位图图标需 @2x，见 §4.4）。
- 字号联动：现有 `font_scale 0.9–1.6`（UI 钳制）+ `FONT_BASE_PX`；补一条规则：**DPI≥150% 时 font_scale 下限自动抬到 1.0**（防小字号不可读）。
- 目标：`settings_panel` 加"示例卡片"实时预览（改字号/主题即时看效果）。

### 2.3 安全区 / 任务栏 / 多屏（对应"notch & safe area"）
- 全窗口用 `QScreen.availableGeometry()`（排除任务栏）而不是 `screenGeometry()`；多屏时用**当前窗口所在屏**。
- 托盘菜单/对话框弹出位置以**主窗口所在屏**居中（多屏用户常见"弹到另一屏"问题）。
- `QFontMetrics` 提前测长文本，确保按钮文字在最小宽度不截断（加 `elideMode`）。

### 2.4 触控 / 键盘 / 手势（桌面亦有触屏与无障碍）
- 触摸 Min-48px：关键按钮 `setMinimumHeight(38–48)`（已有 start 按钮 38），补"触摸模式"下统一 44–48。
- 键盘：现有焦点环 + 管理表 Enter 打开详情；补齐 **Tab 全遍历可达（含托盘与统计对话框）** + Esc 关闭菜单后焦点还原（G46-12 已部分）。
- 触摸滚动：表格/日志区启用触摸滚动（QAbstractScrollArea 默认），验证两指缩放不误触。

---

## 3. 性能蓝图（Performance Blueprint）

### 3.1 目标（真实可测）
| 指标 | 当前基线（证据） | 目标 |
|---|---|---|
| 启动到可用 | 已有 `_startup_ms` 记录（>3s 提示） | **<2.5s**（含 UI 构建；冷启动 PyQt 加载） |
| UI 事件响应（等价 INP） | 日志/进度已 QTimer 批次心跳（100–250ms 合并） | **事件处理 <50ms**（批缓冲外无大块同步 IO 在主线程） |
| 动画 | 完成行淡出（QTimer 驱动） | **60fps**：避免 QSS 复杂渐变 + 避免 QGraphicsEffect |
| 内存/长挂机 | 已有 worker 引用收敛（G43-2 防 OOM） | 连续 10 万行日志不涨；progress.json 滚动治理 |

### 3.2 技术手段（Qt 对应）
- **懒加载**（已有）：管理表大小列后台线程 + 统计图按需构建；补：统计中心"首次打开才算 daily 聚合"，历史表"按需加载 200 行 + 滚动续载"。
- **避免主线程阻塞**：`repo_db` 写放大聚合类操作移入 `QThreadPool`/后台线程（统计、dir_size、导出）。
- **CSS 等同物**：Qt 无 CSS，但 QSS 选择器复杂度也会拖慢渲染——**避免通配符 `*` 与深层后代选择器**；复杂层级用对象名/属性选择器限定。
- **GPU 合成**：QSS 纯色/圆角由 Qt 光栅化；动画优先 QPropertyAnimation（走合成）而非逐帧定时器贴图。
- **离线/降级**（天然支持）：git 本地克隆/增量**完全离线可用**；补：断网时网络诊断给出明确"离线"状态（G48-8 已有分级，补"离线检测"提示），弱网自动降级已有。

### 3.3 评估方法
- 启动耗时：启动日志 `_startup_ms`（已有自检）——纳入 CI 门禁日志断言（<2500ms）。
- 事件响应：`QElapsedTimer` 采样（发布版默认关，`--profile` 开）。
- 动画：手工 + 录屏；Rendering 用 `QSG_*` 不做强制（QtWidgets 光栅）。

---

## 4. 设计系统规范（Design System Specification）

> 基于现有 `gcm/ui/theme.py` TOKENS 扩展，**不推翻**。

### 4.1 Token 架构（补齐）
| 类型 | 现状 | 补齐项 |
|---|---|---|
| 颜色 | PALETTES（deep/light/nord/hc）、ACCENT_PRESETS(3) | 补**语义色别名**（bg/panel/text/accent/danger/success/warning 已近完备）→ 增 `hover/disabled/pressed` 全控件态一致映射 |
| 间距 | TOKENS["spacing"] xs4/sm6/md8/lg12/xl18 | **规范化 4px 基**：xs4 sm8 md12 lg16 xl24（统一，避免 6/10/14 混用） |
| 圆角 | radius card8 ctl6 bar5 tab8 chip4 | 规范为 **4/6/8/12 四档**（chip4 bar6 control6 card8 dialog12） |
| 边框 | border default1 focus2 | 加 shadow token（QSS 用 outline/border 表达，不引 box-shadow） |
| 运动 | 完成动效 1.2s、hover 过渡 | 运动时长表：**150ms(hover/焦点) / 250ms(状态切换) / 400ms(对话框/托盘弹出)**，缓动 ease-out；reduced-motion 清空动画（已有） |

### 4.2 深浅色
- 深色 token 规则：面板 `#1b1f27` 系、文字 `#e6e8eb` 系、强调保持 4.5:1 以上、**成功/失败/警告在深色下对比度 ≥4.5:1**（补齐断言，参照 HC 主题的 `hc_contrast_ratio`）。
- 跟随系统深浅（auto）已有；补：**切换瞬间无闪烁**（先应用 token 再重绘）。

### 4.3 排版
- 字体栈已有（微软雅黑等）；补**字号阶梯**：body 13/14、h1 20、h2 16、caption 12（与 font_scale 联动）；行高 1.4–1.6。

### 4.4 图标
- 现有 `_std_icon` 语义图标；补 **@2x 资源**（高 DPI 下不糊）；图标尺寸规范 16/20/24；与文字间距 8px。

### 4.5 组件一致性规则
- 主按钮高 38、次按钮 32、图标按钮 28–32；输入框高 32–36；统一 focus 环 2px accent。
- 对话库（QMessageBox/自定义）统一样式与间距。

---

## 5. UX/UI 模式库计划（UX/UI Pattern Library Plan）

| 模式 | 现状 | 增强（含验收） |
|---|---|---|
| 层级/留白 | 部分 | 三 Tab 主次分明；下载区用间距分组（spacing 应用后截图对比） |
| 反馈/骨架 | 空态 overlay 已有、进度条已有 | 管理表"加载中"骨架行；导出/归档按钮 loading 态（禁用+文案"处理中…"） |
| 微交互 | 完成行淡出 | 按钮按下 100ms 反馈（QSS pressed 已有）；列表 hover 高亮（已有） |
| 导航/回退 | Tab+托盘+工具栏 | Tag/仓库详情/统计 增加"返回上一层"语义（面包屑文本）；托盘双击显示主窗已有 |
| 可访问性 | 焦点环 + QAccessible 部分 | 目标 **WCAG 2.1 AA**：关键控件 `setAccessibleName`/`setToolTip` 补齐（G46-6 已系统化一部分）；HC 主题对比度 ≥7:1 断言；焦点顺序表格可达 |
| 表单/输入 | QLineEdit 掩码/占位已有 | inline 校验：下载目录不存在/非法路径 → 输入框边红 + 说明文字（不再只弹窗）；导入清单非法行内联标红 |
| 动效 | reduced-motion 开关已有 | 动画统一走 token（150/250/400ms）；开关开后零动画断言 |
| 空态/错误/权限 | 空态 overlay、敏感目录警告 | 权限失败（无写入权限）→ 内联故障页；redact 后错误信息带"可操作建议" |

**无障碍清单（AA 必查）**：对比度（正文 ≥4.5:1、大字 ≥3:1、HC≥7）、焦点可见、键盘全遍历、可访问名、消息可读（不做纯颜色传达，颜色+符号双通道 G46 已落地）。

---

## 6. 技术架构（Technical Architecture）

### 6.1 现状
PyQt6 + gcm/{app,db,git,ui,i18n,util}；引擎(engine)+服务(service)+UI(main_window) 分层已清晰；QSS 由 theme.py token 工厂生成。

### 6.2 结构性增强（小步，不重构）
- **UI 拆件**：把 `settings_panel.py`(37KB)/`main_window.py`(66KB) 中**新增**的复杂面板继续按 G45-6 模式抽取（仅当新增功能时顺带，不做纯拆件大改）。
- **主题工厂**：`qss_for_palette` 保持单一入口；token 变更仅改 theme.py，UI 不散落魔法值。
- **目录/文件夹**：新增功能模块放 `gcm/ui/<feature>.py` + `tests/test_<feature>.py`；文档/计划统一进 `计划书/`。

### 6.3 测试策略（响应式等价验收）
- 离屏单测：`QT_QPA_PLATFORM=offscreen` 下用 `resize(width,height)` 矩阵驱动 `_apply_layout_for_width`，断言各档位控件可见性/几何。
- 截图对比：`offscreen grab` 输出 S/M/D/XL × 主题 × 缩放 矩阵图（已有 `docs/screenshots/` 先例）。
- 真实 E2E：`scripts/e2e_smoke.py`（裸仓 clone/update/conflict）+ 启动器实测（`启动Git-clone-Max.bat`）。
- CI：三平台矩阵 + mypy（已入）；覆盖门禁 ≥84。

### 6.4 远景：Web/SaaS 前端（待定，未实现——仅预留方向）
若未来做 Web 版（如跨设备管理后台/浏览器使用），届时按模板补：CSS 断点 320/768/1024/1440（移动优先）、CWV（LCP<2.5s/INP<100ms/CLS<0.1）、srcset/Service Worker/代码分割、设计 token 用 CSS 变量双主题。**本轮不实施、不虚构**。

---

## 7. 分阶段落地（Phased Rollout Plan）

### MVP（先做，1–2 周，等你确认）
1. **W1-1** 窗口尺寸语义档 `_apply_layout_for_width`（S/M/D/XL + 防抖）+ 离屏矩阵测试 + 截图。
2. **W1-2** 深色模式对比度补齐（成功/失败/警告 ≥4.5:1 断言）+ 高 DPI 150% 字号下限。
3. **W1-3** 反馈补齐：导出/归档 loading 态、管理表骨架、目录非法 inline 校验。

### Polish（打磨）
4. W2-1 运动 token（150/250/400ms）统一 + 键盘/焦点全遍历。
5. W2-2 图标 @2x 与尺寸规范；统计中心首次打开懒加载。

### Optimize（优化）
6. W3-1 启动 <2.5s 门禁（`_startup_ms` 入 CI 断言）；日志 10 万行不涨内存。
7. W3-2 profile 采样工具（`--profile`）与慢路径报告。

---

## 8. 质量清单（Quality Checklist · 发布前逐项勾选）

- [ ] 窗口尺寸矩阵：320/768/1024/1440 → 各档布局正常、无控件遮挡/截断（难截图登记）
- [ ] DPI 矩阵：100/125/150/200% → 无模糊图标、无小字号不可读
- [ ] 主题矩阵：deep/light/nord/hc/auto × 各档 → 对比度断言全部通过
- [ ] 键盘：Tab 全遍历 + Enter/Esc 语义 + 焦点环可见
- [ ] 触摸：关键按钮 ≥44px、滚动顺滑
- [ ] 性能：启动 <2.5s、动画流畅（录屏）、长挂机内存稳定
- [ ] 反馈：loading/骨架/空态/错误/权限五态齐全
- [ ] 回归：全量测试绿（≥761）、覆盖 ≥84、ruff 0、mypy 0、真实 git E2E PASS、health_check EXIT=0
- [ ] 清理：无临时文件/旧产物残留（`计划书/下一步改进指南.md` §6 纪律）

---

## 9. 常见坑与规避

1. **resize 里重建控件 → 卡顿**：只改可见性/布局，QTimer 防抖合并。
2. **QSS 花哨 → 渲染慢**：禁 `*` 通配与深后代选择器；动画走 QPropertyAnimation。
3. **高 DPI 位图糊**：图标给 @2x；字号下限随 DPI 抬升。
4. **深浅色闪烁**：先换 token 再统一重绘，勿逐控件改。
5. **把 Web 模板硬套桌面**：所有"断点/CWV/service worker"仅作语义映射，落地以 Qt 验证为准。
