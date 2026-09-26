# ADR-002：progress.json 原子写 + 批次流控

- 状态：Accepted（v4.0.0 / G43-2）
- 背景：高并发 32 时进度文件并发写 → 数据损坏；500 仓批次全量入队 → 内存峰值高
- 决策：
  - progress.json 内存态由 engine 聚合，周期落盘（tmp + os.replace 原子写）+ 进程级锁
  - 去重后 >64 只入队前 64，完成一个由 `_on_worker_done` 惰性补投（`_pending_queue`）
  - G52-4 追加滚动/归档/阈值治理（progress_gc.py）
- 后果：崩溃后 in_progress 可恢复；内存峰值受控；进度结构保持 load_progress 兼容
- 相关：G22-2 崩溃恢复、G52-4
