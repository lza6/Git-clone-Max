# CLAUDE.md — Git-clone-Max 项目约定（供 AI agent / 新协作者先读）

> 本文件是**项目级**约定。与全局 `~/.claude/CLAUDE.md` 冲突时，**项目级优先**（安全/授权边界除外）。
> 若发现本文件与代码不符：**以代码为准**，并顺手修正本文件（过期文档比没有文档更贵）。

---

## 0. 30 秒建立上下文

- **这是什么**：GitHub / GitLab / Gitee / Codeberg / Bitbucket / 自建主机的**批量并行克隆 + 增量更新 + 入库追踪**桌面工具（Python 3.10+ / PyQt6）。目标用户是"挂着刷几百个仓库"的个人开发者与小白用户。
- **入口**：`gcm/__main__.py`（GUI）· `gcm/cli.py`（`--cli [--json] [--diag] [--serve]`）· `gcm/mcp_server.py`（MCP stdio）· `gcm/http_api.py`（本地只读 HTTP）。
- **版本单源**：`gcm/__init__.py` 的 `__version__`。**任何其他地方出现版本号字面量都是 bug**（见 §5 守卫）。

**开工前必读（`.agent/`）：**

| 想知道什么 | 读哪个 |
|-----------|--------|
| 哪些验证已经跑过（别重复跑） | `.agent/memory/tested-scope.md` |
| 已知边界 / 未闭环（别当 bug 修） | `.agent/memory/known-issues.md` |
| 为什么这样设计（别推翻后重犯） | `.agent/memory/decisions.md` |
| 改这块有哪些连带影响 | `.agent/memory/change-index.md` |
| 做事流程（加功能/加 API/发版/回滚/审查/审计/计划文档清理） | `.agent/skills/README.md` |
| 计划文档审计结论与归档 | `docs/archive/plans/<日期>/archive-manifest.md` |
| AI 协作铁律（验收、反假闭环） | `.agent/rules.md` |

**权威文档的位置（避免读一堆过期文档）**：

| 想知道什么 | 读哪个（唯一事实源） |
|-----------|---------------------|
| 我该改哪、改动影响什么 | `docs/architecture.md` + `docs/adr/ADR-001..004` |
| 什么条件下允许宣称「完成」 | `policy.md`（交付门禁 / 任务分级 / 证据标签 / 停止条件） |
| 这条链路哪个测试证明它通 / 哪个接缝没覆盖 | `test_ledger.md`（S01–S14 接缝注册表 + 测试状态） |
| 某个节点本轮的真实命令与输出 | `evidence/<节点ID>.json` |
| 可复用的代码范式 | `docs/code-gems.md`（单写者/原子写/redact/批聚合/流控/注入时钟/契约/纯校验） |
| agent 接入契约（CLI/MCP/HTTP schema、退出码、打码规则） | `docs/agent-contract.md` |
| 数据模型 / 迁移链 / 跨进程锁 | `docs/data-model.md` |
| 历次验证证据与审计报告 | `docs/验证记录-*.md` + `docs/reports/` |
| 每个版本交付了什么 | `gcm/__init__.py` 的 `__changelog__`（逐版本一行） |
| 还没做的（已知边界/技术债） | 本文件 §6 + 代码里的 `G##` 注释 |
| 跑过哪些验证、改哪块必跑哪些测试 | `docs/验证记录-*.md`（**改前先看，避免重复跑同一批验证**） |
| 慢测定位与提速 | `docs/慢测定位.md` |
| 发布流程 | `docs/RELEASE_CHECKLIST.md` |
| 产品定位与增长 | `docs/product-brief.md` |
| 并发/可观测边界 | `docs/perf-observability.md` |
| 交接与维护 SOP | `docs/SOP-交付与维护.md` |

---

## 1. 常用命令（Windows / bash；解释器一律用 `.venv/Scripts/python.exe`）

```bash
# 运行
./.venv/Scripts/python.exe -m gcm                          # GUI
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m gcm --cli --json urls.txt

# 测试（快集 ≈8.8min；慢集 ≈6min；全量 ≈15min）
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest tests.test_g66_patch -v
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m coverage run --source=gcm scripts/ci_run_fast.py
./.venv/Scripts/python.exe -m coverage report --include="gcm/*"

# 质量闸（CI 同款）
./.venv/Scripts/python.exe -m ruff check gcm        # 必须 0 error（注意：CI 只查 gcm，不含 tests/scripts）
./.venv/Scripts/python.exe -m mypy gcm              # 必须 Success

# 打包 / 发布
./.venv/Scripts/python.exe -m PyInstaller --noconfirm Git-clone-Max.spec
./.venv/Scripts/python.exe scripts/publish_release.py --dry-run
```

> **Windows bash 陷阱**：本机无 `rg`/`find` 的 PATH，用 `grep -rn` 或 `python`。中文输出乱码时加 `PYTHONIOENCODING=utf-8`。

---

## 2. 目录职责（放文件前先确认）

```
gcm/app/     纯逻辑：引擎/URL 解析/诊断/告警/通知/看门狗/限流/探测（不 import Qt Widgets 以外的 UI）
gcm/db/      持久化：repo_db / settings / lists / history / runtime_sessions / progress_gc / progress_file
gcm/git/     git 子进程封装：service.py（同步）、archive.py（归档）
gcm/ui/      PyQt6 界面：main_window 及其拆分面板；**新 UI 逻辑优先独立模块**，别再塞 main_window.py
gcm/util/    跨层工具：redact（脱敏）/ filelock（跨进程文件锁）
gcm/i18n/    文案字典 zh/en（key 即中文，缺失回退中文）
skills/      agent 技能包（SKILL.md + scripts/），每个必须自包含验收步骤
scripts/     运维/CI/基准脚本（不参与打包）
tests/       测试（113 文件）；**新增测试文件默认落在 CI 快集**（scripts/ci_run_fast.py 用黑名单，不是白名单）
docs/reports/ 历次审计报告（HTML，只读快照）
docs/        工程/产品/契约文档
```

---

## 3. 代码约定（本项目既有风格，请跟随，不要引入新范式）

- **子进程**：`subprocess.run(..., text=True)` **必须**同时给 `encoding`，但**不能无脑写 `"utf-8"`**：
  - **git** 输出 UTF-8 → 用 `encoding="utf-8", errors="replace"`。
  - **Windows 自带控制台工具（`schtasks` 等）** 输出 ANSI 代码页（中文系统 = GBK/cp936）
    → 用 `gcm/app/autostart.py:_console_encoding()`（取 `locale.getpreferredencoding(False)`，
    回退 `mbcs`）。写死 utf-8 会把用户可见的中文提示变成乱码（实测过的回归，别再犯）。
  - 通用判据：**先确认这个工具输出什么编码，再决定**。省略 `encoding` 则用系统 locale，
    对 UTF-8 输出的 git 会抛 `UnicodeDecodeError` 且 `stdout` 塌陷成 `None` →
    下游报「明明成功却失败」。此条有 AST 守卫强制（只强制「必须有 encoding」，不强制具体值）。
- **脱敏**：任何面向 UI / 日志 / agent 输出的文本，出站前过 `gcm/util/redact.redact`。
  `token` / `host_tokens` / `ssh_key` / URL 内嵌凭据一律打码。
- **SQL**：只允许参数化查询；`f-string` 只能用于 PRAGMA 常量（现状已如此）。DB 写入单线程 + `BEGIN IMMEDIATE` + `busy_timeout=30000`。
- **异常**：不要裸 `except:`；`except Exception` 必须要么记日志要么给出可操作提示，**禁止静默吞**（`# noqa: BLE001` 需附理由注释）。
- **资源**：文件/DB 连接/QThread/子进程都要有确定释放路径；Qt 对象注意父对象与线程亲和性。
- **不可变优先**：优先返回新对象而非就地改传入结构。
- **测试隔离铁律**：任何直接构造 `MainWindow`/`QWidget` 的测试，模块级先 `QApplication.instance() or QApplication([])`。
- **测试断言**：禁止绝对日期表达相对语义（`"2026-09-01"` + "距今 8 天" = 时间炸弹）。用 `datetime.now() - timedelta(...)`。
- **i18n**：用户可见新文案走 `tr()`；新增 key 必须同时进 `gcm/i18n/zh.py` 与 `en.py`。
- **文件规模**：新模块目标 200–400 行，硬上限 800 行；函数上限 50 行；嵌套上限 4 层。
- **Windows 约束**：不新增 `.sh`（用 PowerShell / node / python）；`.bat` 必须 CRLF（`.gitattributes` 已强制）。

---

## 4. 加一个新功能 / 新 API 的标准流程

1. **先读**：`docs/architecture.md` → 目标区域的 ADR → `docs/验证记录-*.md`（这块最近改过吗、必跑哪些测试）。
2. **判过期**：文档与代码冲突时以代码为准，**顺手修文档**（同一次提交里）。
3. **定点**：新能力落在哪一层（见 §2）？跨层调用是否已有现成契约（`docs/code-gems.md` 的范式可复用）？
4. **TDD**：先写失败测试（RED）→ 最小实现（GREEN）→ 重构（IMPROVE）。业务逻辑真跑，只对**外部边界**（网络/时钟/对话框/子进程）mock。
5. **加守卫**：如果这个 bug 属于"能再犯"的类型（版本漂移 / 编码 / 时间 / 安全边界），**补一个静态或 E2E 守卫测试**，别只修当下这一处。
6. **登记**：`gcm/__init__.py` 的 `__changelog__` 追加一行（版本号 + 一句话能力 + 测试/覆盖率）；
   新能力的「凭什么算完成」写进 `docs/验证记录-<日期>.md`（命令 + 真实输出摘要）。
7. **同步文档**：README 功能表 / `docs/agent-contract.md`（若涉及 agent 入口）/ `docs/architecture.md`（若涉及分层）/ 本文件（若涉及约定）。
8. **收尾**：`ruff check gcm` + `mypy gcm` + 定向回归 → 必要时快集 → 版本 bump（`__version__` + `__changelog__` 一行）→ 打包 → 按 `docs/RELEASE_CHECKLIST.md` 发布。
9. **agent 可调用**：新能力若要暴露给 agent，同步更新 `skills/` 与 `docs/agent-contract.md`，并保证退出码语义（0/1/2，MCP 未启用为 3）与 `--json` schema 不被破坏。

---

## 5. 不可违反的红线（都有测试守卫，破坏了 CI 会红）

| 红线 | 守卫 |
|------|------|
| 版本号只能来自 `gcm.__version__`（含 pyproject / installer.iss / spec / changelog） | `tests/test_g66_patch.py::TestContractVersionSingleSource` + `TestPackagingVersionSync` |
| `subprocess` 文本模式必须显式给 `encoding`（gcm / scripts / tests 全覆盖）；git 用 utf-8，Windows 控制台工具用 `_console_encoding()` | `tests/test_g66_patch.py::TestSubprocessTextDecoding` + `TestSubprocessDecodeE2E` + `TestConsoleToolEncoding` |
| 测试不得用绝对日期表达相对语义 | `tests/test_g66_patch.py::TestNoTimeBombAssertions` |
| 本地 HTTP API 只允许回环绑定（服务无鉴权） | `tests/test_g66_patch.py::TestHttpApiBindHardening` |
| MCP / HTTP API **默认只读**，写操作需显式放行（`GCM_MCP_ENABLE=1` + `--allow-export` / `--allow-mutate`） | `tests/test_g56_mcp*.py` / `test_g56_api_write.py` |
| 付费外部 API 默认不真实调用（图像/视频/支付等）；用 mock 覆盖 success/timeout/rate_limit/malformed/retry_exhausted | 全局规则 + 现有 mock 惯例 |
| 写盘互斥必须**跨进程**：`settings.json` / `progress.json` 的读-改-写要走 `gcm/util/filelock.cross_process_lock`（线程锁只保护单进程，多实例会丢更新） | `tests/test_g67_closure.py::TestCrossProcessLock`（含无锁对照组反向自检） |
| 数据目录解析**唯一来源**是 `gcm.__main__.get_data_dir()`，任何模块不得自行拼 `<exe>/data` 或 `<源码>/data`（否则安装版读不到 `%APPDATA%` 里的配置） | `tests/test_g67_closure.py::TestExtraHostsUsesRealDataDir` |
| 同步网络 I/O（检查更新、诊断、归档、通知）**不得在 GUI 线程**执行：worker 只 `emit`，槽回主线程 | `tests/test_g66_patch.py::TestNoCrossThreadWidgetAccess` / `TestUpdateCheckThreading` |
| 自动重投（retry）必须复用上一批的 `host_by_key` / `fetch_depth` / `mirror`（否则污染 `host` 列、克隆语义错位） | `tests/test_g67_closure.py::TestRetryReusesBatchContext` |
| 新增的模块/函数/设置项**必须有生产调用点或 UI 入口**（只在 tests 里出现 = 虚伪实现） | `tests/test_g69_regressions.py::TestG69_18WiringClosure` |
| 新增设置项必须**写入侧与消费侧都接线**（只写不读 = 用户改了没用） | 同上 |
| 断言 `GIT_CONFIG_*` 精确索引的测试必须隔离进程环境（父进程可能已带该变量） | `tests/_gitcfg_isolation.py` mixin |
| 异常处理分支不得引用不存在的 API（会把可容忍坏输入升级为全局中断） | `TestG69_18VerifierFindings::test_env_uses_only_existing_applog_api` |
| 绝不为了绿灯削弱断言、跳过测试、mock 生产逻辑 | 代码审查 |

---

## 6. 已知边界（诚实清单，别当 bug 修）

- **`计划书/` 台账已删，但「全部闭环」的说法不成立（G69-18 独立复验）**。
  该目录在提交 `e6a8759`（2026-10-14）被删，理由写的是「台账全部闭环并经用户确认」。
  2026-10-16 的独立复验推翻了它：**定时报表整条链路零生产调用点**、**阈值告警未接
  `on_alerts`**、**9 个设置字段只读不写**（用户够不到）—— 均已在本轮接线；
  但仍有未闭环项，见下方「未闭环（需立项）」。**不要再以「台账已删」当作已完成的证据。**
  2026-10-17 已完成**计划文档二次审计**：目录墓碑归档至
  `docs/archive/plans/20261017/archive-manifest.md`（含逐条状态表），`计划书/` 目录已移除。
- **未闭环（需立项，勿当成已完成）**：
  - ~~`G54-4 引导清单`：台账标 ✅，实际全库无该组件。~~
    **2026-10-17 更正**：v9.4.3 已实现 `gcm/ui/checklist.py`（5 项，首启提示一次，
    设置页入口），守卫 `tests/test_g69_checklist.py`。
  - ~~`G53-1 health_card`：台账称新增 JSON 列 + 雷达图，实际仍是单值 `repo_health` + 派生视图。~~
    **2026-10-18 已闭环（v9.6.0）**：雷达图已落地（`gcm/ui/radar_chart.py`，统计中心挂载，
    6 维、三主题可读，守卫 `tests/test_g53_radar.py`）。**`health_card` 列刻意不加**：
    评分由 `sync_history` 实时派生（`alerts.scorecard_for_repo`）是唯一事实源，
    再存一份冗余列会漂移；若将来需要「历史评分趋势」，应新建快照表而不是加列。
  - ~~`G58-3 代理凭据`：台账称「解析 + 加密落盘」，实际未解析、`settings.proxy` 明文落盘。~~
    **2026-10-17 更正**：`SettingsStore.save()` 已对 `proxy` 调 `_encrypt_token`，加载时 `_decrypt_or_keep`
    （`gcm/db/settings.py:366,407-409`）。「解析」不是必需项 —— 代理以完整 URL 注入
    `http_proxy/https_proxy`，git 原生支持 `http://user:pass@host:port`（`git/service.py:492-494`）。
  - `G58-1 统一限流`：原「统一限流」模块在 v9.2.0 因**零生产调用点**被删除；
    git 侧限速由 `settings.rate_limit_kbps` → `http.lowSpeedLimit` 承担。
    「统一限流」当前**没有真实调用场景**，不属缺陷。
  - ~~`G58-2 镜像测速择优`：只有静态 `mirror_prefix`，未实现候选测速排序。~~
    **2026-10-18 已闭环（v9.5.0）**：`gcm/app/mirrors.py`（多候选 + 预设）+
    `gcm/app/mirror_probe.py`（`git ls-remote` 短超时探测 + 缓存）+
    克隆前探测择优、克隆失败回退源站、更新失败在镜像/源站间切换。
- **镜像默认启用（G58-2，v9.5.0）**：内置 4 个国内 GitHub 镜像**默认开启**。
  三条边界必须守住：① 只对 `github.com` + HTTPS 生效；② **有令牌/主机凭据时绝不用镜像**
  （第三方镜像能看到 `Authorization` 头）；③ 源站永远是最后候选，镜像全挂也能继续。
  预设是 2026-10-18 用 `git ls-remote` 实测的第三方服务，会失效；设置页可关闭或
  用「测速并排序」按当前网络重排。**不要用 HTTP HEAD 判断镜像可用性** ——
  实测 HEAD 结果与 git 能否工作不一致（见 `gcm/app/mirror_probe.py` 顶部说明）。
- **镜像凭据边界（G69-21，v9.6.0 加固）**：认证头必须是 **URL 作用域**
  （`http.https://<host>/.extraHeader`），不能用全局 `http.extraHeader`
  （全局头会随 fetch 发给 origin 实际所在的主机）。镜像克隆成功后必须立即
  `remote set-url origin <源站>`，镜像只记在 `gcm.mirror`；旧仓库在更新时自动规范化。
  全局/系统 git 配置里带 `http.*extraHeader` 时**整体禁用镜像**；
  URL 内嵌凭据（`https://user:pass@host`）不参与镜像。
  - ~~`v9.1.3 ~ v9.3.2` 未打 git tag~~ **该条不成立，已于 2026-10-17 更正**：
    这些 tag **在远端一直存在**，是本地未 `git fetch --tags` 造成的误判。
    实测 `git ls-remote --tags origin` 后逐个校验：v9.x 全部 tag 与其提交的
    `__version__` **逐一对得上**。教训：**涉及远端状态的结论必须先 fetch 再下**。
- **CI `test` job 的原生崩溃（根因已定，已有处置）**：
  Linux/macOS 表现为 `exit 139`（SIGSEGV），本机 Windows 可复现。
  **根因**：`MainWindow` 等 Qt 控件由**引用环**维持 —— 实测 `del w` 之后
  **不被引用计数回收**，只有 `gc.collect()` 才回收（一次 369 个对象）。
  于是销毁时机落到 Python 的**自动循环 GC** 上，而它在**任意分配点**触发，
  **包括 `QApplication.processEvents()` 内部** → Qt 在事件分发中途销毁控件与其
  子定时器 → `QTimerInfoList::activateTimers` 解引用已释放对象 → 崩溃。
  这解释了全部观测：崩溃点随机漂移、`PYTHONMALLOC=debug` 会改变结果、测试越多越易崩。
  **处置**：`scripts/ci_run_fast.py` 整轮关闭自动循环 GC，只在全部模块跑完后回收一次。
  对照实验（禁用循环 GC 跑快集）实测 0 崩溃。
  **仍未根治**：正解是让每个用例确定性收尾（close → deleteLater → 泵空事件队列），
  但那要逐个改造 31 个构造 MainWindow 的测试文件；已登记为后续工作。
  **不得**用 `skip`/重试把它「弄绿」。
- **CI 未锁定 PyQt6 版本**：主矩阵用 `pip install PyQt6`（取最新），
  而 `requirements-build.txt` 锁了 ruff/mypy/coverage/pyinstaller。上游发新版会让 CI 行为漂移。
- **代码规模现状（2026-10-19 实测）**：仍为 **3 个**文件超 800 行 —— `main_window.py` **2624**、
  `service.py` **1740**、`engine.py` **976**（`progress_table.py` **800**、`repo_db.py` **799**、
  `settings_panel.py` **720**、`settings_groups.py` **679**、`theme.py` **565** 均在 800 线内）；
  8 个函数超 100 行（最大 `service._update` 178 行）。
  `build_settings_ui` 曾达 432 行，已拆为 `settings_groups.py` 的 7 个构建器；
  G70 又把进度 delegate 拆到 `progress_delegate.py`（65 行）。
  **这是已登记的、被显式接受的技术债，不是缺陷**：`main_window.py` 的体量在
  「拆分收益 < 回归风险」区间，一次大规模 UI 重排会危及发布稳定性。
  约束是**增量的**：新增 UI 逻辑一律落独立模块（既有 `failure_panel` / `onboarding` /
  `progress_table` / `progress_delegate` / `layout` / `settings_panel` / `main_window_tasks` 先例），
  不得再往 `main_window.py` 里塞。下次动这几块时**顺手**把所在函数拆小。
- **覆盖率门禁是快集口径**（Windows 81 / POSIX 80），不是全量口径；POSIX 低 1 个点源于 `msvcrt`/`schtasks`/`winreg` 分支不可执行。
- **`gcm/ui/*.py` 有 E501 豁免**（纯中文文案长行，East-Asian 宽度计数）；`gcm/i18n/*.py` 为生成文件同样豁免。
- **CI 只在 Linux 上跑慢集**：Windows 特有的编码/路径问题必须本机验证，别只信 CI 绿灯。
- **打包构建未纳入 CI**：`Git-clone-Max.spec` 的版本常量由静态测试守卫，但 exe 需人工重建验证。
- **技能包脚本用 `sys.executable` 找解释器**：在打包 exe 环境下需改为打包路径（见 `skills/G56-1-验收说明.md` 遗留风险）。
- **`report.export_xlsx` 依赖可选包 openpyxl**：缺失时抛带安装提示的 RuntimeError（不静默降级）。
