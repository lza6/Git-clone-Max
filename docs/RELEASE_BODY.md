# Git-clone-Max v{version} 发布说明

<!--
维护者注（**不会出现在 Release 页面**：HTML 注释在渲染时被隐藏）：
本文件是发布模板，由 `scripts/publish_release.py` 读取、替换其中的版本号与
两个产物 sha256 后，作为 GitHub Release body 发布。
「本版重点」一节自动取自 `gcm/__init__.py` 的 `__changelog__` 对应版本条目 ——
每次 bump 版本时补一行 changelog，Release 说明就会自动跟上，不必手改本文。

G69-18：原先这段是**裸引用块**，会原样出现在公开的 Release 说明最上方，
读者第一眼看到的是「本文件是发布模板…」这种内部维护说明。
改用 HTML 注释后对维护者仍可见（源码里），对读者隐藏。
-->

## 本版重点

{highlights}

## 能力总览

- **批量并行下载**：≤32 并发且同一仓库自动去重、进度断点续传、**增量更新**、**冲突保护**（本地改动永不覆盖）、单任务暂停、取消缓存、任务清单持久化、批量打标签、导出所选 CSV、失败不中断。
- **多平台**：GitHub / GitLab / Gitee / Codeberg / Bitbucket / 自建主机；HTTPS + SSH + 短格式 + 子组 + `@tag` 指定版本 + 镜像前缀；目录命名 `host__owner__repo`。
- **网络与认证**：按 host 注入凭据（可逐 host 选择 `Bearer` / `PRIVATE-TOKEN` / `Basic`）、token 逐条目加密落盘、远端可达性预检、单分支浅克隆、弱网降级链（treeless → depth1）、强制 HTTP/1.1、自定义主机白名单、下载限速。
- **数据与洞察**：统计中心（全局 + host 分布 + 30 天趋势 + 用量）、多维评分卡、阈值告警（可推送到钉钉/飞书/企微）、定时报表、仓库备注与标签、远端检查、历史时间线。
- **可靠性**：崩溃/退出后可续跑（跨会话任务清单）、自动重试队列（网络类退避）、看门狗、进度文件滚动治理、单实例锁、全局异常兜底。
- **UI/UX**：多主题（含高对比）+ 跟随系统深浅色、首启向导与引导清单、设置搜索、窗口尺寸记忆、全局热键、开机自启（可选最小化到托盘）、完成通知。
- **易用性**：轻量 i18n（中文 / English）、环境与网络一键体检、排错地图、数据目录策略（`--portable` / 默认 `%APPDATA%`）。
- **Agent / 脚本接入**（统一契约见仓库 `docs/agent-contract.md`）：
  - CLI：`python -m gcm --cli [--json] [--diag] [--serve host:port]`
  - MCP server：`GCM_MCP_ENABLE=1 python -m gcm.mcp_server`（默认只读）
  - 本地 HTTP API：`--serve`（默认只读、**强制回环绑定**）
  - 技能包：仓库 `skills/` 下 6 个（clone / archive / report / diag / resume / feature）
- **工程**：ruff 零 error / mypy 零 error / 覆盖率门禁 / 真实 git E2E（本地裸仓，不依赖外网）/ 双产物 sha256 校验 / 契约与安全守卫测试。

## 校验信息

- onefile exe sha256：`{sha256_exe}`
- portable zip sha256：`{sha256_zip}`

## 使用

- **便携版**：下载 `Git-clone-Max-portable.zip` 解压后双击 `Git-clone-Max.exe`（启动更快、SmartScreen 拦截率更低）。
- **单文件版**：下载 `Git-clone-Max.exe` 双击即用。
- **源码运行 / 打包 / 测试**：见仓库 README「快速开始」「重新打包 exe」「测试」各节。

## 安全边界（重要）

- 本地 HTTP API **无鉴权**，因此只允许绑定回环地址（`127.0.0.1` / `127.0.0.2` / `127.0.0.3` / `localhost`）。
  传入 `0.0.0.0` 等非回环地址会被拒绝并以退出码 2 退出。需要远程访问请自备带鉴权的反向代理。
  （IPv6（含 `::1`）暂不支持：本地 API 基于 AF_INET 实现，传入会明确报错而非崩溃。）
- MCP server 与 HTTP API **默认只读**，写操作需显式放行（`GCM_MCP_ENABLE=1` + `--allow-export` /
  `--allow-mutate`）。
- 所有面向 UI / 日志 / agent 的输出都会对 URL 内嵌凭据、授权头、URL query 里的 token 打码。

## 已知限制与反馈

- 更新检查只发现新版并提示跳转下载，不做自动覆盖安装。
- 弱网下降级链可能在提示后才触发，极端弱网建议手动切浅克隆。
- 冷门自建主机的 URL 需先加入「常用主机」白名单才能解析。
- 克隆后钩子按 shell 语义执行（保留重定向/管道能力）；敏感场景请只对自己写的钩子启用。
- 问题反馈：https://github.com/lza6/Git-clone-Max/issues
