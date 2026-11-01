# 目录大小落库缓存（schema v6）：千级仓库二次启动不再全量递归 stat
dir_size() 是递归 scandir：实测 15 万文件 484ms。原来只放进程内内存缓存 → 每次重开应用都要把几百个仓库重新 stat 一遍。新增 dir_stats 表（迁移 v6，只加表不动既有列），默认 30 分钟 TTL，批量读写。
严格遵守单写者 ADR：**DB 的读与写都只在 GUI 线程**（进函数时批量读、回填时批量写），工作线程只做纯文件系统遍历（Database.dir_size 是静态方法，不碰连接）。
@verify tests/test_c3_data.TestDirStatsCache（4 例）+ scripts/bench_scale.py 实测 3000 条命中 3.92ms
