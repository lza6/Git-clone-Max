# 守卫自身按审查意见加固（不让它们静默变绿）
doc_drift_check：①窄编码终端（cp936）下 ✓/✗ 会 UnicodeEncodeError —— 而脚本唯一用途就是在有漂移时打印这些行，现 reconfigure 为 utf-8；②basename 有歧义（如 __init__.py 多处同名）时原为静默跳过，现直接报错。
测试加固：①test_g43_engine 的补投断言原先把 _start_worker 整个 mock 掉（递增 _running 的正是它）→ 恒 0 → 补投会把队列倒空却仍通过，现 fake 同步递增并恢复精确断言；②test_doc_drift_guard 两处 skipTest 改为从文档现取真值再改坏（原来恰在行数真的变了时静默跳过）；③batch_start_limit / _refresh_stuck 两处静默 except 补 applog.warn。
@verify tests/test_g43_engine（7）· tests/test_doc_drift_guard（8，零 skip）· python scripts/doc_drift_check.py → DRIFT 0
