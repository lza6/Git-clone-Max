# 矮窗口把工具行折叠进「更多 ▸」，字号下限收敛到 0.9，窄窗列折叠真正生效
900×480 / 1366×768@150%(911×512) 下四个工具按钮原本只有 27/42px 可见；现在按 layout.tools_fold_height() 折叠进菜单，入口与槽不变
zoom_out 改用 typography.FONT_SCALE_MIN_UI（0.9），此前 Ctrl+- 可压到 0.8 且全库零强制
main_window._apply_layout_for_width 原用 QTableView.horizontalHeaderItem()（不存在）+ 匹配不存在的「标签」列 + 阈值 768 不可达 → 死分支；改为 model().headerData + NARROW_WIDTH=1020 + 只折叠 大小/健康
@verify tests/test_rev13_adapt(6) + test_ui_compact_visibility(3) + test_g57_responsive(8) + test_font_scale + test_g80_hygiene
