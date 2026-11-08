# Rev11-C 悬空项收口：结论条就绪态 / 空态清零 / 按钮权重 / 事件流中文化 / i18n 批次 3

- **R-01 结论条就绪态**（三份文档反复登记、悬空 4 轮）：点「开始」后、引擎起跑前，批次结论条
  先展示「N 将克隆 · M 将更新 · P 受保护 · Q 被拦截」，数据来自与 `--dry-run` /
  预览差异表同一份 `plan_batch`。顺带删掉 `main_window` 里指向不存在方法
  `_conclusion_from_overview` 的假注释。守卫 `tests/test_r01_ready_wiring.py`（4 例）。
- **R-05 空态清零**：失败面板 / 命令面板 / 设置导航 / 撤销浮条四处裸 `QLabel` 全部改走
  `empty_state.build_empty_state`（唯一来源），全库复扫 0 命中。
- **R-06 大小列三态**：管理页「大小」列空值由空串改为 `—`，与既有 ToolTip 解释配套。
- **P-01 按钮权重**：QSS 新增 `#secondary` / `#ghost` 两档（配合既有 `#primary` / `#danger`），
  管理页 9 个动作按钮按语义赋权；映射落在新模块 `ui/button_weights.py`（`manage_panel.py` 保持 799 行）。
- **P-03 事件流中文化**：事件表「类型」「状态」由协议枚举（`task_result` / `success`）改为
  i18n key，中文用户看到「任务结果 / 成功」；序号列右对齐。
- **P-04 失败面板排版**：失败次数列右对齐 + mono 字体（散文列保持正文字体）。
- **R14-18 i18n 批次 3**：`main_window` / `repo_detail_dialog` / `undo` / `help_dialog` /
  `manage_panel` / `menu_bar` 的中文常量批量走 `tr()`，棘轮基线 **1288 → 1128**（实迁 160 条）；
  英文模式真窗扫描零中文残留。

@verify `python -m unittest tests.test_r01_ready_wiring tests.test_rev12_usability tests.test_rev11_visual tests.test_i18n_coverage`（全绿）+ `python scripts/i18n_coverage.py`（I18N OK）+ 4 张 UI 截图（reports/ui-s012、ui-s013）+ 真窗 EN 扫描 0 残留
