# 无障碍审计排除 Qt 内部子控件
QSpinBox 内部编辑框、QLineEdit 清除按钮、菜单栏扩展按钮不再计入缺名；ManagePanel 搜索框补 accessibleName，真实基线归零。
@verify a11y_audit missing_total=0；tests.test_r15_a11y_truth 4 例 OK
