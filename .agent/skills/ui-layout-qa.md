# UI 布局 / 设计系统改动验收

> 适用：改 `gcm/ui/` 的布局、分割器、响应式档位、设计 token、触摸密度、设置页/管理页结构。
> 不适用：纯文案、纯后端、仅 QSS 色值微调（色值改动见 `docs/design-system.md` §10）。

## 1. 前置读取

1. `docs/design-system.md`（色板/token/断点/组件规则）。
2. `gcm/ui/layout.py` 与 `main_window._apply_layout_mode`。
3. `.agent/memory/known-issues.md` 与 `test_ledger.md` 里对应接缝（S52–S63）。
4. 当前 `git status --porcelain`：确认没有并行会话改同一批 UI 文件。

## 2. 先写失败测试

至少覆盖：

- 纯函数：断点/尺寸/比例换算；
- 真实离屏 `MainWindow`：控件 `visibleRegion == rect`、最小高度、隐藏/恢复；
- 持久化：重启/改高度后值仍正确；
- 反向自检：把修复回退，测试必须红。

常用：

```bash
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest \
  tests.test_rev13_adapt tests.test_ui_compact_visibility tests.test_g57_responsive \
  tests.test_rev14_adapt tests.test_rev14_feedback tests.test_rev14_keyboard \
  tests.test_r15_settings_workspace tests.test_r15_manage_columns tests.test_r15_touch
```

## 3. 实现约束

- 新 UI 逻辑落独立模块；不得再塞 `main_window.py`。
- 贴线模块先拆：`settings_panel` 746、`manage_panel` 712、`settings_groups` 790。
- 子控件尺寸用 `sizeHint/minimumSizeHint`，不要写死；被压缩时先压可滚动区。
- QSplitter：`setChildrenCollapsible(False)` + 只允许日志折叠；主任务区 `minimumHeight`。
- 列宽/比例保存：存比例不存绝对像素；跳过隐藏列、`stretchLastSection` 拉伸列、程序化下限。
- 触摸 44px 只在 `has_touchscreen()` 为真时生效；桌面保持 38/28。
- 颜色只读 `current_palette()`；状态用 `status_pill` 几何形状，不用字符。

## 4. 视觉与几何验收

```bash
QT_QPA_PLATFORM=offscreen PYTHONIOENCODING=utf-8 \
  ./.venv/Scripts/python.exe scripts/ui_screenshot_audit.py reports/ui-rev15-check
```

逐张看关键尺寸（至少 1440×900、1024×768、900×480），并断言：

- 主操作 `visibleRegion == rect`；
- 设置区/日志区比例符合预期，设置区不被拖到 0；
- 管理页 1024 宽不出现日期/HEAD 截断；
- 900 宽设置导航为下拉，不是消失。

## 5. 门禁

```bash
./.venv/Scripts/python.exe -m ruff check gcm
./.venv/Scripts/python.exe -m mypy gcm
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/doc_drift_check.py
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe scripts/i18n_coverage.py
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest tests.test_g79_repo_hygiene tests.test_g67_docs_freshness
```

改了布局/控件数后跑快集；改设置页/管理页主链路时加慢集或真实窗口冒烟。

## 6. 证据与回滚

- 写 `docs/验证记录-<日期>.md`：命令 + 真实输出 + 尺寸表 + 未验证项。
- 写 `evidence/<task>.json`；截图目录按 `.gitignore` 不入库。
- 回滚：新模块可整体移除；列宽/比例有旧值兼容；不得用 `git checkout <文件>` 冲掉同文件其它未提交改动。

## 7. 常见坑

- `setColumnHidden` 会发 `sectionResized(…, 0)`；只在同步段加抑制，400ms 后照样写盘。
- `stretchLastSection` 会把最后一列宽度改掉，不能当用户值保存。
- `coerce_int(0.72)` 会回退 0；比例值要用 float 解析。
- QSplitter 的 3:1 stretch 会在 resize 时覆盖用户拖出的比例。
- 设置区若允许 `childrenCollapsible`，用户拖过冲会把主任务区折成 0。
- `bump_version.py` 在 GBK stdout 打印 `✗` 会在回滚前抛异常；已统一 UTF-8。