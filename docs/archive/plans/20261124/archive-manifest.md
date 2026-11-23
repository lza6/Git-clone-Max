# 2026-11-24 Rev20 计划文档归档清单

## 1. 归档范围

| 原路径 | 归档路径 | 文档类型 | 任务总数 | 已闭环 | 已过时/不适用 | 仍开放 |
|---|---|---|---:|---:|---:|---|
| `计划书/下一步改进指南.md` | `docs/archive/plans/20261124/下一步改进指南.md` | 施工图 / 改进指南 | 11 | 11 | 0 | 0 |
| `计划书/前端界面设计计划指南.md` | `docs/archive/plans/20261124/前端界面设计计划指南.md` | 设计计划 / 执行手册 | 5 | 4 | 0 | 1（C5 真机触摸屏） |

## 2. 逐条审计结论

Rev20 主指南：P0 发布收尾在开工前复核发现已在 v9.21.1 完成；R20-1~R20-7 均在本轮真实落地并有测试/门禁/发版证据；V 收口已完成（v9.22.0 发布，CI 全绿，Release 双资产 sha256 一致）。指南开头“v9.21.1 尚未发布”的描述是开工时点快照，实际状态以提交与 Release 为准。

设计指南：C1（FAST_MS 微反馈）、C3（截图审计纳入发版单）、C4（pill 语义表）此前已闭环；C2 是持续纪律，已体现在 design-system；C5（真机触摸屏 44px/34px）本机无触摸屏，保留为待验证，不因归档标完成。§9 自检清单是流程模板，不是未完成任务。

## 3. 证据

- Rev20 验证记录：`docs/验证记录-2026-11-24-Rev20.md`
- 变更日志：`gcm/_changelog_data.py` 9.22.0 条目；碎片归档于 `changelog.d/_archive/9.22.0/`
- Release：https://github.com/lza6/Git-clone-Max/releases/tag/v9.22.0
- 真机复验：双屏 2560×1600@150% + 1920×1080@100%，主窗跨屏后无裁切；后台任务面板新按钮可见可达；无触摸屏，C5 维持待验证
- 四轨验收：UI 截图 34 张（`reports/ui-v9220/`，像素比对无回归）、realcalls PASS、e2e_smoke PASS、契约/事件 66 例 PASS

## 4. 仍开放项去处

- 多显示器完整真机项：beads `13k`（本轮已实测双屏跨屏与 DPI 差异，拔屏/贴靠仍未实测）
- 撤销链路真人点击、深浅色与读屏器：beads `cuz`
- 真机触摸屏：并入 `cuz` 的硬件窗口，不因文档删除标完成

## 5. 删除清单与恢复

| 路径 | 是否可删除 | 恢复方式 |
|---|---|---|
| `计划书/下一步改进指南.md` | 是（已归档） | `git show HEAD:docs/archive/plans/20261124/下一步改进指南.md`（提交后） |
| `计划书/前端界面设计计划指南.md` | 是（已归档） | 同上 |


## 6. 本次命令

- `mv 计划书/*.md docs/archive/plans/20261124/ && rmdir 计划书`
- `QT_QPA_PLATFORM=offscreen python scripts/ui_screenshot_audit.py reports/ui-v9220` → 34 张
- `python scripts/pixel_diff.py reports/ui-v9200-audit2 reports/ui-v9220` → `ok: True`
- `python scripts/realcalls_rev17.py` → `VERDICT: PASS`
- `python scripts/e2e_smoke.py` → `E2E_SMOKE_RESULT: PASS`
