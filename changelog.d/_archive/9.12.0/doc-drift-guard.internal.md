# 文档数字漂移有了守卫（六处同时过期终于会红）
新增 scripts/doc_drift_check.py：模块行数表（CLAUDE.md §6 + docs/architecture.md §4，容差 ±5）、超 100 行函数数、测试文件数（三处）、技能包数、版本号共 6 类断言，有漂移即 DRIFT n 非 0 退出。首次运行即报出 18 处真实漂移（service 1768→1899、engine 1169→1516、progress_table 799→790、repo_db 799→793、settings_panel 779→798、main_window 2658→2791、14 个超长函数→20 个、159 个测试文件→162、5 个技能包→6 个…）现已全部修正。
配套 tests/test_doc_drift_guard.py 8 例：正向断言 DRIFT 0，反向 monkeypatch 六个检查各自「改坏必红」——否则守卫只是永远返回 0 的代码。
@verify python scripts/doc_drift_check.py → DRIFT 0；tests/test_doc_drift_guard 8 例绿
