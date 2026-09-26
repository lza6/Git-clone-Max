# 任务推进日志（主控代理）— Git-clone-Max 升级交付

> 只记录事实与证据。

## 参考库扫描 · 优点提炼 · 差距分析 · 优化规划（本次会话）

| 节点 | 状态 | 证据 |
|------|------|------|
| 参考根全量盘存 | ✅ | `D:\参考项目` 共 **1178 个项目**（`计划书/参考扫描证据/_inventory.txt`） |
| 自动聚类（两轮） | ✅ | 15+ 聚类（dev_tools 228 / agent_skill 201 / web_scraper_download 177 / agent_framework 131 / video 74 / mcp 71 / security 54 / ppt_docs 39 / image_gen 26 / ecommerce_ads 18 等；`_clusters2.json`） |
| 同类筛选 + 56 项深读 | ✅ | 短名单 README 摘要（`_shortlist_readme.json`、`_deep2.md`）；直接同行：Website-downloader / web-check / 9router-Max / VideoHub / xl-converter / zvec-grep |
| 优化方案设计 | ✅ | `参考结果计划指南.md`（P0/P1/P2 分层 + 验收 + 决策点） |
| 实施 | ⏳ **待用户确认** | P0-1 一键体检 / P0-2 Webhook 审计台账 / P0-3 CLI --json 待授权后按节点实施+验收 |

> 本节点为只读分析规划，未改动主项目/参考项目任何代码；实施需用户确认后进入 P0 节点闭环。

# 任务推进日志（主控代理）— Git-clone-Max 升级交付

> 只记录事实与证据。

## v4.1.0 增量交付

| 节点 | 状态 | 关键证据 |
|------|------|----------|
| V4.1-1 仓库详情对话框 | ✅ | `gcm/ui/repo_detail_dialog.py`（137 行）；`tests/test_repo_detail.py` 4 用例全绿 |
| V4.1-2 GitHub Actions CI | ✅ | `.github/workflows/ci.yml`（windows-latest · py3.12 · pwsh offscreen 全量 + coverage）；PyYAML 复验 OK |
| V4.1-3 README 多平台文档 | ✅ | 插入「多平台运行（Linux/macOS 源码）」小节，仅 +18 行 |
| V4.1-4 管理页交互测试补覆盖 | ✅ | `tests/test_manage_ui.py` 20 用例（choose_target/open_target/check_update/delete_selected/import/show_history 等），发现无生产 bug |
| V4.1-5 全量测试 | ✅ | `Ran 137 tests OK`（discover，含 launcher） |
| V4.1-6 覆盖率 | ✅ | 核心 76%：engine 92% / scanner 89% / url_lib 98% / worker 88% / repo_db 89% / settings 96% / repo_detail 100% / main_window 77%↑ |
| V4.1-7 打包+发布 | ✅ | commit `ccc2362` · tag `v4.1.0` 已推送 · Release 附件 98,081,107 bytes · 独立下载复验 sha256 `a9328c74…9d8dda` 与本地产物一致 |
| V4.1-8 Release 页面 | ✅ | https://github.com/lza6/Git-clone-Max/releases/tag/v4.1.0 |

## v4.0.0 并发架构升级（上一交付）

| 节点 | 状态 | 关键证据 |
|------|------|----------|
| V4-1 新增 `gcm/app/engine.py` SyncEngine | ✅ | 统一调度：去重 / 并发 1–32 / 进度周期落盘 / 取消缓存 / finished 收敛 |
| V4-2 `repo_db.py` 并发安全 | ✅ | `busy_timeout=30000`；`_progress_guard` 线程+进程双层锁（msvcrt/fcntl）|
| V4-3 `service.py` 失败分类 + 平台限制不重试 | ✅ | `_classify_failure`（invalid path / file exists / unable to checkout）+ `_is_networkish_error` 加入 platform 类；clone 失败路径分类；rebase abort 兜底 |
| V4-4 `main_window.py` 接入 engine | ✅ | `_launch`/`update_all` 统一走 engine；`_save_concurrency` 生效；并发 SpinBox 1–32；closeEvent 用 engine.shutdown |
| V4-5 并发 E2E 测试 `tests/test_engine.py` | ✅ | 批量 8 / 并发 32 / 去重 / 失败不中断 / 断点续传 / 文件锁并发写 / 平台错误不重试（8 用例）|
| V4-6 版本 bump 4.0.0 + README 同步 | ✅ | `__version__=4.0.0`；README 并发描述与目录结构更新 |
| V4-7 全量测试 | ✅ | `Ran 113 tests OK`（含真实 git E2E、并发引擎、launcher）|
| V4-8 覆盖率 | ✅ | `TOTAL 78%`（剔除 theme/tray/token/updater 外部交互模块；核心 engine 92% / scanner 89% / repo_db 89% / worker 88%）|

**本次修复的根因**
1. progress.json 共用 tmp 路径并发写 → PermissionError → finished 信号丢失 → UI 永久 busy（闪退表象）→ 引擎统一落盘 + 双层文件锁。
2. SQLite 无 busy_timeout → `database is locked` → busy_timeout=30000。
3. 同 `folder_name` 无去重 → 两 worker 抢目录（layerfs 日志实证）→ 引擎按目标目录去重。
4. 并发上限硬编码 16 与设置脱节 → 引擎收敛 + SpinBox 1–32。
5. layerfs 冒号文件名 = Windows 平台限制 → 失败分类明确提示 + 不重试。
6. `QSystemTrayIcon.isSystemTrayAvailable()` 在 offscreen 触发访问违规 → 提前返回 False（测试环境）。

## 剩余（后续里程碑，非本次范围）
- `local_repos_dialog.py` / `scan_worker.py` 交互弹窗覆盖率偏低（13% / 21%，属 UI 富交互路径，测试以 mock 为主）
- `__main__.py` 54%（进程入口已冒烟覆盖）
- publish_release 的 Release body 文案未随版本迭代（BODY 常量仍为 v2 描述），建议后续维护
- CI 真实运行需在 GitHub Actions 环境验证（本机已 PyYAML 校验语法）

## 交付状态（历史基线）

| 节点 | 状态 | 关键证据 |
|------|------|----------|
| N0 预检 | ✅ | gh 未装改用 git；远端仓库已存在（main 首个提交 abc3ea0）|
| N1 骨架 | ✅ | `Git-clone-Max/` 多模块工程建立 |
| N2 核心 | ✅ | url_lib / repo_db / service / worker；单测 13 项通过 |
| N3 UI | ✅ | 三 Tab 主窗口离屏冒烟 `SMOKE_UI_OK` |
| N4 启动器 | ✅ | bat CRLF 修复；测试 `test_bat_reaches_launch ok` |
| N5 测试 | ✅ | 真实 git E2E（clone/update/fetched/conflict/broken-dir）全过 |
| N6 迁移 | ⏭️ | 旧 1.2G 存量保持原目录未迁移结构（已授权为最终产品形态）|
| N7 审查 | ✅ | code-reviewer/security-reviewer 审查通过 |
| N8 发布 | ✅ | `main` + `v1.0.0` 已推送到 `github.com/lza6/Git-clone-Max` |
| N9 v2.0 迭代 | ✅ | P0 全部 + P1 全部 + P2 主要项 落地闭环；`gcm/__init__.py` 版本 bump → 2.0.0 |
| N10 最终验收 | ✅ | 74 测试全绿；覆盖率 75%（含 UI/tray 未覆盖部分）；无 ResourceWarning；发布脚本 dry-run 通过 |

## v2.0 迭代明细（N9 落地内容）

- **P0-1 输入校验不静默丢行**：`parse_urls` 批量解析；无效行弹窗提示（用户可选忽略/返回）。
- **P0-2 全局超时 + HTTP 代理**：`GitService` 支持 `fetch_timeout/clone_timeout/proxy/retries/backoff`；`run_git_ui` 读线程 + `proc.wait(timeout)` 双保险，静默挂死可按时触发。
- **P0-3 进程树彻底取消**：`CREATE_NEW_PROCESS_GROUP` + `taskkill /T /F`；`_kill_tree` 单测覆盖真实进程。
- **P1-1 断网自动重试**：网络类错误退避重试（默认 retries=2）；仓库类错误（404/认证）不重试；`_is_networkish_error` 分类单测 8 项。
- **P1-2 配置持久化**：`gcm/db/settings.py`（并发/超时/重试/代理/自动清空/托盘）；损坏兜底 + 类型强转。
- **P1-3 并发动态可调**：自建 `QThreadPool(self)`，不再共享全局实例；设置 Tab 实时调整。
- **P1-4 发布脚本固化**：`publish_release.py` 支持 `--dry-run` / `--tag`；`requirements-build.txt` 声明 PyGithub+PyInstaller。
- **P1-5 UI 自动化测试**：`tests/test_ui.py` + `tests/test_extra.py`（QtTest 离屏）。
- **P1-6 冲突语义统一**：`_divergence_info(ahead, behind)` 替换死代码 `_branch_ahead`。
- **P2-1 空仓库优雅处理**：`SyncAction.EMPTY`；UI 显示"空仓库"。
- **P2-3 一键更新跨目录**：按 `local_path` 反推多根目录逐组继任（`_multi_root_relay` + `_pending_count` 显式计数）。
- **P2-4 自更新**：`gcm/app/updater.py` 版本比对 + 设置 Tab "检查更新"按钮。
- **P2-5 系统托盘**：`gcm/ui/tray.py`（无托盘环境自动降级，不崩）。
- **P2-6 .gitattributes**：统一 EOL（bat/ps1 强制 CRLF，防 LF 崩溃复发）。

## 关键验证日志（v2.0 最终）

- 全量测试：`Ran 74 tests ... OK`（core 38 + extra 26 + updater 5 + ui 5 + launcher 1），无 ResourceWarning
- 覆盖率：`TOTAL 1322 stmts 75%`（`--source=gcm`；未覆盖集中在 UI 交互路径与托盘真实显示）
- 发布脚本 dry-run：`[DRY] 校验通过，未做任何修改`（产物 97911945 bytes，sha256 31094534…）
- UI 冒烟：主窗口 + 设置持久化（并发 4 重启恢复）+ 托盘降级，离屏全部通过

## 阻塞项与处理

- gh CLI 未安装 → 改用 git push + git tag，已完成
- bat 行尾 LF 导致 cmd 解析错误 → 落盘 CRLF 字节修复，测试通过
- 远端已有 Initial commit（含 README/LICENSE 冲突）→ 智能合并保留远端版权行 + 我方完整内容
- `run_git_ui` 首次实现重试循环漏 `break` → 成功路径重复 Popen（CRITICAL，审查发现）→ 读线程版重写 + 成功即 break 单测
- 超时判定仅在线程读取后 → 静默挂死不触发 → 读线程 + `proc.wait(timeout)` 双保险
- `LogModel.clear()` 触发 `appended(0)` 后 `_on_log_appended` 读空列表 → IndexError → 空列表守卫

## 剩余

- 无未解决 P0/P1
- P2-2 日志过滤 / P2-7 仓库详情页 / P3 多平台·CI·私有仓库·worktree·i18n 未做（按路线图下一里程碑）
- GitHub Actions CI（可选增强，未配置）


## M0 v7.3.1（G50 收口）— 2026-09-18 主控执行记录

| 节点 | 状态 | 关键证据 |
|------|------|----------|
| G50-1 测试隔离修复 | ✅ | `tests/test_updater_retry.py` 模块级 QApplication；单文件 Ran 3 OK / 30 OK 相邻 G38 测试 |
| G50-7 覆盖率补齐 | ✅ | 新增 29 用例；statistics_dialog 63%→100% · single_instance 59%→96% |
| G50-4 spec 入库 | ✅ | `.gitignore` 去 *.spec；`git ls-files` 含 Git-clone-Max.spec |
| G50-5 README 同步 | ✅ | 功能表 28 行能力矩阵（含 G38 网络设置） |
| G50-8 根目录收敛 | ✅ | workflow_status.md → 计划书/ |
| G50-9 RELEASE_BODY | ✅ | docs/RELEASE_BODY.md 模板（{version}/{sha256_exe}/{sha256_zip}） |
| G50-3 慢测定位 | ✅ | docs/慢测定位.md：全量 944s 基线 + Top10 慢文件 + 提速路径 |
| 全量回归 | ✅ | `Ran 590 tests in 944.035s OK`；TOTAL 83%（门禁 82）；ruff gcm 0 error；mypy 通过 |
| G38 真实 E2E | ✅ | 本地裸仓 file:// 12/12：满量克隆/浅克隆(is-shallow=true)/增量更新(+1)/@tag 精确检出/host_tokens 加密无明文/镜像/预检/凭据头/IPv4 |
| 版本 | ✅ | `__version__` 7.3.1 + changelog 追加（30→31 行） |

| 打包/发布 | ✅ | **v7.3.1 发布闭环完成**：tag 已 push · Release 已创建 · exe+zip 双产物上传成功（服务端 digest 与本地 sha256 全等）· 独立下载复验双 MATCH |


---

## v8.2.0 交付登记（2026-09-26，M10/M11/M14/M15）

| 里程碑 | 状态 | 关键证据 |
|--------|------|----------|
| M10 G53 数据洞察 | ✅ | alerts.py（评分卡/阈值告警/存储看板）+ sched_reports.py + repo_db 历史保留参数化 + statistics_dialog 评分卡/存储块；18+5 测试绿 |
| M11 G54 小白体验 | ✅ | diag.py 环境体检升级 + help_dialog 排错地图 + copy_paste_report；8 测试绿 |
| M14 G57 响应式 | ✅ | main_window 窗口语义档（S/M/D/XL）+ theme DPI 联动（offscreen 守卫）；6 测试绿 |
| M15 G58 网络深化 | ✅ | rate_limit.py（限流状态/退避）+ host_probe.py（HTTPS/Git 服务/认证头建议）；11 测试绿 |
| 全量验证 | ✅ | 分块全绿 660+ 用例；TOTAL 84%；ruff 0；mypy 0（59 files） |
| 发布 | ✅ | v8.2.0 Release 双产物上传，服务端 digest 与本地 sha256 全等 |

> 说明：v8.1.0（M8/M9/M12核心/M13核心）登记见上轮；本轮在 v8.1.0 基础上交付 M10/M11/M14/M15。
