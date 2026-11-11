# 关窗与删除确认默认按钮改为 No
运行中关窗、删除库记录、删除半成品目录三处 QMessageBox.question 显式传 defaultButton=No；Esc 与回车都不会误触不可逆动作。
@verify tests.test_r15_destructive_defaults 2 例 OK
