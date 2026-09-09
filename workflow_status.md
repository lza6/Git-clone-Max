# 任务推进日志（主控代理）— Git-clone-Max 升级交付

> 只记录事实与证据。

## v4.0.0 并发架构升级（本次交付）

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