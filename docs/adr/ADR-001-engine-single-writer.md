# ADR-001：引擎统一调度（Engine Single-Writer）

- 状态：Accepted（2026-09-18 起，v4.0.0 引入，持续演进）
- 背景：早期 worker 并发写 SQLite/进度文件 → PermissionError/锁死/UI 永久 busy
- 决策：
  - `SyncEngine`（gcm/app/engine.py）作为唯一调度器：launch/去重/批次流控/取消/进度/重试/会话全部收敛
  - worker（CloneWorker）只做 git 同步 + 结果回传；不写 DB、不落盘进度
  - `_on_result` 统一落库 + 更新内存进度态；`flush_progress` 单线程原子写
- 后果：
  - 正：SQLite 无跨线程写冲突；进度安全；可离屏单测
  - 负：engine 行数 864 偏大；新能力需经 engine 收敛（避免旁路状态）
- 相关：G43-2 批次流控、G52 重试/会话、G48-1 并发自适应
