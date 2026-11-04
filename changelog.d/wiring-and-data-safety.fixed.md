# 接线缺口与数据安全：事件落盘/体积刷新/原子备份/凭据不被清空
event_sink 与 invalidate_size_cache 此前「实现完整但零生产调用点」——现在事件真正落盘（入口统一挂载，幂等）、「刷新列表」两级缓存都清（进程内 + dir_stats），并新增孤儿模块守卫防复发。backup_to 改 tmp→fsync→replace + quick_check 并补 UI 入口；settings.json 写盘补 fsync、解密失败的密文原样保回（换机器/DPAPI 缺失时不再把凭据永久清空）。根目录卫生修复（.gitignore 补状态文件与 ui-audit 截图）。README 横幅重写；文档闸新增机制锚点（CI 依赖单源、横幅不腐烂、已交付能力必须进功能表）；known-issues 三条过期条目复核归档。管理页大小列三态（计算中/值/未知）+ 健康列解释；分割器按视口高度派生（768 高不再只剩 4 行）。
@verify tests/test_g84_wiring（9 例，含真实落盘与打码）+ test_g84_settings_safety（8 例，含反向自检）+ test_g84_backup_atomic（5 例）+ test_g84_quality_gates（11 例）；快集全量见 docs/验证记录-2026-11-04.md
