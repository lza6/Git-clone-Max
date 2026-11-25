# Agent 五分钟接入（CLI / MCP / HTTP-SSE）

> Rev21 P21-3。面向要把 Git-clone-Max 接进自动化或 agent 的使用者：
> 三段各含命令、期望输出、退出码语义。全部命令可原地复跑，
> 复跑证据脚本 `scripts/realcalls_quickstart.py`（产出 `evidence/rev21-quickstart.json`）。
> 完整契约（--json schema / 打码规则 / MCP 工具表）见 `docs/agent-contract.md`。

前置：源码根目录下执行，解释器一律 `.venv/Scripts/python.exe`（Windows）或 `.venv/bin/python`。
所有示例都用 `GCM_DATA_DIR` 指向临时目录，不碰你真实的数据目录。

## 1. CLI：先预览再执行

```bash
GCM_DATA_DIR=/tmp/gcm-demo ./.venv/Scripts/python.exe -m gcm --cli --json --dry-run - <<'EOF'
https://github.com/octocat/Hello-World
EOF
```

期望：stdout 是**单个** JSON 文档，含 `"mode": "dry_run"` 与 `"plan"`（将克隆/将更新/目录被占/重复/无效六档）。
`--dry-run` 不联网、不写盘。去掉 `--dry-run` 才是真实克隆。

退出码：`0` 成功（dry-run 表示"预览算出来了"）/ `1` 有失败（failed/conflict/cancelled 任一）/
`2` 无有效输入或参数错误。`--json` 模式下 stdout 只有 JSON，文本进度走 stderr。

## 2. MCP stdio：握手 + 工具列表

```bash
GCM_MCP_ENABLE=1 GCM_DATA_DIR=/tmp/gcm-demo ./.venv/Scripts/python.exe -m gcm.mcp_server
```

向 stdin 逐行写入（换行分隔的 JSON-RPC）：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"demo","version":"1.0"}}}
{"jsonrpc":"2.0","method":"notifications/initialized"}
{"jsonrpc":"2.0","id":2,"method":"tools/list"}
```

期望：stdout 返回两条响应（`id:1` 含 serverInfo；`id:2` 列出只读工具，
写工具仅在 `--allow-mutate` 时出现）。写操作（`clone_batch` 非 dry_run、`job_cancel`）
需要 `GCM_MCP_ENABLE=1` + `--allow-mutate` 双闸。

退出码：未设置 `GCM_MCP_ENABLE=1` 时进程以 `3` 退出（与 `docs/agent-contract.md` 一致）。

## 3. 本地 HTTP API + SSE 事件流

```bash
GCM_DATA_DIR=/tmp/gcm-demo ./.venv/Scripts/python.exe -m gcm --cli --serve 127.0.0.1:8765
```

另一个终端：

```bash
curl http://127.0.0.1:8765/version
curl -N http://127.0.0.1:8765/api/events
```

期望：`/version` 返回 JSON（含 `version`）；`/api/events` 是 SSE 长连接，
先收到 `retry:` 帧，随后按事件推送。断线重连时带 `Last-Event-ID` 头可精确续传
（`/api/events/snapshot` 供轮询型 agent）。服务只绑回环，无鉴权，**不要**经反代暴露公网。

退出码：`--serve` 正常退出为 `0`；端口占用等启动失败为 `2`。

## 复跑证据

```bash
./.venv/Scripts/python.exe scripts/realcalls_quickstart.py
```

三段全部真实执行并把命令、退出码、输出尾部写入 `evidence/rev21-quickstart.json`；
任一段失败脚本以非 0 退出。
