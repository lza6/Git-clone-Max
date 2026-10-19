# 数据模型与持久化（权威 · 由代码核对生成）

> 本文是 `data/` 下持久化格式的**唯一事实源**。原《db_structure》文档已随 `计划书/` 目录一并
> 删除 —— 它停在 v8.0.2 的列名与迁移版本，与代码严重不符，留着比没有更贵。
> **修改 `gcm/db/repo_db.py` 的 `_SCHEMA` / `_migrate` / 数据文件格式时，必须同一次提交里更新本文。**
>
> 核对方式（可复现）：从代码实测，而不是从旧文档抄。
> ```bash
> ./.venv/Scripts/python.exe -c "
> import sys,tempfile,pathlib; sys.path.insert(0,'.')
> from gcm.db.repo_db import Database,_SCHEMA_VERSION
> d=pathlib.Path(tempfile.mkdtemp()); db=Database(d/'t.db')
> print('user_version=',_SCHEMA_VERSION)
> for t in ('repos','sync_history'):
>     print(t, [c[1] for c in db._conn.execute(f'PRAGMA table_info({t})')])
> "
> ```
>
> 最近核对：2026-10-14（schema 与代码一致；对应 9.2.x 线）。
> 版本号不写死在本文件 —— 唯一事实源是 `gcm/__init__.py` 的 `__version__`。

## 1. 数据库

SQLite，文件 `data/repos.db`，WAL 模式。

| 项 | 值 | 位置 |
|----|----|------|
| 写事务 | 显式 `BEGIN IMMEDIATE`（避免 WAL 下共享锁升级死锁） | `repo_db.Database._begin_immediate` |
| 锁等待 | `PRAGMA busy_timeout=30000` | `_BUSY_TIMEOUT_MS` |
| 外键 | `PRAGMA foreign_keys=ON` | `Database.__init__` |
| 连接 | `check_same_thread=False` + 进程内 `RLock`（**单写者**：DB 写入只在引擎主线程） | `Database.__init__` |
| schema 版本 | `PRAGMA user_version` = **3** | `_SCHEMA_VERSION` |

### 1.1 迁移链（`_migrate`，幂等）

| user_version | 变更 | 条目 |
|--------------|------|------|
| 0 | 初始 `repos` / `sync_history`（`CREATE TABLE IF NOT EXISTS`） | 基线 |
| 1 | `repos` + `tags` / `favorite` / `excluded` | G06-6 |
| 2 | `repos` + `note`（每仓库备注） | G37-6 |
| 3 | `repos` + `ssh_key`（该仓库指定 SSH 私钥） | G48-3 |

> 每条迁移先 `PRAGMA table_info` 判列存在（幂等），最后统一 `PRAGMA user_version=N` + commit。
> 新增列请递增 `_SCHEMA_VERSION` **并**补一段 `if v < N:`，不要直接改 `_SCHEMA` 里的 `CREATE TABLE`
> —— 那只对新建库有效，老用户库不会加列。

### 1.2 `repos`

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `owner` | TEXT NOT NULL | 作者/组织（子组会含 `/`） |
| `repo` | TEXT NOT NULL | 仓库名 |
| `folder_name` | TEXT NOT NULL | 本地目录名（跨 host 规范 `host__owner__repo`，GitHub 为 `owner__repo`） |
| `url` | TEXT NOT NULL | 主地址（HTTPS） |
| `local_path` | TEXT NOT NULL | 本地绝对路径 |
| `host` | TEXT NOT NULL DEFAULT 'github.com' | 平台 host |
| `default_branch` | TEXT | 默认/指定分支 |
| `head_sha` | TEXT | 最近同步后的 HEAD |
| `last_sync_at` | TEXT | 最近同步时间 |
| `created_at` / `updated_at` | TEXT NOT NULL DEFAULT `datetime('now','localtime')` | |
| `tags` | TEXT DEFAULT '' | 标签（迁移 v1） |
| `favorite` | INTEGER DEFAULT 0 | 收藏置顶（迁移 v1） |
| `excluded` | INTEGER DEFAULT 0 | 黑名单，一键更新排除（迁移 v1） |
| `note` | TEXT DEFAULT '' | 备注（迁移 v2） |
| `ssh_key` | TEXT DEFAULT '' | 该仓库指定的 SSH 私钥路径（迁移 v3） |

**约束**：`UNIQUE(owner, repo, host)` —— 同仓库在不同 host 是不同行。
（这正是 G67-3 的破坏点：重投不重建 `host_by_key` 会把非 GitHub 仓库写成
`host='github.com'`，产生重复行。守卫见 `tests/test_g67_closure.py::TestRetryReusesBatchContext`。）

### 1.3 `sync_history`

| 列 | 类型 | 说明 |
|----|------|------|
| `id` | INTEGER PK AUTOINCREMENT | |
| `repo_id` | INTEGER NOT NULL REFERENCES repos(id) ON DELETE CASCADE | 删仓库连带删历史 |
| `status` | TEXT NOT NULL | `success` / `failed` / `conflict` / `cancelled` |
| `action` | TEXT NOT NULL | `cloned` / `updated` / `fetched` / `empty` / `skipped` / `conflict` / `cancelled` / `failed` |
| `message` | TEXT | 结果消息（出站前经 `redact` 打码） |
| `detail` | TEXT | 附加详情 |
| `commits` | INTEGER DEFAULT 0 | 本次新提交数 |
| `head_before` / `head_after` / `remote_head` | TEXT | SHA 三元组，用于判定是否真的前进 |
| `duration_ms` | INTEGER | 耗时（慢仓 Top 榜数据源） |
| `started_at` / `ended_at` | TEXT | |

**索引**：`idx_sync_history_repo (repo_id, started_at DESC)`。
**治理**：每仓保留最近 200 条 + 默认 90 天窗口（`_history_retention_days`，G53-4 可配）。

## 2. 非 SQLite 数据文件（均在 `get_data_dir()` 下）

> 数据目录**唯一来源**是 `gcm.__main__.get_data_dir()`（`GCM_DATA_DIR` 环境变量 > `--portable` >
> `%APPDATA%/Git-clone-Max`，旧 `<exe>/data` 存在时沿用）。
> **任何模块不得自行拼数据目录路径** —— 这是 G67-4 的根因（安装版白名单永久失效）。
> 守卫：`tests/test_g67_closure.py::TestExtraHostsUsesRealDataDir`。

| 文件 | 用途 | 并发与治理 |
|------|------|-----------|
| `progress.json` | 断点续传（`finished` / `in_progress` / `failed`） | 原子写（tmp + `os.replace`）；`cross_process_lock` 双层锁；读-改-写在**同一临界区**内完成；G52-4 滚动裁剪 |
| `settings.json` | 全部设置（token / host_tokens / webhook token 加密落盘） | `.bak` 备份恢复；`cross_process_lock`；tmp 名含 pid+tid（G67-23） |
| `history.json` | URL 历史（去重、上限 200） | — |
| `lists/` | 任务清单持久化 | — |
| `session_runtime.json` | 跨会话续跑清单 + 看门狗心跳 | — |
| `retry_queue.json` | 失败重试队列（持久化 + 退避 + 三段式错误） | 引擎按批次读写；见 §3.1 |
| `.instance.lock` | 单实例锁 | 崩溃残留清理 |
| `error.log` / `app.log` | 结构化日志 | `RotatingFileHandler` 5MB×3；出站 `redact` |

## 3. 跨进程写锁（`gcm/util/filelock.cross_process_lock`）

`settings.json` 与 `progress.json` 的读-改-写都必须走它。语义：

1. 模块级 `RLock` 管**同进程多线程**；
2. `<path>.lock` 上的 `msvcrt.locking`（Windows）/ `fcntl.flock`（POSIX）管**跨进程**；
3. 同线程**重入**只复用外层文件锁（msvcrt 按句柄加锁，重复加会自锁死等）；
4. 加锁失败 → **先关闭已打开的句柄**再降级为仅线程锁，并写 `app.log` 留痕
   （静默降级会失去跨进程互斥，必须可观测）。

守卫：`tests/test_g67_closure.py::TestCrossProcessLock`
（**两个真子进程 + 启动屏障**；含无锁对照组，断言无锁时丢更新、加锁后两次都保留 ——
没有对照组就可能是「永远通过」的空壳）。

### 3.1 原子写与 retry_queue 结构（G71）

所有 JSON 持久化统一走 `gcm/util/atomic_io.py`：

1. **唯一 tmp 名**（`mkstemp` O_EXCL，随机名）—— 固定 tmp 名在多线程/多实例下会互相截断；
2. **写后 `flush` + `fsync`**，替换后再 `fsync` 目录（POSIX）—— 掉电不出现「目录项替换了、内容没落盘」；
3. **替换退避**（12 次线性退避）—— Windows 上杀软/索引器短暂持有句柄时 `os.replace` 会
   `WinError 5/32/33/1224`；只对这些瞬时 winerror 重试，真正的权限错误立即上抛；
4. 失败**上抛**，由调用方决定用户可见提示或显式记录，不静默吞。
5. 诊断包 zip 复用同一 `replace_with_retry`，并发导出不互相覆盖。

`retry_queue.json` 退避（G72）：`delay = min(base*n, 1h)` 后施加 50%–100% jitter，
避免一批同时失败的仓库在同一秒再次齐发；引擎单次 tick 最多重投 50 条。

`retry_queue.json` 结构：

```json
{
  "entries": {
    "owner/repo": {
      "key": "owner/repo", "status": "queued", "state": "queued",
      "attempts": 1, "url_https": "https://...", "display": "owner/repo",
      "folder_name": "owner__repo", "local_path": "", "ref": "", "is_local": false,
      "message": "fatal: ...", "detail": "",
      "category": "network", "code": "network", "title": "网络连接失败或超时",
      "cause": "...", "action": "...", "retryable": true,
      "next_retry_at": 1760000000.0, "next_retry_at_iso": "2026-10-19 12:00:00",
      "last_attempt_at": "2026-10-19 11:59:30", "queued_at": "2026-10-19 11:59:30"
    }
  },
  "dropped": [{"key": "...", "attempts": 3, "reason": "...", "time": "..."}],
  "updated_at": "2026-10-19 12:00:00"
}
```

- `retryable` 由 `app/errors.classify_error` 决定；只有 true 才入 entries。
- `state`：`queued` / `due`（用户点「立即重试」后置 0）/ `dropped`（超限，移入 dropped）。
- `path=None` 时是**纯内存队列**（测试/临时用），结构相同但不出现在磁盘。
