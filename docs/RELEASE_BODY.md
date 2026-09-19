# Git-clone-Max v{version} 发布说明

> 本文件为发布模板，由 `scripts/publish_release.py` 读取并替换 `{version}` 占位符后作为 GitHub Release body。`sha256` 行由发布脚本在 Release 时写入。

## 版本亮点

- **网络与认证**：按平台 host 注入凭据（github→Bearer / gitlab→PRIVATE-TOKEN，host_tokens 逐条目加密落盘）、镜像前缀、远端可达性 10s 预检、单分支浅克隆、弱网降级链（treeless → depth1 保命）、强制 HTTP/1.1、自定义主机白名单。
- **批量并行下载**：≤32 并发去重、进度断点续传、**增量更新**、**冲突保护**、单任务暂停、取消缓存、任务清单持久化、批量打标签、导出所选 CSV、失败不中断 + 网络类自动降级。
- **多平台**：GitHub / GitLab / Gitee / Codeberg / Bitbucket / 自建主机；短格式 / 子组 / @tag / 镜像前缀。
- **数据洞察**：统计中心（全局 + host 分布 + 30 天趋势 + 用量）、报表筛选导出（CSV/Markdown）、每仓库备注、远端检查。
- **UI/UX**：三套主题 + 跟随系统深浅色、首启三步向导、状态符号、标准图标体系、完成动效、全局热键、设置搜索、窗口尺寸记忆。
- **生态与体验**：轻量 i18n（中文/English，重启生效）· 数据目录策略（`--portable` / 默认 %APPDATA% / 旧 data 沿用）· CLI 批量克隆（`python -m gcm --cli`，离屏复用引擎）· uv 现代环境 · 开机自启真实写入任务计划 + 启动最小化到托盘（`--minimized`）· 完成通知 Webhook（显式开启+URL，payload 打码）· Inno 安装器脚本 · 打开数据目录入口。
- **性能（v8.0.2 主打）**：本地扫描引入仓库重构——干掉每仓 2 个 git 子进程（改纯文件读 remote/HEAD）、并发枚举跳 junction、O(1) 已导入预载、item checkState 渲染、单事务批量写库、进度+取消；实测 300 仓从 149.4s→1.6s（≈95× 提速），且补全裸仓/worktree 识别。
- **工程**：ruff 零 error / mypy 通过 / 覆盖率门禁 / Release 双产物 sha256 校验。

## 校验信息

- onefile exe sha256：`{sha256_exe}`
- portable zip sha256：`{sha256_zip}`

## 使用

双击 `Git-clone-Max.exe` 即可；源码运行见仓库 README「快速开始」。

## 已知限制与反馈

- 更新检查只发现新版并提示跳转下载，不做自动覆盖安装。
- 弱网下降级链可能在提示后才触发，极端弱网建议手动切浅克隆。
- 问题反馈：https://github.com/lza6/Git-clone-Max/issues