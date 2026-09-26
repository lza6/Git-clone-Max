# 性能与可观测性评估（v9.0.0，诚实标注）

## 1. 现状（桌面单机工具）
| 维度 | 现状 | 证据 |
|------|------|------|
| 并发克隆 | QThreadPool ≤32，批次流控 64，弱网降级 | engine.py |
| 本地扫描 | v8.0.2 提速 ~95x（150s→1.6s） | scanner.py |
| 内存 | 日志环形缓冲 3000 条；退役 worker 引用回收 | engine.py |
| SQLite | WAL + busy_timeout=30000 + BEGIN IMMEDIATE | repo_db.py |
| 覆盖率门禁 | 84% | coverage |
| 变异测试 | 击杀率 95.24% | run_mutation_smoke.py |
| 可观测性 | app.log/error.log + --diag --json + health_check | applog/diag/health_check |

## 2. 当前限制（诚实）
- **无服务端**：无远程 Metrics/Tracing（Prometheus/OTel）；Load Balancer/CDN/Redis 等**不适用**于纯桌面单机（它们解决服务端高并发，本工具无服务端）
- 全量测试 15-20 分钟（已分块缓解；CI fast-smoke 扩展已加，主矩阵未拆 fast/slow）
- 单实例锁限制多开；无分布式共享仓库池

## 3. SaaS 化架构建议（若未来演进，按真实需求启用）
```
[桌面客户端] → HTTPS → [API Gateway/LB] → [App Server (FastAPI/Django)]
                                        ├─ Redis（缓存/限流/队列）
                                        ├─ PostgreSQL（主从/分片）
                                        ├─ 对象存储（CDN 前置）
                                        └─ 消息队列（异步任务：克隆/归档/报表）
```
- **Rate Limiting**：现已在 CLI/HTTP API 层实现（rate_limit.py + HTTP 只读边界）；SaaS 化后加 Redis 滑动窗口
- **Circuit Breaker**：Git 远端失败已有退避/降级（service.py 弱网链），可抽象为断路器模式
- **Observability**：现在只有本地日志；SaaS 化需 OTel 结构化 Logs/Metrics/Traces
- **Health Checks**：已有 health_check.py（产物级）；SaaS 化加 /healthz + /readyz

## 4. 桌面单机可立即做的性能补位（下一轮候选）
- G55-1 CI fast/slow 主矩阵拆分（已加 fast-smoke 扩展）
- progress.json/历史自动清理的调度（G52-4/G53-4 已提供能力，未默认开启）
- 大仓库 dir_size 计算后台化（manage 表已懒加载，统计评分卡同步计算可优化为异步）
