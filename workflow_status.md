# 任务推进日志（主控代理）— Git-clone-Max 升级交付

> 每节点落地即记录，只记事实与证据。

## 契约

- 交付物：`Git-clone-Max/` 生产级工程 + bat/ps1 双启动器 + 单测/E2E + push 到 `lza6/Git-clone-Max` + 创建 Release
- 命名规则：克隆目录一律 `作者__仓库`（如 `lza6__Git-clone-Max`）
- 核心行为：已存在→`git fetch` 增量更新；存在本地冲突→**不覆盖**，标记并报告
- 数据库：`repos.db`（SQLite）记录仓库元数据与每次同步快照
- 断点续传：进度写入 `progress.json`，中断恢复；已 clone 则后续为增量同步

## 任务图

```
N0 预检 ──► N1 骨架 ──► N2 核心(URL/DB/git服务/stream) ──► N3 UI ──► N4 启动器 ──► N5 测试 ──► N6 迁移 ──► N7 审查 ──► N8 发布
   (并行入口: N2 各文件可并行)                     (N4 独立可并行)      (N6 依赖 N5)
```

## 验证日志

| 时间 | 节点 | 动作 | 结果 |
|------|------|------|------|
| 00:16 | N0 | gh CLI 不存在，远端库未知 | 改用 git ls-remote；TLS 握手失败需网络重试 |
| 00:16 | N1 | 创建工程骨架目录 | 成功 |

## 阻塞项

- N0: 远端网络 TLS 握手暂时失败（可能网络代理/防火墙），push/release 节点将重试；本地功能不依赖此步。
- N0: `gh` CLI 未安装——如发布阶段需要，先尝试 `winget install GitHub.cli`；否则改用 git push + git tag。

## 下一步

- 写 N2：`gcm/app/` 各模块 + `gcm/db/schema.sql`