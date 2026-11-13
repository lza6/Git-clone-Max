核对无误项先行一句：busy 异常路径正确（异常在 with 体内抛出时 `__exit__` 先恢复按钮、再进 except 显示错误）；replace 默认不勾、全不选禁用、Enter/Esc 分支均无问题；manage_panel WARN 与 diag_dialog 迁移无发现。

中|download_tools.py `open_batch_preview` 尾部（新增块即文件末行）|按钮「执行勾选行」与对话框 docstring（“剔除…再 launch()”）都承诺执行，实现 accept 后仅 `setPlainText` 回填输入框即 return，两条分支均无 launch 调用（注释自证“用户直接点开始”）；按钮契约与实现相悖|二选一：accept 后直接调 owner 启动入口；或按钮/文档改为「应用勾选」语义并同步修 docstring

中|download_tools.py `if _urls & skip: continue`|剔除以整行为单位：一行含多个 URL 且任一未勾选时整行丢弃，同行已勾选 URL 被连带剔除且无提示；`parse_urls` 每行返回列表说明多 URL 行是受支持输入|交集命中时按 URL 粒度重写该行仅保留已勾选项，或回填时强制一行一 URL

中|download_tools.py `skip = dlg.unchecked_executable_urls()` vs `{s.url_https for s in _s}`|集合交两侧出处不同：skip 用 `PlanRow.url` 原值，行侧用 `parse_urls` 归一化的 `url_https`；ssh/.git/尾斜杠/大小写任一归一化差异即交集为空 → 未勾选行静默残留并照常执行（漏剔恰为本功能要防的事故）|加 ssh/https/.git 变体的剔除行为测试锚定等价性，或改传行索引/`selected_rows` 消除二次字符串匹配

中|progress_table.py:572-577|背景声称本批含「清 _detail_batch+DETAIL_ROLE」，但该文件全 diff 仅一个 hunk：只有 `_progress_batch.pop` 与置 `-1`，无任何 detail 侧清理；若批内 detail 更新在终态后 flush，状态文本仍会被覆盖（修复不完整或描述失实，二者必居其一）|在同一终态分支补 `_detail_batch`/DETAIL_ROLE 清理；若已在更早提交完成，在验证记录指认位置以便核验

中|gcm/i18n/zh.py、en.py（git 状态显示已修改，但 diff 未附）|新增 key 至少 10 个：执行 / 执行勾选行 / 已跳过（预览未勾选）：{u} / 成功 / 失败 / 已取消 / 冲突 / 已跳过 / 运行中 / 等待中（diag 的「导出诊断包」若非既有 key 一并计）|核对两字典同步收录；tr 缺失回退中文不致崩，但英文界面会出现中文残留

低|batch_preview_dialog.py `refresh()` 末尾|`itemChanged.connect` 位于 refresh() 内而非 `__init__`，与取舍①「已移入 __init__」的描述不符；当前仅 `__init__` 调一次故等价，但 refresh 无清表步骤且 `selected_rows`/`unchecked_executable_urls` 按 `plan.rows[i]` 表行索引直取——二次调用会连接堆积、行翻倍、勾选映射错位|connect 移回 `__init__`，refresh 先 `setRowCount(0)`，或注释明确 refresh 单次调用契约

低|event_panel.py `_summary`|status 已中文化但 action 仅排除 `cloned`，其余动作值仍以英文原值入摘要（“成功 · updated · 更新完成”），且 cloned+空 message 时丢失动作信息|动作同样走映射表，或在 message 已表达动作时一并省略

低|batch_preview_dialog.py `selected_rows()`|新公开方法在 diff 内零生产调用点（调用方实走 `unchecked_executable_urls`+文本重写），违反「新增函数必须有生产调用点」约定（G69-18 守卫范围）|删除，或让剔除链路改用它（与发现3联动消除字符串匹配）

低|download_tools.py `BatchPreviewDialog(..., parent=owner)`|exec 返回后 wrapper 出作用域，父对象持有使 C++ 侧对话框（含全部 item）留存累积；旧代码同病灶，本 diff 重构了该行但未加 `WA_DeleteOnClose`|`setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)` 或 exec 后 `deleteLater`

低|download_tools.py skip 路径|`owner.repo_input` / `owner._emit_log` / `LogLevel` 的属性契约未在 diff 内证实，首次走 skip 分支若名称不符即 AttributeError|与发现3共用一条「勾选→剔除」行为测试一并覆盖

VERDICT: FAIL — 核心新功能的剔除正确性存在两个未证实接缝（错剔/漏剔均可静默发生）、确认按钮契约与实现相悖、声称的竞态修复 detail 侧在 diff 中缺席；均为合入前须处理项，非风格挑剔。
