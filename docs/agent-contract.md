# Git-clone-Max Agent 契约（G56-5）

> 适用版本：v9.10.2。本文档是所有 agent 接入点
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
  "version": "9.10.2",
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
| `invalid[]` | array | 无效输入行条目，见 §3 错误 schema |

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

## 4. MCP server（G56-2）

- 启用：`GCM_MCP_ENABLE=1` 后才启动（否则退出码 3，见 §6）。
- 数据目录：`GCM_DATA_DIR` 覆盖，默认 `<repo>/data`（repos.db）。
- 工具清单（`tools/list`，均为只读）：

| 工具 | 参数 | 返回 |
|------|------|------|
| `list_repos` | `host?` | `{repos: [...], count}`，行含 `owner/repo/host/folder_name/local_path` 及元数据，剔敏感列 |
| `search_repos` | `query` | 按 owner/repo/host/folder_name/tags/note 关键字过滤 |
| `status` | – | `{status: {...stats_overview}}` |
| `export_report` | `format: csv\|markdown`, `out_dir?` | 写产出文件（默认拒绝，需 `GCM_MCP_ENABLE=1` + `--allow-export`） |

- 退出码：`GCM_MCP_ENABLE` 未启用 → `3`（区别于 CLI 0/1/2）。

### 4.1 stdio 握手示例

```
→ {"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{}}}
← {"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-03-26","capabilities":{"tools":{"listChanged":false}},"serverInfo":{"name":"gcm","version":"9.10.2"}}}
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
| `task_result` | `index,key,status,action,message,detail,commits,duration_ms,path,head_sha,remote_sha` | 终态结果 |
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
