# 改完设置重启后引擎真的用新值（启动路径不再走第二份参数表）
MainWindow.__init__ 手写着第二份 settings→SyncEngine 参数列表，与 engine_factory 那份漂移，漏了 host_concurrency / time_budget / concurrency_state_path —— 真机实测：host_concurrency 改 11、time_budget 改 20 分钟后重启，控件回填正确、引擎仍是 4 / 0s（界面骗人）。
修法：启动期也走 build_sync_engine，删掉手写列表 → settings→engine 变唯一映射点；主窗 __init__ -19 行（252→240）。空转引擎用 enable_retry_queue=False —— 启动时 _last_batch_ctx 为空，挂上重投队列会自动重投并把非 GitHub 仓库落库成 host=github.com（G67-3 同类污染）。
@verify tests/test_c1_wiring.TestStartupEngineReflectsSettings（4 例：启动读持久化值 / 空闲无重投队列 / 批次有重投队列 · 对照组 / 启动恢复并发记忆）+ 真机跨重启复验
