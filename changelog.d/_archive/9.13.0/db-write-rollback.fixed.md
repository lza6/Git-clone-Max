# 数据库写失败会回滚，不再把事务悬住拖慢后续所有写入
upsert_repo / add_sync_history / set_repo_head 原来是 BEGIN IMMEDIATE → execute → commit，**没有 try/except-rollback**。一次失败（锁超时/约束冲突/SQL 错）会把事务悬在那里，后续写入要等 busy_timeout=30s 才报错 —— 用户侧只表现为「越用越慢、偶尔卡住」，而不是一条清晰的错误。bulk_upsert_repos 当时是对的，单条写入漏了。
抽成 gcm/db/txn.py 的 write_txn() 上下文（repo_db 已贴 800 行红线，按项目范式落新模块），三处写入统一走它。
@verify tests/test_c3_data（3 例：注入失败后**同一连接的下一次写必须立刻成功** / 历史写失败后仍能写 / 成功路径确实 commit）
