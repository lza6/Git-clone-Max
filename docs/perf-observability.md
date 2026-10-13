# 性能与可观测性评估（2026-09-26 初稿；2026-10-13 校准，诚实标注）

> 版本号不写死在本文件：唯一事实源是 `gcm/__init__.py` 的 `__version__`。

## 1. 现状（桌面单机工具）
| 维度 | 现状 | 证据 |
|------|------|------|
| 并发克隆 | QThreadPool ≤32（默认 8，设置页可调 1–32），批次流控首批 64，弱网降级 | engine.py |
| 本地扫描 | 实测 300 仓 149.4s→1.6s（约 95x，G38 专项） | scanner.py |
| 内存 | 日志环形缓冲上限 3000 条；退役 worker 引用回收 | engine.py |
| SQLite | WAL + busy_timeout=30000 + BEGIN IMMEDIATE | repo_db.py |
| 覆盖率门禁 | 快集口径：Windows 81 / POSIX 80（`.github/workflows/ci.yml` 主矩阵） | ci.yml |
| 变异测试 | 击杀率 95.24% | run_mutation_smoke.py |
| 可观测性 | app.log/error.log + --diag --json + health_check | applog/diag/health_check |

## 2. 当前限制（诚实）
- **无服务端**：无远程 Metrics/Tracing（Prometheus/OTel）；Load Balancer/CDN/Redis 等**不适用**于纯桌面单机（它们解决服务端高并发，本工具无服务端）
- 快集 / 全量耗时与用例数见 `docs/慢测定位.md`（那里是耗时的唯一事实源，本文件不复述，避免两处漂移）
- 单实例锁限制多开；无分布式共享仓库池

## 3. SaaS 化架构建议（若未来演进，按真实需求启用）
```
[桌面客户端] → HTTPS → [API Gateway/LB] → [App Server (FastAPI/Django)]
                                        ├─ Redis（缓存/限流/队列）
                                        ├─ PostgreSQL（主从/分片）
                                        ├─ 对象存储（CDN 前置）
                                        └─ 消息队列（异步任务：克隆/归档/报表）
```
- **Rate Limiting**：当前**没有** API 层限流器 —— 本地 HTTP API 是**回环 + 未鉴权**的
  （安全边界见 `docs/agent-contract.md` §5），不是对外服务，没有限流面；
  仓库内已有的「限速」是**下载限速**（`settings.rate_limit_kbps` → git `http.lowSpeedLimit`，G04-4），
  与 API 限流是两件事。SaaS 化后需引入 Redis 滑动窗口。
- **Circuit Breaker**：Git 远端失败已有退避/降级（service.py 弱网链），可抽象为断路器模式
- **Observability**：现在只有本地日志；SaaS 化需 OTel 结构化 Logs/Metrics/Traces
- **Health Checks**：已有 health_check.py（产物级）；SaaS 化加 /healthz + /readyz

## 4. 桌面单机可立即做的性能补位（下一轮候选）

> 本节两条均经 2026-10-13 核对代码后保留：都**尚未**实施。

- 清理/报表的默认策略：`history_retention_days` 默认 90 天、定时报表默认关闭
  （`sched_report_enabled=False`）—— 是否需要更细粒度的清理调度仍待评估
- 大仓库 dir_size 计算后台化（manage 表已懒加载，统计评分卡按仓库惰性计算，可进一步异步）

