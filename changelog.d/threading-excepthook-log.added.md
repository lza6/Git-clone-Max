# 线程内未捕获异常落盘：install_excepthook 增装 threading.excepthook（含线程名，windowed 打包不再无声消失）
@verify tests.test_exc_dump.TestThreadExcepthook（子进程正例+阴性对照）+ test_g80_hygiene + ruff/mypy
