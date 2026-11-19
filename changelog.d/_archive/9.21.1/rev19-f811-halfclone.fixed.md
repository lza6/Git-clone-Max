# 修复 halfclone_actions 重复导入导致的 ruff F811
repair_halfcloned_row 内重复 import 与模块级别名冲突；删除函数内导入，恢复 ruff 全绿。
@verify ruff check gcm 0 error；g85/p11/g52 相关 33 例回归绿
