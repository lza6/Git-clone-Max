# Git-clone-Max Agent 契约（G56-5）

> 适用版本：v9.20.0。本文档是所有 agent 接入点
> （CLI `--json` / MCP server / 本地 HTTP API / `skills/gcm-*`）的统一契约。
> 任何实现与本文档冲突时，以实际输出为准（测试 `tests/test_g56_contract.py` 抽样断言）。

## 1. 概述

Git-clone-Max 提供三类 agent 可调用入口，全部**默认只读**：

| 入口 | 启动方式 | 读取 | 写操作 | 显式放行 |
|------|----------|------|--------|----------|
| CLI `--json` | `python -m gcm --cli --json [urls.txt\|-]` | 克隆/同步到 `--dir` 目录 | 克隆写盘（本工具主业） | 本体即写操作 |
| MCP server（G56-2） | `python -m gcm.mcp_server` | 数据库查询/报表 | 无（默认拒绝） | `GCM_MCP_ENABLE=1` + `--allow-export` |
| 本地 HTTP API（G56-4） | `python -m gcm --cli --serve 127.0.0.1:port` | 查询/状态/报表 | 无（默认 403） | `--allow-mutate` |

共性原则：

- **打码**：所有输出经 `gcm.util.redact`，URL 内嵌凭据 / Authorization / PRIVATE-TOKEN 一律 `***`；
- **不触网**：本地 E2E 一律 `file://` 本地裸仓；真实平台仓库由调用方自行承担网络行为；
- **无凭据列**：MCP / HTTP 返回行剔除 `token` / `host_tokens` / `ssh_key` 等敏感列；
- **stdout 纯净**：JSON 输出时 stdout 只含 JSON 文档，进度文本走 stderr。

## 2. CLI `--json` 输出 schema（顶层）

`python -m gcm --cli --json --dir <out> <urls.txt>`

```json
{
  "version": "9.20.0",
  "ok": true,
  "counts": {"success": 2, "failed": 0, "other": 0, "total": 2},
  "results": [
    {
      "url": "file:///.../r0.git",
      "status": "success",
      "action": "cloned",
      "path": "/abs/out/r0",
      "message": "克隆完成",
      "code": 0
    }
  ],
  "invalid": []
}
```

### 2.1 字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | string | 项目版本，恒等于 `gcm.__version__`（G66-2 起单源，`tests/test_g66_patch.py` 强制校验） |
| `ok` | bool | `true` 当且仅当 `failed==0` 且 `total>0` |
| `counts` | object | `success` / `failed` / `other` / `total`（`other` = conflict+cancelled+skipped） |
| `results[]` | array | 每仓库一行 |
| `results[].url` | string | 原始输入 URL（本地路径原样；已打码） |
| `results[].status` | string | `SyncStatus.value`：`success` / `failed` / `conflict` / `cancelled` / `skipped` |
| `results[].action` | string | `SyncAction.value`：`cloned` / `updated` / `fetched` / `empty` / `failed` / … |
| `results[].path` | string | 结果落盘绝对路径（相对时已 resolve） |
| `results[].message` | string | 人类可读结果/失败说明（已打码） |
| `results[].code` | int | 0=成功；非 0=失败（当前 1） |
| `results[].mirror_used` | string | **G81 起新增（加性字段）**。实际取数用的镜像前缀，`""` = 直连源站。回答「这次为什么走了镜像」；打码输出。 |
| `results[].degrade_path` | string | **G81 起新增（加性字段）**。弱网降级链：`""` = 常规克隆；`treeless` = `--filter=blob:none` 部分克隆；`depth1` = 单分支浅克隆（完整历史未拉取）。回答「为什么历史不完整」。 |
| `invalid[]` | array | 无效输入行条目，见 §3 错误 schema |

### 2.1b `--dry-run` 的 JSON 形态（G84 新增，与常规结果**不同形**）

```json
{
  "version": "9.20.0",
  "ok": true,
  "mode": "dry_run",
  "preview_id": "0f1e2d3c…（32 位 hex，Rev17 T7）",
  "plan": {
    "root": "/abs/out",
    "dry_run": true,
    "counts": {"clone": 2, "update": 1, "replace": 0, "blocked": 0,
               "duplicate": 0, "invalid": 1},
    "writable": 3,
    "rows": [{"url": "...", "folder": "o__r", "target": "/abs/out/o__r",
              "action": "clone", "reason": "目标目录不存在"}],
    "limits": "本预览只做本地判定：不说『已是最新』（需远端 ls-remote）..."
  }
}
```

- **`mode` 字段是判别位**：有它就不是克隆结果，`ok` 只表示"预览算出来了"，
  与任何仓库是否成功无关。消费方必须先判 `mode` 再读 `counts`。
- **`preview_id`（Rev17 T7）**：进程内 uuid（不落盘）。用途：①同一批输入两次
  预览的比对锚；②与 GUI 预览对话框同源 `plan_batch`——counts 一致即
  "看到的就是会执行的"。MCP `clone_batch(dry_run=true)` 响应同字段。
- `action` 六档语义（与执行器逐条对齐）：
  `clone` 新克隆 · `update` 增量更新 · **`replace`（会先删除目标目录再克隆）** ·
  `blocked`（`local_path` 来自导入元数据 → 执行器不删除，非空非 git 会失败） ·
  `duplicate` 本批内同目录 · `invalid` 解析失败。
  ⚠️ `replace` 是**破坏性**动作，agent 必须显式告知用户。

### 2.1c `--prune` 三态的 JSON 形态（R-01，与克隆结果**不同形**）

```json
{
  "version": "9.20.0",
  "ok": true,
  "mode": "prune:prune",
  "pruned": 1,
  "dir_removed": 0,
  "skipped_protected": 0,
  "reported": 0,
  "items": [{"folder": "o__r", "url": "https://github.com/o/r",
             "remote_gone": true, "protected": false,
             "db_removed": true, "dir_removed": false,
             "skipped_reason": ""}]
}
```

- 三模式互斥：`--no-clean`（默认只报告，`mode: "prune:no-clean"`，什么都不删）、
  `--prune`（删 DB 记录，不删本地目录）、`--prune-untouched`（删 DB 记录 + 删本地目录）。
- `mode` 字段是判别位，消费方必须先判 `mode` 再读计数。
- ⚠️ `--prune-untouched` **会删除本地目录**（不可恢复，不进回收站）。
  agent 必须显式告知用户后才可调用。
- 三重护栏（服务端强制，不是建议）：`protected`（excluded 标记）的仓库
  **任何模式都不删**；只处理 `remote_gone`（最近一次同步失败且报
  "Repository not found"）的仓库；每次删除写 oplog（`undoable=0`，不可撤销）。

### 2.1d `--cli` 是**无状态批处理**入口（不写本地库）

2026-11-08 E2E 实测确认并在此明确：`--cli` 路径构造引擎时 `db=None`，
**克隆结果只出现在 stdout/`--json` 里，不写 `repos.db`**，也不更新
`progress.json` / 会话清单。

| 入口 | 是否入库 | 适用 |
|------|---------|------|
| `--cli` / `--cli --json` | **否**（无状态） | CI、脚本、一次性批量抓取 |
| GUI 主窗 | 是 | 需要「仓库管理 / 搜索 / 统计 / 续跑」的日常使用 |
| `--serve`（HTTP API） | 读库 + 可选写库（`--allow-mutate`） | agent 读现状 |
| MCP `clone_batch` | 走 CLI 子进程 → **不入库** | agent 一次性抓取 |

因此 `--prune`（扫描 `repos.db`）**看不到**纯 `--cli` 克隆过的仓库 —— 这是
「无状态」的直接推论，不是缺陷。要入库请用 GUI。

### 2.1e `--fetch-release`（Rev17 Batch C 新增，与批处理无关的独立入口）

```bash
python -m gcm --cli --fetch-release OWNER/REPO [--out <目录>] [--json]
```

下载该仓库**最新 Release** 中匹配本机平台（Windows x64 优先，无平台词的通用
压缩包兜底）的附件到 `<out>`（默认 `./release-downloads`），带断点续传
（`.part` + Range；206 起始偏移错位宁可重下不拼接；本地磁盘错误立即失败不重试）。

`--json` 形态（与批处理结果**不同形**）：

```json
{"ok": true, "repo": "OWNER/REPO", "tag": "v1.2.3",
 "asset": "tool-windows-x86_64.zip", "path": "…/tool-windows-x86_64.zip",
 "size": 12345}
```

失败：`{"ok": false, "error": "…"}`（无匹配资产时附 `assets` 名单与 `tag`）。
token 取自 git credential manager（与检查更新同源），只发给 api/github.com。

### 2.2 退出码

| 码 | 含义 |
|----|------|
| 0 | 全部成功（可存在无效行，但不影响有效仓结果） |
| 1 | 有失败（任一仓库 failed/conflict/cancelled 视为非全成功） |
| 2 | 无有效输入（无有效输入），即文件为空 / 全部无效 / 读取失败 |

## 3. 统一错误 schema

所有错误对象统一为：

```json
{
  "code": 2,
  "message": "无法解析：C:\\...\\not_a_repo",
  "redacted": "无法解析：C:\\...\\not_a_repo"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `code` | int | 2=无效输入 / 403=只读拒绝 / 404=未知端点 / -32601..-32603=JSON-RPC 错误码 |
| `message` | string | 原始人类可读错误（可能含敏感片段） |
| `redacted` | string | 已打码版本，供日志/展示 |

范围约定：`message` 可直接打日志；`redacted` 用于用户可见输出，保证 URL/Token 不出屏。

**包装层差异（Rev16 D4 校正，以代码为准）**：

- HTTP API 的错误响应把上述对象包在 `error` 键里：
  `{"error": {"code": 403, "message": "...", "redacted": "..."}}`
  （`gcm/http_api.py:_send_json`，客户端取 `resp["error"]["code"]`）。
- MCP `clone_batch`（同步路径）返回 `{"clone_result": <CLI 顶层 JSON>, "cli_exit_code": 0|1|2}`
  —— 不是「CLI 顶层契约」直接透传；`clone_result` 里才是 §2 的 schema。
  `job_status` / `job_cancel` 返回作业快照（`{job_id, status, ...}`）。

## 4. MCP server（G56-2）

- 启用：`GCM_MCP_ENABLE=1` 后才启动（否则退出码 3，见 §6）。
- 数据目录：`GCM_DATA_DIR` 覆盖，默认 `<repo>/data`（repos.db）。
- 工具清单（`tools/list`）。**默认只暴露只读工具**；带 `mutate` 标记的工具只有
  显式放行（`GCM_MCP_ENABLE=1` + `--allow-mutate`）时才**出现在列表里**（不是"存在但报错"）：

| 工具 | 门禁 | 参数 | 返回 |
|------|------|------|------|
| `list_repos` | 只读 | `host?` | `{repos: [...], count}`，行含 `owner/repo/host/folder_name/local_path` 及元数据，剔敏感列 |
| `search_repos` | 只读 | `query` | 按 owner/repo/host/folder_name/tags/note 关键字过滤 |
| `status` | 只读 | – | `{status: {...stats_overview}}` |
| `job_status` | 只读 | `job_id` | `{job_id,status,finished,result}`（异步作业进度/结果） |
| `export_report` | `--allow-export` | `format: csv\|markdown`, `out_dir?` | 写产出文件（默认拒绝） |
| `clone_batch` | `--allow-mutate` | `urls[]`, `dry_run?`, `async?` | 同步：CLI 顶层契约；`dry_run=true`：预览计划；`async=true`：`{job_id,status}` |
| `job_cancel` | `--allow-mutate` | `job_id` | `{job_id,status:"cancelling"}`，终止子进程树 |

### 4.1 预览与异步作业（G84 新增，加性）
- `clone_batch` 传 `dry_run: true` → **不联网、不写盘、不起子进程**，返回
  `{dry_run:true, plan:{counts, writable, rows[], limits}}`。判定口径：
  `clone`（目标目录不存在）/ `update`（含 `.git`）/ `blocked`（目录被占，不会覆盖）/
  `duplicate`（本批内同目录）/ `invalid`（解析失败）。**`limits` 字段自曝边界**：
  本地预检不说"已是最新"（需远端 `ls-remote`）。
- `clone_batch` 传 `async: true` → 立即返回 `{job_id, status:"running"}`，作业在后台线程
  跑 `--cli --json` 子进程；用 `job_status` 轮询、`job_cancel` 取消（杀整棵进程树）。
  不传 `async` 时行为与旧版**完全一致**（同步等待，契约不变）。
- 进度：客户端在 `tools/call` 的 `params._meta.progressToken` 里给 token 时，
  服务端才会发 `notifications/progress`（无 token 不发，避免污染客户端读流）。
- 作业表上限 20 个：超出按插入顺序丢**已完成**的；运行中的作业不会被静默遗忘。

### 4.2 资源与提示词（G84 新增，加性）
`initialize` 的能力声明不再是空对象：`resources: {subscribe:false, listChanged:false}`、
`prompts: {listChanged:false}`。

| 资源 URI | MIME | 内容 |
|----------|------|------|
| `gcm://status` | application/json | 统计概览（与 `status` 工具同源） |
| `gcm://repos` | application/json | 仓库清单（剔敏感列 + 打码） |
| `gcm://report/markdown` | text/markdown | 内存生成的报表（不写盘） |
| `gcm://history` | application/json | 全库最近 50 条同步记录（JOIN owner/repo；agent 判断「上次跑成没成」） |

| 提示词 | 参数 | 用途 |
|--------|------|------|
| `clone-batch` | `urls` | 引导「先 dry_run 预览 → 再执行 → 用 job_status 轮询」 |
| `diagnose-failure` | `repo` | 按错误分类给出排查顺序（auth/network/not_found…） |

- 未知 URI / 缺参数一律返回 JSON-RPC `-32602`，**不会把 stdio 通道带崩**。
- 资源文本出站前过 `redact`：库里历史行若残留 URL 内嵌凭据，不会原样吐出。

- 退出码：`GCM_MCP_ENABLE` 未启用 → `3`（区别于 CLI 0/1/2）。

### 4.1 stdio 握手示例

```
→ {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{}}}
← {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26","capabilities":{"tools":{"listChanged":false}},"serverInfo":{"name":"gcm","version":"9.20.0"}}}
→ {"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}
← {"jsonrpc":"2.0","id":2,"result":{"tools":[{...list_repos...}, ...]}}
→ {"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"list_repos","arguments":{}}}
← {"jsonrpc":"2.0","id":3,"result":{"repos":[],"count":0}}
```

## 5. 本地 HTTP API（G56-4）

- 只绑定 `127.0.0.1`；默认只读。
- **Host / Origin 校验（G69 起）**：只绑回环本身**不足以**防 DNS rebinding ——
  攻击者把自己的域名解析到 `127.0.0.1`，浏览器就与本地服务**同源**，CORS 不参与，
  页面即可读取仓库清单。因此 Host 缺失、Host 非回环、或带跨站 `Origin` 的请求
  一律 **403**（错误 schema 同 §3）。agent/脚本用 `127.0.0.1` / `localhost` 访问
  即可，无需额外处理。
- 端点：

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/version` | `{version, readonly}` |
| GET | `/api/repos?host=&limit=&offset=` | 仓库列表（剔敏感列；`limit` 默认 500、上限 5000，`offset` ≥0；返回 `count/total/limit/offset/truncated`） |
| GET | `/api/repos/search?q=` | 关键字搜索（不分页，返回全部命中） |
| GET | `/api/status` | 统计概览 + `readonly` |
| GET | `/api/report?format=csv\|markdown\|json` | 报表（内存生成，不写盘） |
| GET | `/api/events?replay=&types=&heartbeat=` | **SSE 事件流**（G71：批次/任务/重试/错误；长连接，见 §5.1） |
| GET | `/api/events/snapshot?limit=&types=` | 最近事件 JSON 快照（不便维持长连接的 agent 用） |
| POST | `/api/clone` | 批量克隆；默认 403；`--allow-mutate` 开启后执行并审计 |

- 写边界：任何 `POST` 在只读模式一律 `403`，错误 schema 同 §3。

### 5.1 事件流契约（G71）

> **进程边界**：EventBus 是**进程内**的。独立 `python -m gcm --cli --serve` 进程
> 只能看到它自己进程内发布的事件（通常为空）。要观察 GUI 引擎的真实事件，
> 请在设置页开启「本地 API / 事件流」（`local_api_enabled=true` + 端口），
> 由 GUI 进程内托管同一套端点。

`GET /api/events` 返回 `text/event-stream`，每帧格式：

```
id: 42
event: task_result
data: {"type":"task_result","seq":42,"ts":1760000000.0,"batch_id":"20261019-...","index":0,"key":"owner/repo","status":"success","action":"cloned","message":"ok"}

```

- **参数**：`replay` 0..500（连接时补发最近 N 条，默认 0）；`types` 逗号分隔
  （未知类型不报错，便于向后兼容）；`heartbeat` 1..60 秒（空闲注释帧
  `: ping`，默认 15）。
- **断线续传（G72）**：浏览器 `EventSource` 重连会自动带 `Last-Event-ID`
  请求头；服务端优先按该 seq 精确补发 `seq > Last-Event-ID` 的事件（覆盖
  `replay`）。若请求的 seq 早于缓冲最老事件，会先发一条
  `: history-truncated` 注释帧，客户端应视为「可能有缺口」。
- **背压/资源（G72）**：并发 SSE 订阅默认上限 16，超限返回 `503` +
  `Retry-After: 5`；单连接写超时 30s；慢订阅者队列满时丢最旧事件并在
  `stats.subscribers_detail[].dropped` 计数。
- **重连**：服务端首帧发送 `retry: 3000`；EventSource 按 3s 重连。
- **协议**：HTTP/1.0 + `Connection: close`，读到连接关闭为止（没有
  Content-Length，也不是 chunked）。`curl -N` / EventSource 均可直接消费。
- **打码**：事件在入总线前已过 `redact()`（URL userinfo、Authorization、
  PRIVATE-TOKEN、query token 参数、高置信 token 前缀）。**仍不要**把事件流
  原样外发到公网。
- **无鉴权**：与其它端点一致，仅绑定回环 + Host/Origin 校验；SSE 是只读流。

事件类型：

| `event:` | 关键字段 | 说明 |
|----------|----------|------|
| `batch_started` | `batch_id,total,root,fetch_depth,unshallow,mirror,concurrency` | 一批开始（每个 `launch` 一个 batch_id） |
| `task_queued` | `index,key,folder_name,display,url,host` | 任务进入调度（含 >64 的待发队列） |
| `task_started` | 同上 | worker 真正开始 |
| `task_progress` | `index,key,percent,detail` | 100ms 心跳批量发布（可能缺省 percent/detail） |
| `task_result` | `index,key,status,action,message,detail,commits,duration_ms,path,head_sha,remote_sha,mirror_used,degrade_path` | 终态结果（后两个为 **G81 新增加性字段**，`""` = 直连源站 / 常规克隆；老消费者按多余键忽略） |
| `task_error` | `category,code,title,cause,action,retryable,http_status,raw` | 失败时的三段式结构化错误（§3 之外的新 schema） |
| `retry_queued` | `key,folder_name,attempts,next_retry_at,category,code,title` | 进入自动重试队列 |
| `retry_dropped` | 同上 + `reason` | 超过最大重试次数而放弃 |
| `retry_started` | `count,keys[]` | 引擎到期重投 |
| `batch_finished` | `total,cancelled,counts,state,net_fail` | 一批结束（`state` 含状态机计数） |
| `system` | `message` | 预留的系统消息 |

错误 schema 扩展：`task_error` 的 `category` 取值
`network|auth|platform|local|not_found|cancelled|other`；`retryable=true`
仅对网络/限流/5xx 置真（401/403/404、磁盘满、平台限制均为 false）。

`GET /api/events/snapshot?limit=100&types=task_error` 返回（默认**不含**
`task_progress`；需要进度时显式传 `types=task_progress`）：

```json
{"events": [{"type": "task_error", "seq": 7, "ts": 1760000000.0, "...": "..."}],
 "count": 1, "stats": {"subscribers": 0, "buffered": 7, "published": 7, "dropped": 0, "closed": false}}
```

## 6. 退出码总表

| 场景 | 码 |
|------|----|
| CLI 全部成功 | 0 |
| CLI 有失败 | 1 |
| CLI 无有效输入 | 2 |
| MCP 未启用（`GCM_MCP_ENABLE != 1`） | 3 |

## 7. 只读 / 写边界总表

| 入口 | 读 | 写 | 放行条件 |
|------|----|----|----------|
| CLI --json | 仓库地址 / 本地仓 | 克隆写盘 | 调用本身即写意图 |
| MCP | repos.db 查询 | 无 | 拒绝一切写工具；报表需 `--allow-export` |
| HTTP API | 查询/状态/报表 | 克隆（POST） | `--allow-mutate` |

## 8. 调用示例（agent 视角）

```bash
# 1) CLI 批量克隆 + JSON 输出（stdout 即 JSON）
python -m gcm --cli --json --dir ./clones ./urls.txt
# → exit 0/1/2；stdout 为 §2 schema

# 2) MCP 注册（Claude Desktop / Cursor）
# python -m gcm.mcp_server   （需 GCM_MCP_ENABLE=1）

# 3) 本地 HTTP API（只读）
python -m gcm --cli --serve 127.0.0.1:8765
curl http://127.0.0.1:8765/api/status
# POST /api/clone → 403（只读模式）
```

## 9. 验收对照（test_g56_*）

- `tests/test_g56_cli_json.py`：2 裸仓 E2E 断言顶层 schema / counts / results / stdout 纯净 / 失效与无效行 / 打码。
- `tests/test_g56_mcp.py`：stdio 三跳握手 + `list_repos` 只读 + 默认禁用退出码 3 + 报表未放行不写盘。
- `tests/test_g56_contract.py`：契约文档与实现抽样断言 + HTTP API 只读边界冒烟。
- 运行命令：`.venv\Scripts\python.exe -m unittest tests.test_g56_cli_json tests.test_g56_mcp tests.test_g56_contract -v`

## 10. 工具风险分级与治理（G83）

本节引入标准化的治理词汇（对齐 OWASP MCP Governance 的 Tier 分级与
mcpproxy 的隔离区模型），让 agent 生态的接入方一眼看懂本工具的写保护姿态。

### 10.1 工具 Tier 分级

| Tier | 定义 | 本工具对应能力 |
|------|------|----------------|
| Tier 0 | 纯读，无副作用 | 仓库查询 / 状态 / 报表（MCP `list_repos`、HTTP GET） |
| Tier 1 | 导出，写本地文件 | 报表导出（需 `--allow-export`） |
| Tier 2 | 写本地数据 | 克隆 / 更新写盘（CLI 本身即写意图；HTTP POST 需 `--allow-mutate`） |

**本工具不提供 Tier 3+（远程副作用 / 不可逆操作）能力，未来新增也禁止绕过本表。**

### 10.2 放行即审批（quarantine 语义）

- 一切写能力**默认处于"隔离区"**：不设环境变量 / 不传开关时，
  写端点返回 403、MCP 拒绝导出、退出码语义不变。
- "放行"是**显式、每次启动、按入口**的：`--allow-mutate` / `--allow-export` /
  `GCM_MCP_ENABLE=1` 三者互不隐含。没有"一次放行、永久生效"的路径。
- 放行后的写操作**必须审计**（事件入总线前 redact，app.log 落盘打码）——
  治理与可观测绑定，不允许"放行但无声"。HTTP 落 `<data>/.gcm-api-audit.jsonl`，
  MCP 落 `<data>/.gcm-mcp-audit.jsonl`（Rev16 A7；`clone_batch` 的 `dry_run`
  预览不审计，未放行的拒绝也不审计）。

### 10.3 接入方自查清单（agent 接入前过一遍）

1. 你需要的最低 Tier 是什么？只用 Tier 0 就不要请求放行。
2. 放行环境变量写在谁那里？进程级而非用户级，避免泄漏到其它会话。
3. 读取的仓库内容是否会被你外发？本工具的 redact 只保护**本工具的出站**，
   agent 消费后的外发责任在接入方。
