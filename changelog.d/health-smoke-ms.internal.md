# 健康检查拆分真实启动耗时 smoke_ms
health_check.elapsed_ms 含大文件 SHA-256/版本提取，不再当启动时间；新增 smoke_ms 并让 bench_startup 只读该字段。
@verify health_check 真跑 smoke_ms=1802ms；bench_startup exe_ms=1802
