# 选择「跟随系统」主题后立即按系统深浅色生效
设置页选 auto 时先把 auto 解析成 light/deep 再应用，并保持 settings.theme=auto；监听 QStyleHints.colorSchemeChanged，系统换色后自动重刷。
@verify tests.test_r15_auto_theme + tests.test_g36_ui 18 例 OK
