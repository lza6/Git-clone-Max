# 已验证范围（verified-scope）

## 本轮（2026-10-17，v9.4.5 / v9.4.6）真实验证

| 项 | 命令/方式 | 结果 |
|----|----------|------|
| CI 三平台 | GitHub Actions | **7/7 job 全绿**（含 windows/ubuntu/macos），**且已移除 GC 安全网** |
| 引用环 | `show()` + `del w` + `weakref` | 立即按引用计数释放；`CLOSURES_CAPTURING_WINDOW: []` |
| Webhook 幂等 | 40 线程 Barrier 并发 | 发送 **1** 次（修复前 13 次） |
| 审计并发稳定性 | 连跑 30 次 | 30/30 绿（修复前 25 次挂 3 次） |
| 审计防泄漏 | 3 类真实 URL 形态 | `?k=SECRET` / `/<KEY>.send` / `/bot<TOKEN>/` **均不落盘** |
| 引导清单 | 真窗口 + DB 写入 | `import` 项 False→True；只提示一次 |
| 输入区校验 | 真窗口 | 「有效 3/无效 0」「有效 1/无效 1：not-a-url」 |
| 打包 | PyInstaller + health_check | `version=9.4.6` · `started_ok=true` · `artifacts_exist=true` |
| Release | `publish_release.py` | v9.4.5 / v9.4.6 双产物 + 服务端 sha256 一致 |
| tag 链 | 逐个校验 | v9.4.0~v9.4.6 全部与 `__version__` 一致 |

## 未能验证（外部条件）

| 项 | 原因 | 当前做到 |
|----|------|---------|
| 真实 Webhook 投递 | 需用户自己的 Server酱/TG 地址 | 全 mock；行为（幂等/审计/重试/防泄漏）已真实测 |
| macOS/Linux 桌面 GUI 人眼点检 | 只有 Windows 实机 | CI 离屏跑通 + exe 真启动健康检查 |
| 生产"热更新" | 本项目是**桌面单机工具**，无服务端 | 等价动作：打包 → 健康检查 → GitHub Release |
