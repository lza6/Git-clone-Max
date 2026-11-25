# Qt 消息归档：qWarning/qCritical/qFatal 写 error.log（qFatal 在 abort 前留下「遗言」，0xC0000409 类闪退可定位）
@verify tests.test_exc_dump.TestQtMessageHandler（qWarning 落盘+打码 / qFatal 子进程先落盘后 abort / 幂等）+ tests.test_main（fake 注入同步）+ test_g80_boundaries + test_g80_hygiene + test_g84_quality_gates（vulture）+ ruff/mypy
