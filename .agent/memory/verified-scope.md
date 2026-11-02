---
topic: verified-scope
updated: 2026-11-03
owner: Verifier
---
# 已验证范围（verified-scope）

## 最新（2026-11-01，v9.12.0 / Rev7 C1 批与终局收口）

| 项 | 命令/方式 | 结果 |
|----|----------|------|
| 静态门禁 | `ruff check gcm` / `mypy gcm` | All checks passed / Success（104 files） |
| 快集全量 | `coverage run --source=gcm scripts/ci_run_fast.py` | **Ran 1728 · 0 失败 · 0 错误 · 4 skipped**（149 模块，exit 0） |
| 覆盖率 | `coverage report --include="gcm/*"` | **83%**（门禁 Win 81 / POSIX 80） |
| 文档漂移 | `python scripts/doc_drift_check.py` | **DRIFT 0**（首跑报 18 处真实漂移，已全修） |
| 度量 | `python scripts/dev_metrics.py` | 105 文件 / 27,918 行 / 超 800 行 3 / 超 100 行函数 20 / except 622·pass 282 / tests 162 |
| 真实 git E2E | `python scripts/e2e_smoke.py` | `E2E_SMOKE_RESULT: PASS` |
| 真实 CLI 克隆 | `python -m gcm --cli --json --dir <tmp>` | exit 0 · `action=cloned` · `mirror_used=gh-proxy.com` · 非法项 `code=2` |
| HTTP API | `--serve 127.0.0.1:8901` + 5 GET | 全 200 · **非回环绑定 exit 2**（安全边界未破） |
| MCP | `GCM_MCP_ENABLE=1` initialize + tools/list | `serverInfo 9.12.0` · 4 个只读工具 · 未启用 exit 3 |
| UI 截图 | `scripts/ui_screenshot_audit.py` | 34 PNG · 新增设置两行 `visibleRegion == rect` |
| 打包 | PyInstaller → portable zip → health_check | exe 41,677,729 B · zip 80,311,847 B · **health 7/7 OK** |
| 打包 exe E2E | `dist/Git-clone-Max.exe --cli --json` | exit 0 · 真克隆 |
| CI 三平台 | 提交 `6a10fcc` / `ae07a22` | **7/7 job success**（test×3 + slow + 2×smoke + perf） |
| Release 复验 | `gh release download v9.12.0` + sha256 + 真跑 | 两份资产逐字节一致 · 下载版 exe exit 0 |
| 跨重启闭环 | 改设置 → 关窗 → 新建 MainWindow | 引擎读到新值（11 / 1200s）、闸门容量 11、空闲引擎无重投队列 |
| 计划文档抽查 | AST 25 条 + 运行期 80 例 | **25/25 通过**（首版文本匹配误报 1 条，改 AST 后 PASS） |

**未验证**：Linux/macOS GUI、真机 125%/150% DPI 目检、慢速网络长时间挂机、C2–C6 全部 27 项。

---


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

## G71（2026-10-19）
- EventBus 发布/订阅/回放/过滤/打码/慢订阅者丢弃：`tests.test_g71_event_stream.TestEventBus`。
- SSE 帧/心跳/类型过滤/断连清理/不占 DB：`TestSseEndpoint`。
- 错误分类与重试判定、重试面板禁用态：`TestErrorModel/TestRetryQueueVisibility/TestRetryPanelRows`。
- 原子写并发/无 tmp 残留：`TestAtomicWrites`。
- 诊断包内容与 userprofile 打码：`TestDiagBundle`。
- GUI 本地 API 启停与 `/version`：`TestGuiHostsLocalApi`。
- 真实 git E2E：`scripts/e2e_smoke.py` → `E2E_SMOKE_RESULT: PASS`。
- 快集全量：1316 tests / 0 failures / 0 errors / skipped 3 / 覆盖 81%。
- 构建：PyInstaller EXIT=0；portable zip 314 条目 / 79,482,799 B；健康检查 started_ok=true、version_match=9.8.0。
- 打包 exe E2E：首次克隆 `action=cloned` + 文件内容校验；第二次 `action=updated (+1)` + 内容 v2。
- CI：commit `33f5e64` run `39238030986` **7/7 job success**（windows/ubuntu/macos + e2e/fast/slow/perf）。
- Release：`v9.8.0` 创建成功；exe/zip 上传；服务端 sha256 与本地一致。

## G72（2026-10-19）
- app.log 落盘打码：`tests.test_g72_hardening.TestLogRedaction`。
- 路径变体/token 前缀打码：`TestRedactG72`。
- 原子写重试/清理：`TestAtomicHardening`；诊断包并发/截断/manifest：`TestDiagHardening`。
- 重试 jitter/批量上限：`TestRetryJitter`。
- SSE Last-Event-ID/503 并发上限：`TestSseG72`；订阅者指标：`TestEventBusG72`。

- G72 发布：commit `616fd73`；tag `v9.8.1`；CI run `39291521568` 7/7 success；Release id `417540264`，双产物 sha256 一致。

## G73（2026-10-20）
- DLQ 字段保留与重新入队：`tests.test_g73_dlq.TestDlqQueue`。
- 面板 DLQ 切换/单条 redrive：`TestDlqPanel`。

## 2026-11-03 G83 补充验收（v9.17.0 发布后深验）

| 项 | 方式 | 结果 |
|---|---|---|
| UI 截图审计 | `scripts/ui_screenshot_audit.py`（34 张：4 主题×5 尺寸+8 对话框） | 无 tofu/裁切；3 条 MINOR（向导「上一步」未禁用/事件流摘要三段拼接/已跳过行残留进度条）→ beads 留档 |
| MCP stdio E2E | 真实三跳握手 + tools/list + tools/call | HANDSHAKE_OK；4 工具；未启用 exit 3 契约 |
| SSE E2E + 压测 | 真实订阅 + 18 并发 | Last-Event-ID/replay 200；16 上限精确 503 |
| 自适应间隔全周期 | 真实 MainWindow 5 轮 | 2→3→5→7 退避曲线 + 变更恢复 2 · FULL_CYCLE_OK |
| 对抗审查 | 迟到 finished/空库 tick/引擎替换 | 语义正确，无误结算 |
