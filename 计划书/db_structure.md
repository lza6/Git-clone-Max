# db_structure.md（数据库权威文档 · 建立：2026-09-25，G51-1）

> 依据 `gcm/db/repo_db.py`（HEAD 4429c32，v8.0.2）核对。Schema 变更时必须同步更新本文件。
> 数据库：SQLite（WAL 模式），文件 `data/repos.db`；写事务显式 `BEGIN IMMEDIATE`（G22-3，repo_db.py:118）；`busy_timeout=30000`。

## 1. 迁移链（PRAGMA user_version）

| 版本 | 变更 | 证据 |
|------|------|------|
| 0 | 初始库：repos 基础列 | `_migrate`（repo_db.py:56） |
| 1 | +tags / favorite / excluded 列（G06-6） | 迁移 0001 |
| 2 | +note 列（G37-6 每仓库备注） | 迁移 0002 |
| 当前 | user_version=2（随代码演进续加） | `_migrate` 幂等 |

> 规则：每次迁移先 `BEGIN IMMEDIATE`，幂等（`PRAGMA user_version` 判断），失败回滚且备份。

## 2. 表结构

### repos（仓库元数据）
| 列 | 类型 | 约束/说明 |
|----|------|-----------|
| id | INTEGER | PK AUTOINCREMENT |
| owner | TEXT | 作者 |
| repo | TEXT | 仓库名 |
| host | TEXT | 平台 host（github.com/gitlab.com/…） |
| folder_name | TEXT | 本地目录名（跨 host 规范 host__owner__repo） |
| local_path | TEXT | 本地绝对路径 |
| url_https | TEXT | HTTPS 地址 |
| url_ssh | TEXT | SSH 地址（可空） |
| branch | TEXT | 默认/指定分支 |
| depth | INTEGER | 浅克隆深度（0=满量） |
| status | TEXT | 最近状态 |
| tags | TEXT | 标签（JSON，G03） |
| favorite | INTEGER | 收藏置顶 0/1 |
| excluded | INTEGER | 黑名单（一键更新排除）0/1 |
| note | TEXT | 备注（G37-6） |
| health | INTEGER | 健康评分 0-5（G47） |
| dir_size | INTEGER | 磁盘占用字节（G47） |
| last_sync | TEXT | 最近同步时间 |
| created_at / updated_at | TEXT | 时间戳 |

### sync_history（同步历史快照）
| 列 | 类型 | 说明 |
|----|------|------|
| id | INTEGER | PK AUTOINCREMENT |
| repo_id | INTEGER | FK→repos.id |
| status | TEXT | success/failed/conflict/cancelled |
| action | TEXT | clone/update/skip/… |
| duration_ms | INTEGER | 耗时（G43-3 慢仓 Top10 数据源） |
| objects | INTEGER | 传输对象数（进度） |
| message | TEXT | 结果消息（redact 后） |
| created_at | TEXT | 时间 |

> 治理：每仓保留最近 200 条 + 90 天窗口（G33-5，repo_db.py:237 参数化；G53-4 计划改可配天数）。

### 其他数据文件（非 SQLite）
| 文件 | 用途 | 治理 |
|------|------|------|
| data/progress.json | 进度周期落盘（断点续传） | 原子写 + 进程级锁；G52-4 计划滚动/归档 |
| data/settings.json | 设置（含加密 token/host_tokens） | .bak 备份恢复（G06-8）；加密字段 DPAPI/Keyring/XOR |
| data/history.json | URL 历史（G02-4） | 去重、上限 200 |
| data/lists/ | 任务清单持久化（G35-4） | — |
| data/.instance.lock | 单实例锁（G06-2） | 崩溃残留清理 |
| data/error.log / app.log | 结构化日志 | RotatingFileHandler 5MB×3 + redact |

## 3. 索引与约束
- repos：`folder_name` 唯一（去重键；G43-2 批次流控依赖）；`owner/repo/host` 组合用于匹配。
- sync_history：`repo_id` 索引（详情页时间线/慢仓查询）。
- 迁移幂等 + `BEGIN IMMEDIATE` 防 WAL 死锁（G22-3）。

## 4. 修改记录
| 日期 | 变更 | 对应条目 |
|------|------|----------|
| 2026-09-25 | 首次建立（基线核对 v8.0.2） | G51-1 |
