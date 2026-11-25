# prune-untouched 删除前过系统路径护栏（盘根/系统目录/主目录拒绝）
@verify tests.test_b3_path_guard + test_r01_prune 22 tests OK；拒绝项计入 skipped_dangerous 且保留 DB 记录
