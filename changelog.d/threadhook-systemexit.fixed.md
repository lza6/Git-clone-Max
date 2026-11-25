# 线程 excepthook 按 CPython 语义静默忽略 SystemExit：正常线程退出不再被记成未捕获异常
@verify tests.test_exc_dump.TestThreadExcepthook.test_thread_systemexit_is_ignored + test_exc_dump（全模块 7 例）+ ruff/mypy
