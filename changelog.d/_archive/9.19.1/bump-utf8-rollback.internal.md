# 版本 bump 在 GBK 控制台下也能回滚
bump_version 打印 ✗/✓ 时 GBK stdout 会抛 UnicodeEncodeError，且发生在回滚前，把版本停在半改状态；改为 UTF-8 输出，回滚测试无需 PYTHONIOENCODING 也通过。
@verify test_g84_bump_version 9 例 OK（未设 PYTHONIOENCODING）
