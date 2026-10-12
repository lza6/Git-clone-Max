# CLAUDE.md — Git-clone-Max 项目约定（供 AI agent / 新协作者先读）

> 本文件是**项目级**约定。与全局 `~/.claude/CLAUDE.md` 冲突时，**项目级优先**（安全/授权边界除外）。
> 若发现本文件与代码不符：**以代码为准**，并顺手修正本文件（过期文档比没有文档更贵）。

---

## 0. 30 秒建立上下文

- **这是什么**：GitHub / GitLab / Gitee / Codeberg / Bitbucket / 自建主机的**批量并行克隆 + 增量更新 + 入库追踪**桌面工具（Python 3.10+ / PyQt6）。目标用户是"挂着刷几百个仓库"的个人开发者与小白用户。
- **入口**：`gcm/__main__.py`（GUI）· `gcm/cli.py`（`--cli [--json] [--diag] [--serve]`）· `gcm/mcp_server.py`（MCP stdio）· `gcm/http_api.py`（本地只读 HTTP）。
- **版本单源**：`gcm/__init__.py` 的 `__version__`。**任何其他地方出现版本号字面量都是 bug**（见 §5 守卫）。

**权威文档的位置（避免读一堆过期文档）**：

| 想知道什么 | 读哪个（唯一事实源） |
|-----------|---------------------|
| 我该改哪、改动影响什么 | `docs/architecture.md` + `docs/adr/ADR-001..004` |
| 可复用的代码范式 | `docs/code-gems.md`（单写者/原子写/redact/批聚合/流控/注入时钟/契约/纯校验） |
| agent 接入契约（CLI/MCP/HTTP schema、退出码、打码规则） | `docs/agent-contract.md` |
| 这个迭代还剩什么没做 | `计划书/下一步改进指南.md`（唯一总纲） |
| 每条需求凭什么说"已完成" | `计划书/验收清单.md`（逐条证据，发布清单硬性要求） |
| 当前版本交付了什么 | `计划书/workflow_status.md`（逐版本登记） |
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
gcm/db/      持久化：repo_db / settings / lists / history / runtime_sessions / progress_gc
gcm/git/     git 子进程封装：service.py（同步）、archive.py（归档）
gcm/ui/      PyQt6 界面：main_window 及其拆分面板；**新 UI 逻辑优先独立模块**，别再塞 main_window.py
gcm/util/    跨层工具：redact（脱敏）
gcm/i18n/    文案字典 zh/en（key 即中文，缺失回退中文）
skills/      agent 技能包（SKILL.md + scripts/），每个必须自包含验收步骤
scripts/     运维/CI/基准脚本（不参与打包）
tests/       测试（113 文件）；**新增测试文件默认落在 CI 快集**（scripts/ci_run_fast.py 用黑名单，不是白名单）
计划书/      迭代总纲与证据台账（中文名目录，保持）
docs/        工程/产品/契约文档
```

---

## 3. 代码约定（本项目既有风格，请跟随，不要引入新范式）

- **子进程**：`subprocess.run(..., text=True)` **必须**同时给 `encoding="utf-8", errors="replace"`。
  中文 Windows 默认 locale 是 cp936，git 的 UTF-8 输出会让读取线程抛 `UnicodeDecodeError`，
  且 `CompletedProcess.stdout` 会塌陷成 `None` → 下游报"明明成功却失败"。此条有 AST 守卫强制。
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
6. **登记**：`计划书/下一步改进指南.md` 条目标 `[✅ 已落地]` + `计划书/验收清单.md` 加证据行（命令 + 输出摘要）。
7. **同步文档**：README 功能表 / `docs/agent-contract.md`（若涉及 agent 入口）/ `docs/architecture.md`（若涉及分层）/ 本文件（若涉及约定）。
8. **收尾**：`ruff check gcm` + `mypy gcm` + 定向回归 → 必要时快集 → 版本 bump（`__version__` + `__changelog__` 一行）→ 打包 → 按 `docs/RELEASE_CHECKLIST.md` 发布。
9. **agent 可调用**：新能力若要暴露给 agent，同步更新 `skills/` 与 `docs/agent-contract.md`，并保证退出码语义（0/1/2，MCP 未启用为 3）与 `--json` schema 不被破坏。

---

## 5. 不可违反的红线（都有测试守卫，破坏了 CI 会红）

| 红线 | 守卫 |
|------|------|
| 版本号只能来自 `gcm.__version__`（含 pyproject / installer.iss / spec / changelog） | `tests/test_g66_patch.py::TestContractVersionSingleSource` + `TestPackagingVersionSync` |
| `subprocess` 文本模式必须显式 UTF-8（gcm / scripts / tests 全覆盖） | `tests/test_g66_patch.py::TestSubprocessTextDecoding` + `TestSubprocessDecodeE2E` |
| 测试不得用绝对日期表达相对语义 | `tests/test_g66_patch.py::TestNoTimeBombAssertions` |
| 本地 HTTP API 只允许回环绑定（服务无鉴权） | `tests/test_g66_patch.py::TestHttpApiBindHardening` |
| MCP / HTTP API **默认只读**，写操作需显式放行（`GCM_MCP_ENABLE=1` + `--allow-export` / `--allow-mutate`） | `tests/test_g56_mcp*.py` / `test_g56_api_write.py` |
| 付费外部 API 默认不真实调用（图像/视频/支付等）；用 mock 覆盖 success/timeout/rate_limit/malformed/retry_exhausted | 全局规则 + 现有 mock 惯例 |
| 绝不为了绿灯削弱断言、跳过测试、mock 生产逻辑 | 代码审查 |

---

## 6. 已知边界（诚实清单，别当 bug 修）

- **`main_window.py` 仍偏大**（约 1900 行）：新 UI 逻辑请落独立模块（既有 `failure_panel` / `onboarding` / `progress_table` / `settings_panel` 先例）。
- **覆盖率门禁是快集口径**（Windows 81 / POSIX 80），不是全量口径；POSIX 低 1 个点源于 `msvcrt`/`schtasks`/`winreg` 分支不可执行。
- **`gcm/ui/*.py` 有 E501 豁免**（纯中文文案长行，East-Asian 宽度计数）；`gcm/i18n/*.py` 为生成文件同样豁免。
- **CI 只在 Linux 上跑慢集**：Windows 特有的编码/路径问题必须本机验证，别只信 CI 绿灯。
- **打包构建未纳入 CI**：`Git-clone-Max.spec` 的版本常量由静态测试守卫，但 exe 需人工重建验证。
- **技能包脚本用 `sys.executable` 找解释器**：在打包 exe 环境下需改为打包路径（见 `skills/G56-1-验收说明.md` 遗留风险）。
