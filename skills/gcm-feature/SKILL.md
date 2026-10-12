# gcm-feature：Git-clone-Max 功能/API 扩展工作流技能

为 agent 提供的**「往这个项目里加新功能/新 API」的标准作业流程**。
不是代码生成器，而是一份**强制先读、先判断过期、再动手**的检查清单 ——
目的是让每次扩展都落在同一个质量水位上，而不是每次重新摸索。

## 何时用这个技能

- 要新增一个功能、新命令、新 API 端点、新 UI 面板、新数据字段
- 要修一个**可能反复出现**的缺陷（版本漂移 / 编码 / 时间 / 安全边界 / 平台差异）
- 接手这个仓库、不知道该信哪份文档

## 第 0 步（强制）：先读，且先判断文档是否过期

**按顺序读这四份，不要跳**：

| 顺序 | 文件 | 你要从中拿到什么 |
|------|------|------------------|
| 1 | `CLAUDE.md` | 项目约定、目录职责、红线、常用命令、已知边界 |
| 2 | `docs/architecture.md` + `docs/adr/ADR-001..004` | 目标区域的分层与既有决策（**别推翻已决策的事**） |
| 3 | `docs/验证记录-*.md` | 这块最近改过吗？改了必须跑哪些测试？ |
| 4 | `计划书/下一步改进指南.md` + `计划书/验收清单.md` | 这件事是不是已经做了？有什么现成缺口？ |

> **判断过期**：文档与代码冲突时**一律以代码为准**，并在同一次提交里顺手修掉那份文档。
> 本项目已多次出现「文档说没做、代码已做」（如 G53 设置 UI、CI fast/slow 拆分），
> 也出现过「文档说做了、代码没有」（如 G48-3 SSH key 链路）—— **两个方向都要怀疑**。

## 第 1 步：用 grep 确认这件事的真实状态

```bash
# 功能是否已存在（函数名 / 设置项 / UI 文案 / API 路径）
grep -rn "<关键标识>" gcm/ tests/ docs/ skills/ 计划书/

# 某文件/函数是否真有调用方（防"写了但接不上"）
grep -rn "<函数名>" gcm/ | grep -v "def <函数名>"

# 某设置项：写入侧与读取侧的 key 是否一致（本项目踩过的坑）
grep -rn "<设置字段名>" gcm/
```

**本项目最贵的三类坑，都靠这一步暴露**：

1. **写入侧与读取侧 key 不一致** —— UI 写 `host_auth_types["*"]`，`_env()` 只读 `[host]`，
   于是下拉写了半年但从不生效（G66-9）。**必须同时 grep 写入与读取。**
2. **有代码但没有调用点** —— `Database.set_ssh_key` / `host_probe.py` / `export_xlsx`
   都属此类，changelog 却宣称已落地（G67-7）。
3. **入口参数写了但跑不通** —— `--cli --serve 127.0.0.1:8765` 是文档原样命令却 100% 崩溃（G66-8）。

## 第 2 步：判断这是「能再犯」的缺陷吗？

如果是，**不要只修当下这一处** —— 补一个守卫测试。本项目已有 11 类守卫（见
`tests/test_g66_patch.py`），可作为模板：

| 缺陷类型 | 守卫手段 |
|----------|----------|
| 版本号漂移 | AST 扫描字符串常量里的 semver 形态 |
| 打包/元数据版本不一致 | 正则解析 pyproject/iss/spec 与 `__version__` 比对 |
| 子进程编码 | AST 扫描 `text=True` 是否缺 `encoding` |
| 时间相关 | 静态扫「相对语义 + 绝对日期字面量」 |
| 安全边界 | 直接调真实构造函数断言被拒绝 |
| 跨层契约 | 端到端断言**消费端**收到的内容（不只看写入端） |

> **守卫质量自检**：新增守卫后问自己两个问题 ——
> ① **能绕过吗？**（换一种等价写法还拦得住吗）
> ② **它证明的是「拼对了」还是「生效了」？** 后者才有效。
> 本项目的 G66-14 就是自查发现这两点都不满足后升级的。

## 第 3 步：TDD（RED → GREEN → IMPROVE）

- 先写会失败的测试，**跑一次确认它真的失败**（否则不知道测试有没有在测东西）
- 只对**外部边界** mock（网络 / 时钟 / 对话框 / 子进程）；业务逻辑真跑
- 真实 E2E 一律用本地裸仓（`file://`），不依赖外网
- **浅克隆相关 E2E 必须用 `file://` URL** —— git 对本地路径克隆会忽略 `--depth`

## 第 4 步：定点实现（按分层落）

```
gcm/app/   纯逻辑（引擎/URL/诊断/告警/通知/看门狗/限流）
gcm/db/    持久化（repo_db / settings / lists / history / runtime_sessions）
gcm/git/   git 子进程封装（service / archive）
gcm/ui/    PyQt6（新 UI 逻辑**优先独立模块**，别再塞 main_window.py）
gcm/util/  跨层工具（redact）
```

**跨层注意**：
- 任何面向 UI/日志/agent 的输出，出站前过 `gcm/util/redact.redact`
- 任何 `subprocess.run(..., text=True)` 必须给 `encoding`，且**不能一律 utf-8**：
  git 用 utf-8，Windows 控制台工具（`schtasks`）用 `autostart._console_encoding()`
- SQL 只允许参数化；DB 写入单线程 + `BEGIN IMMEDIATE`

## 第 5 步：agent 可调用性（如果要暴露给 agent）

- 更新 `skills/` 与 `docs/agent-contract.md`
- 保持退出码语义：`0` 全成功 / `1` 有失败 / `2` 无有效输入 / `3` MCP 未启用
- `--json` 时 stdout 只含 JSON，进度走 stderr
- 新设置项若要落盘，确认加密链路（token 类走 `_encrypt_token`）

## 第 6 步：验证（分层，别机械全跑）

```bash
# 1. 定向（最快）
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest tests.test_<相关> -v

# 2. 受影响面回归批（把相关几个文件一起跑）
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest tests.test_a tests.test_b

# 3. 质量闸（必须）
./.venv/Scripts/python.exe -m ruff check gcm
./.venv/Scripts/python.exe -m mypy gcm

# 4. 真实 E2E
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe scripts/e2e_smoke.py

# 5. 快集全量（~7.5min，含覆盖率门禁）
./.venv/Scripts/python.exe -m coverage run --source=gcm scripts/ci_run_fast.py
./.venv/Scripts/python.exe -m coverage report --fail-under=81

# 6. 提交前若改到了会 flaky 的东西（HTTP server / 端口 / 线程），连跑 3-4 次
```

## 第 7 步：登记（**不做这步等于没做完**）

1. `计划书/下一步改进指南.md`：条目标 `[✅ 已落地]` + 复现→修复→验证三段证据
2. `计划书/验收清单.md`：加一行证据（命令 + 输出摘要）
3. `计划书/workflow_status.md`：本版本交付登记
4. README 功能表 / `docs/agent-contract.md`（若涉及 agent 入口）/ `docs/architecture.md`（若涉及分层）
5. `docs/验证记录-*.md`：**记录这次改了哪些区域、跑过哪些验证** ——
   下次改同一区域的人（可能是未来的你）会先读它，避免重复跑同一批验证
6. `gcm/__init__.py`：`__version__` bump + `__changelog__` 追加一行
7. 提交按主题拆分（feat / fix / test / docs / chore），不用 `git add .`

## 验收清单（本技能交付即自检）

1. `python -m unittest tests.test_g66_patch` → 40 用例全绿（含所有历史守卫）
2. `ruff check gcm` → All checks passed；`mypy gcm` → Success
3. `scripts/e2e_smoke.py` → `E2E_SMOKE_RESULT: PASS`
4. 新增/修改的每个「能再犯」缺陷都配了守卫，且守卫通过「抗绕过」与「真生效」自检
5. 七步登记全部落地，无「代码改了文档没改」

## 历史教训索引（踩过的坑，别再踩）

| 坑 | 症状 | 防线 |
|----|------|------|
| 契约版本硬编码 | agent 读到的版本落后 4 个大版本，测试还把错值断言成正确 | `TestContractVersionSingleSource` |
| 测试用绝对日期 | 用例随时间必然变红，主矩阵门禁失效 | `TestNoTimeBombAssertions` |
| 中文 Windows 解码 | git UTF-8 输出按 GBK 解 → 抛异常 → 假失败 | `TestSubprocessTextDecoding` |
| 统一 UTF-8 套到控制台工具 | schtasks 中文提示变乱码 | `TestConsoleToolEncoding` |
| 安全边界只写在文档 | `--serve 0.0.0.0` 把仓库清单暴露到局域网 | `TestHttpApiBindHardening` |
| CLI 入口没真跑过 | 文档命令 100% 崩溃；`--help` 静默拉起 GUI 挂死 | `TestCliEntrypointContract` |
| UI 写 key、消费端读不到 | 认证头下拉永不生效 | `TestAuthHeaderGlobalFallback` |
| 两个开关被错误 AND | 「浅克隆」静默退化为全量克隆 | `TestShallowCloneDepth` |
| 测试端口 TOCTOU | 间歇性红灯 → 训练人忽略红灯 | 端口 0 内核分配 |
