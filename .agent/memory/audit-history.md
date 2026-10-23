# 审计历史（audit-history）

| 日期 | 范围 | 结论 | 产出 |
|------|------|------|------|
| 2026-10-16 | G51–G59 台账复验 + 前后端接缝 + agent 契约 + 安全 | 台账「全部闭环」**不成立**；发现定时报表零调用点、告警无出口、9 个设置字段不可达、MCP cp1252 崩、数据目录单源违规 | `docs/reports/G69-18-*.html`、`docs/验证记录-2026-10-16.md` |
| 2026-10-17 上午 | CI 80+ 次红灯根因 | 5 类成因（无诊断 / 泄漏线程 / Qt 引用环 / MCP 编码 / CI 自身编码） | `docs/验证记录-2026-10-17.md` §3 |
| 2026-10-17 下午 | 引用环 + Webhook + 清单 + 输入校验 + 压测 | 引用环已根治（含 `show()` 路径），移除 GC 安全网；Webhook 并发竞态已修 | `docs/reports/G69-19-*.html` |
| 2026-10-17 晚 | 独立审查（v9.4.2–v9.4.4）→ 整改 → 复验 | 5 个阻塞项 **全 PASS**；复验另抓 7 项（审计无锁 / 键名写错 / 守卫仍不 show 等），已全部整改 | `docs/验证记录-2026-10-17.md` §11–§12 |
| 2026-10-18 | 独立审查（v9.5.0）+ 复验轮（G69-21） | 2 阻塞（凭据随 origin 泄漏给镜像、测速按钮 TypeError）+ 8 Required 全部修复；复验再提 3 HIGH 并修复；顺带修退出 0xC0000409 与 CLI 事件循环误报 | `evidence/N11-review-fixes.json`、`docs/验证记录-2026-10-17.md` §14、`reports/change-report-2026-10-18-security-review.html` |
| 2026-10-17/18 | 计划文档二次审计 + 独立盲点扫描（G69-20） | 计划台账：12 已闭环 / 2 未闭环 / 1 不适用，`计划书/` 归档后删除；盲点扫描 15 项「写了但用户够不到」，修 8 项、登记 7 项 | `docs/archive/plans/20261017/archive-manifest.md`、`reports/change-report-2026-10-17-plan-audit.html`、`docs/验证记录-2026-10-17.md` §12 |

## 审计方法沉淀

1. **对照实验**优于推理：本机绿 / CI 红 → 做干净检出。
2. **判定性守卫**优于「跑一遍没崩」：如「`del w` 后 `weakref` 是否已失效」。
3. **压测**能抓到单线程永远绿的问题（幂等竞态）。
4. **独立审查**必须复验「修复本身」；本轮 10 个缺陷里 4 个是「声称已完成但证据不支持」。
5. **涉及远端状态的结论先 `fetch`**（tag 断档就是未 fetch 的误判）。

## 2026-10-19 G71 队列可观测性 / 事件流 / 诊断包（v9.8.0）

- 范围：新增 `event_bus/errors/batch_state/diag_bundle/retry_panel/event_panel/engine_factory/download_layout`；
  修改 `http_api/engine/retry_queue/atomic_io/progress_file/git.service/main_window/settings/onboarding/help/failure_panel/i18n`。
- 独立审查：`python-reviewer` 无 Critical，5 Required（R1 无限重投、R2 @tag 计数覆盖、
  R3 诊断包泄露本机路径、R4 GUI 启停阻塞、R5 禁用态假成功）全部关闭；7 Suggestions 已处理。
- 验证：ruff/mypy 通过；G71 定向 54 OK；相关回归 142 OK；快集 1316 tests / 0 failures / 0 errors / 81%。
- 真实 E2E：`scripts/e2e_smoke.py` PASS；CLI `--json` 空输入 rc=2、version 9.8.0。
- 未闭环：B4 仓库知识索引、B5 MCP annotations/outputSchema、B6 真实浏览器/真机点击。
- 发布：commit `33f5e64` → main；tag `v9.8.0`；Release id `417169620`；CI 7/7 success。
- 发布证据：`evidence/G71-observability.json`、`evidence/G71-health-check.json`、`evidence/G71-release.json`。

## 2026-10-19 G72 加固（v9.8.1）

- 来源：独立调研 agent 的 8 条 backlog；实施 7 条（日志落盘打码、redact 变体、
  原子写 mkstemp/winerror、诊断包并发/上限、重试 jitter/批量、SSE Last-Event-ID/背压、订阅者指标）。
- 未实施：DLQ 可视化（下轮）。
- 验证：ruff/mypy 通过；G72 16 OK；G71+日志+打码+重试 107 OK；快集见验证记录。

## 2026-10-20 G73 DLQ 重新入队（v9.8.2）
- dropped 记录补全 spec 字段；`requeue_dropped` 重置 attempts 并立即到期。
- 重试面板新增「显示已放弃」与「重新入队选中」。
- 验证：G73 3 OK；定向 103 OK；快集见验证记录。

## 2026-10-21 计划文档产出一轮（仅文档交付，未改代码）
- 范围：只读扫描 + 文档产出（基线 `0018628`，版本 9.8.3）。**未修改任何代码/配置/测试**。
- 现测事实：`gcm/` 79 个 `.py` / 23,284 行；`gcm/ui/main_window.py` **2704** 行
  （`docs/architecture.md` §4 登记 2648 → 已漂移）；`tests/` 133 个文件
  （`README.md` 写 130）；`ci.yml:23` 的 `COVERAGE_FAIL_UNDER: 84` 是死值（被 `:75` 覆盖）。
- 新发现（登记为缺口，非缺陷声明）：**11 处「实现了但用户够不到」** ——
  6 个设置字段只读不写（`clone_timeout` / `minimize_to_tray` / `watchdog_silent` /
  `sched_report_format` / `post_clone_hook` / `quick_repos`）、`ScanWorker.progress` 半接线、
  `check_latest_with_sha` / `is_data_dir_private` / `migrate_data_dir` 零生产调用、
  `manage_model` 两个信号只 emit 不 connect。
- 产出：`docs/计划书/README.md`（目录生命周期纪律）+ `docs/计划书/下一步改进指南.md`
  （P1 5 / P2 6 / P3 6；方案 5 个；任务 T-01…T-18；含待执行验收项）。
- 验证：`python -m unittest tests.test_g67_docs_freshness` → `Ran 11 tests · OK`
  （首轮失败 2 条，因文档引用已删的限流模块路径；改文档后全绿，守卫未动）。
  未跑快集/构建/真机 —— 文档模式，不得声称通过。
- 未闭环：4 项需用户决策（四字段补 UI 或删、`post_clone_hook` 是否开放 UI、
  数据目录隐私入口、本地 API 一次性 token）。

## 2026-10-23 G75 收口计划（仅文档交付，未改代码）
- 范围：识别「工作区里的 G75 半成品」并把它排成可验收的收口任务。基线 `0018628` / 9.8.3，
  测量时刻 10:48–11:05；**期间工作区被并发修改**（modified 9→13 个），故数字均为测量点。
- 现测事实：`ruff check gcm` **6 error**（I001×2/F841×2/UP037/F401）；`mypy gcm` **2 error**
  （`status_strip.py:152,154` None+int）；定向回归 **242 条全绿**（68 + 174）；
  `progress_table.py` **848 行**（新破 800 红线）；`gcm/` 84 文件、`tests/` 134 文件；
  `docs/architecture.md` §4 行数表三项漂移且「3 个文件超 800」实为 **4 个**。
- 新发现：`@pyqtSlot(int,str)` 装饰器错位到零参 `_strip`（`gcm/ui/progress_table.py:430`，
  当前不可达但可再生）；`typography.apply_type` 零生产调用；`gcm/ui/palettes.py:15` docstring
  引用尚不存在的 `test_g75_visual_system`；齿条（`status_strip`）与 `progress_table.batch_total_lbl`
  并存，与 UI 计划「合并」口径冲突且无决策记录。**正向**：齿条/pill 均已真实接线（非空壳）。
- 产出：`docs/计划书/下一步改进指南.md` Rev 3（P1 8 / P2 11 / P3 7；T-19…T-33）+
  `history/下一步改进指南-R2-20261021.md` 留档 + `docs/计划书/README.md` 索引更新 +
  `tasks/riper5_20261023_1105.md`。
- 验证：`tests.test_g67_docs_freshness` → `Ran 11 · OK`（守卫未动）。未跑快集/覆盖率/构建/真机/CI。
- 未闭环：D6 齿条与总览条去留 · D7 4 个设置字段补 UI 或删 · D8 基线 PNG 入库或忽略 ·
  D9 本地 API token（需授权）。下一轮入口：T-19（门禁清零）→ T-24（登记同步）。

## 2026-10-23 G75 UI 视觉系统落地（v9.9.0，已改代码）
- 范围：把 `docs/计划书/UI视觉系统重构指南.md`（T-UI-00…T-UI-10）真实落地并发布。
- 结构：`theme.py` 598→314 行，拆出 `typography`(107) / `palettes`(211) / `qss_build`(262)；
  新增 `status_strip`(224) / `status_pill`(162) / `batch_overview`(90) / `result_labels`(62)；
  `progress_table` 848→799 行（回到 800 线内）。超 800 仍为 3 个：main_window 2725 / service 1768 / engine 1169。
- 修掉的真实缺陷（本轮现测）：①强调色预设只影响 QSS → 批次条旧蓝、行内条新青（加 `apply_accent_preset`）；
  ②nord success 4.23:1 未达 AA → 4.70:1；③失败汇总操作列按钮被 `::item` padding 裁字；
  ④`@pyqtSlot` 错位到零参 `_strip()`；⑤`theme="auto"` 时 `apply_theme("auto")` 退化为 deep。
- 验证（全部真实运行）：ruff 0 error · mypy Success(85 文件) · test_g75_visual_system 43 项 OK ·
  UI/主题/i18n 回归 172 项 OK · 快集 **1392 tests / 0 fail / 4 skip** · 覆盖率 **82%** ·
  PyInstaller 构建 OK · portable zip 79,691,506 B · health_check `version=9.9.0 started_ok=true` ·
  打包 exe 真实 E2E：clone→`action=cloned`，复跑→`action=fetched`，HEAD `7fd1a60`。
- 未做（不得读成完成）：3 处 QPropertyAnimation 编排、删除逐行消隐（D4，属功能回退需用户拍板）、
  进度条 25% 刻度、设置表单全角冒号（P3）。逐条见 `docs/archive/plans/20261023/archive-manifest.md`。
- 证据：`evidence/G75-ui.json` · `docs/验证记录-2026-10-23-G75UI.md` · 截图 `reports/ui-baseline-983` ↔ `reports/ui-g75`。

## 2026-10-23 G75 视觉系统 + G76 接线收口（v9.9.0，已落地）
- **G75**（前半轮由并发会话落地，本轮收口）：`theme.py` 598→314 拆为 `palettes`/`typography`/`qss_build`；
  七级字号 + 三字族（Bahnschrift/雅黑 UI/Cascadia Mono）；四套色板重制（Ink/Paper）+ `cta/on_cta/glow`；
  状态形状编码 `status_pill`（QPainter，不用字符符号 —— G74 的 tofu 教训）；仪表齿条 `status_strip`；
  批次总览 `batch_overview`、结果文案 `result_labels`（各成模块，`progress_table` 848→799）。
- **G76**（本轮）：6 个「只读不写」设置字段补写入侧（`minimize_to_tray` / `clone_timeout` /
  `post_clone_hook` / `watchdog_silent` / `sched_report_format` / `quick_repos`）；扫描进度确定态；
  更新提示附 SHA256；数据目录私密性显示 + 迁移入口（拒绝在跑任务）；`manage_model` 死信号接统计行。
  新增 `gcm/ui/settings_actions.py`（SettingsWiring 用**弱引用**持有面板，避免引用环）。
- 启动后立即发现的回归：`scripts/bench_g70_ui.py` 引用已迁走的 `theme._QSS_CACHE` → 已修（改为 `qss_build`）。
- i18n 教训：状态列「跳过」与首启向导「跳过」按钮共用一个 key → EN 只能二选一，且 ruff F601 报重复键；
  拆成独立键「已跳过」（EN `Skipped`）。**新增状态文案先查是否已有同名 key**。
- 验证：ruff/mypy 绿（86 文件）；`test_g76_wiring` 23；定向 234；快集 **1415 绿**、覆盖 **82%**；
  源码级 git E2E PASS；**打包 exe 真实 GitHub E2E**（cloned → fetched/已是最新）；基准 500 行 26.82ms；
  截图 38/38；构建 exe 41,340,204 B + zip 79,756,248 B；健康检查 version_match 9.9.0。
- 未闭环边界：本机 Windows 慢集、CI 三平台、真机 125% DPI 目检、数据目录真实搬移（均已在台账披露）。

### 追加（同轮发布结果）
- CI 第一次（`fa33ebc`）**5/7**：macos/ubuntu 的 fast suite 各 1 例红 → 根因是迁移目标写死 `%APPDATA%`，
  POSIX 上功能不可用（真实缺陷，不是测试问题）→ 修成跨平台目标目录（`%APPDATA%`→`$XDG_DATA_HOME`→`~/.local/share`）。
- CI 修复后（`07fc51c`）**7/7 success**；`v9.9.1`（`c4e647f`）**7/7 success**（run 39899957217）。
- 发布：`v9.9.0`（id 421433715，已被取代，body 已加说明）→ **`v9.9.1`**（id 421450820）
  exe 41,343,506 B sha256 `9ddc54b1…`、zip 79,761,826 B sha256 `37c69482…`，服务端 sha256 核对一致；
  打包 exe 真实 GitHub E2E：`action=cloned`、version 9.9.1。
- 教训入库：**平台路径分支必须在 `unset APPDATA` 下复跑**（本地 Windows 全绿 ≠ 功能可用）。

## 2026-10-23 G75 收口（v9.9.2）
- 补最后一项视觉缺口：`progress_delegate.tick_positions()` + 25/50/75% 刻度（3 项单测）。
- 补齐上一轮未跑项：Windows 慢集 129/OK、`bench_g70_ui` 基准、`启动Git-clone-Max.bat` 实跑、
  CLI 退出码 0/1/2 与 MCP 3、更新链路对**真实 Release** 三分支、T-33 连跑 10/10。
- 修一处**测试与实现不同步**：G76 把更新弹窗搬到 `gcm/ui/update_flow.py`（模块级 import QMessageBox），
  而 `test_g66_patch` 仍只 patch `main_window.QMessageBox` → 确定性红；已补打桩（生产代码未动）。
- 已知环境限制：本机 TLS 代理会打断 Python `urllib` 与 git schannel（`UNEXPECTED_EOF`），
  故更新检查的 urllib 直连路径无法本机实测，改用 requests 取真实 JSON 替换传输层。
- 发布：`v9.9.2`（Release id 421490075）；CI run 39905267408 **7/7**；本地快集 1421/0/4 skip，覆盖 82%；
  产物 exe 41,354,950 B（sha256 `c70a48a8…`，服务端校验一致）/ zip 79,779,525 B；
  打包 E2E：cloned → fetched（HEAD 7fd1a60）。**未做**真机 DPI 目检与"点击更新后真的打开浏览器"。

## 2026-10-23 独立审查与 v9.9.3（无功能新增）
- 独立只读 reviewer（第 3 个；前两个随会话重启被回收）→ **Approve**，无 Critical。
- 关闭 3 条 Required：①删测试里的「给 update_flow 打桩」死分支（该模块无模块级 QMessageBox，
  靠 `_msgbox()` 运行期取 main_window 的）②E501 折行 ③`_update_checking` 复位提到延迟 import 之前（防永久失效）。
- 采纳 4 条建议：AST 作用域断言（已变异验证能红）· `_msgbox()` 进 try · `check_silent` 补 G67-9 理由 ·
  未知进度不画刻度 + 删三连空行。未采纳 1 条（`test_g76_wiring` 巨型 with → ExitStack，纯可读性）。
- 教训：**给"抽出去的模块"打桩前先确认它有没有模块级符号**，否则写的是死分支还自认为加固了。
