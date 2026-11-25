# Agent 管理 Agent · 外部实践扫描 backlog（2026-11-26）

> **backlog 只是候选，未经主控评估不执行。** 本文件不改变任何代码、配置、测试或发布状态；条目进入里程碑前须由主控按 `policy.md` 分级并重新评估适用性。

## 0. 采集元信息

| 项 | 值 |
|---|---|
| 来源轮次 | 第 1 轮（唯一一轮） |
| 搜索/抓取预算 | 12 次，已用满 12 次 |
| WebSearch | 6 次，**本环境全部返回空结果**（仅回显提示语，无结果块） |
| WebFetch | 6 次，全部成功（PyPI / Microsoft Learn / Qt 文档 / Python 文档 / GitHub） |
| 时间上限 | 未超（本次任务内完成，未再启动其他代理） |
| 证据等级 | 所有「已确认」事实均来自上述 6 次抓取的原文；未抓到的内容一律标 **未确认**，不补数字、不补结论 |

采集受限的后果写在这里，避免下游误读：本轮**没有**社区讨论、issue 线程、博客这类二手证据，来源全部是一手文档。因此下表里凡是「实践建议」性质的判断，都是基于项目现有能力的推理，不是从外部案例照搬。

## 1. 筛选口径

项目阶段：单人维护、本地桌面、无服务端、无账号、Windows 优先、已有强守卫文化与发布门禁。据此丢弃一切需要服务端、多租户、团队协作流水线、SaaS 可观测性的实践。

每条收录项必须能回答一句：**它检测/解决的具体失败是什么，出现后下一步动作有何不同。**

---

## 2. P2（值得做）

### P2-1 线程与虚函数异常统一落盘（`sys.excepthook` + `threading.excepthook`）

- **来源**：https://docs.python.org/3/library/threading.html#threading.excepthook （原文确认：`threading.excepthook(args, /)`，参数含 `exc_type/exc_value/exc_traceback/thread`；默认行为是「除 `SystemExit` 静默忽略外，其余打印到 `sys.stderr`」；若 hook 自身抛异常，则转交 `sys.excepthook`）
- **适用性**：本项目是 windowed 打包（`PyInstaller`，无控制台），`sys.stderr` 不存在或不可见——线程里逃逸的异常、以及 Qt 虚函数重写（`closeEvent` 这类）里未捕获的异常，在用户机器上等于**无声消失**。v9.24.1 统计中心 `0xC0000409` 闪退正是这一类：异常发生在 Qt 回调里，只留下一个 fail-fast 退出码，没有任何落盘线索。
- **预期收益**：任何未捕获异常在进程死掉之前于 `app.log` 留一条带线程名与栈的记录，并可直接进诊断包（现成的打码管线已经覆盖日志出口）。把「用户说闪退」从不可复现变成可定位。
- **风险/成本**：约 40–80 行，落在 `gcm/util` 或 `gcm/app/applog` 旁边；风险是 hook 里再抛异常造成二次伤害，必须整段 `try/except` 包裹并降级到原 hook。不引入依赖、不改 UI。
- **可验证方式**：`tests/` 加两例——① 子线程 `raise ValueError`，断言 `app.log` 出现该异常且进程正常退出；② 一个测试用窗口在 `closeEvent` 里抛异常，断言日志有记录、退出码仍为 `0xC0000409`（证明「Qt 仍然终止，但我们先拿到了证据」）。两例都必须能红（删掉 hook 即失败）。

### P2-2 Windows 本机崩溃取证：开启 WER LocalDumps

- **来源**：无抓取（本轮预算已用满）。**静态知识，需本机实测**：`HKLM\SOFTWARE\Microsoft\Windows\Windows Error Reporting\LocalDumps\<exe 名>` 下 `DumpFolder`(REG_EXPAND_SZ) + `DumpType`(DWORD, 2=完整)。
- **适用性**：P2-1 抓的是 Python 层异常；`__fastfail`（`0xC0000409`）**刻意绕过** SEH 与 `faulthandler`，Python 侧拿不到任何东西。WER LocalDumps 是唯一能在本机留下 minidump 的系统级手段，而且零代码。对「一天发三个版本、闪退只能靠用户口述」的 solo 维护者是最高性价比的一步。
- **预期收益**：下次拿到 `0xC0000409` 时有 dump 可看原生栈（谁在 `abort`、哪个 Qt 对象已析构），而不是只有退出码。
- **风险/成本**：只写本机注册表，随项目文档记录，不做安装器行为——用户机器不开，这是维护者自己的取证配置。约 10 分钟 + 一段 `known-issues` 说明。
- **可验证方式**：写一个 5 行 `os.abort()` 的独立小程序，按上述注册表配置跑一次，确认 `DumpFolder` 下生成 `.dmp`（用 WinDbg/`dumpchk` 能打开即算通过）。验证通过再把同样的键指向 `Git-clone-Max.exe`。

### P2-3 可访问性最小验收（读屏 + 自定义绘制控件）

- **来源**：https://doc.qt.io/qt-6/qaccessible.html （原文确认：标准控件由 Qt 自带 accessible 插件提供，无需实现；**自定义控件**需实现 `QAccessibleInterface`，或继承 `QAccessibleObject`/`QAccessibleWidget`；状态变化须主动 `updateAccessibility()`；`isActive()` 在无读屏客户端时为 false，此时不发通知；接口由内部缓存管理，**不要 delete**；`installFactory()` 后注册者优先）
- **适用性**：项目 `known-issues` 里「读屏器验收开放」是实打实的未闭环项，而本项目恰好有大量自绘件（`status_pill`、`radar_chart`、`progress_delegate`、sparkline、托盘图标）——这些**不会**被 Qt 自动接管，读屏器看到的是空白或错误的角色。
- **预期收益**：把「开放项」变成一条可复跑的验收，而不是无限期挂账。最低目标是主窗口 tab 顺序合理、每个可交互控件有可读名称、进度表每行读得出仓库名与状态。
- **风险/成本**：半天量级。**不做**完整 a11y 重建：不给每个自绘件写 `QAccessibleInterface` 子类，只在必要处设 `setAccessibleName/Description`，最多给 1–2 个核心自绘件补角色。NVDA 是手工验收，无法进 CI。
- **可验证方式**：一段 20 行验收清单（NVDA 逐项朗读主窗口关键区），录一次结果进 `docs/验证记录-*.md`；再补一个离屏测试断言关键控件的 `accessibleName()` 非空（这条能进快集、能红）。

### P2-4 `pytest-testmon` 作为本地迭代加速器（**不进门禁**）

- **来源**：https://pypi.org/project/pytest-testmon/ （原文确认：版本 **2.4.0**，2026-11-25 发布，**要求 Python >= 3.11**，MIT，Development Status **4 - Beta**；首次 `pytest --testmon` 建立 `.testmondata` 依赖库，之后只跑受影响的测试）· https://github.com/tarpas/pytest-testmon （README 未列限制，只说「可靠性见外部链接」）
- **适用性**：快集 ≈8.8 分钟、慢集 ≈6 分钟、全量 2574 例——这是单人维护者最贵的资源。改一行后「该跑哪些测试」目前靠人脑 + `docs/验证记录-*.md` 记忆，正是 testmon 能替代的判断。
- **预期收益**：改一个模块后从「跑 8.8 分钟」降到「跑 20–60 秒」，同时不改变发布门禁口径。
- **风险/成本**：三条硬边界必须在采用时写进文档——① 只用于本地增量提示，**发布/CI 仍跑快集全量**；② 本机开发解释器需 ≥3.11（项目运行支持 3.10，不受影响，但 dev 环境要注明）；③ **出现一次假阴性（该跑没跑）即卸载**——本项目对「绿灯不可信」的容忍度为零。另外 `.testmondata` 要进 `.gitignore`。
- **可验证方式**：先在单模块上试两周，比对「testmon 选出的集合」与「人工判断的集合」差异；两周内无假阴性才推广。验收指标是事实性的：同一改动下 testmon 集合 ⊇ 人工集合。

---

## 3. P3（registry 记录即可）

### P3-1 代码签名可行性判定（Artifact Signing / 原 Trusted Signing）

- **来源**：https://learn.microsoft.com/en-us/azure/trusted-signing/overview （现名 **Artifact Signing**，文档 `ms.date: 2026-01-02`；原文确认：Basic / Premium 两种 SKU，资源 = Identity validation + Certificate profile，证书在 FIPS 140-3 Level 3 HSM 内生命周期全托管，支持 Public/Private Trust 与外部时间戳，**签名是 digest 签名、文件不出端点**）· https://azure.microsoft.com/en-us/pricing/details/trusted-signing/ （原文确认：Basic **5,000 签名/月**、Premium **100,000 签名/月**，各含 1 份/10 份证书 profile；**页面价格字段为 `$-` 占位，本轮未取到金额**；**个人开发者资格本轮未确认**）
- **适用性**：Windows 便携 exe 首次运行触发 SmartScreen 警告，是本项目面向小白用户最直接的流失点；已有 winget 清单，签名能让清单与二进制口径一致。
- **预期收益**：减少首启警告。但**签名 ≠ 立刻无警告**：SmartScreen 信誉仍需累积，这点必须提前说清，否则会被当成「签了还弹窗」的失败。
- **风险/成本**：Azure 订阅 + 身份验证（个人是否有资格 **未确认**，需先查 Identity Validation 页面）+ CI 里存签名凭据。对单人项目属于外部依赖与合规成本。
- **可验证方式**：先做 30 分钟的资格判定（能否通过 Identity validation 的类型/年限要求），不合资格就**归档结论**、不再投入——这一步本身就是交付物。

### P3-2 标准键位与焦点顺序审计

- **来源**：https://doc.qt.io/qt-6/qkeysequence.html （Qt 标准键 `QKeySequence::StandardKey`：`Find`/`Delete`/`Cancel`/`Refresh` 等按平台映射）
- **适用性**：桌面工具的键盘路径几乎没人系统测过；本项目有搜索框、列表、对话框、托盘，`Esc`/`Ctrl+F`/`Delete`/`Enter` 的行为不一致会直接表现为「这个工具怪怪的」。
- **预期收益**：一致性；顺带暴露焦点陷阱（Tab 进得去出不来）。
- **风险/成本**：小。**不做**命令面板（见 §4 丢弃理由）。
- **可验证方式**：一张 12 行的键位矩阵表 + 手工走查记录；可自动化的部分用离屏测试断言 `QAction.shortcut()` 与 `QKeySequence.StandardKey` 一致。

### P3-3 可访问性自动化冒烟的扩展（依赖 P2-3）

- **来源**：同 P2-3 的 Qt 文档（`QAccessible::isActive()`、`updateAccessibility()` 语义）
- **适用性**：只有 P2-3 落地后才有意义；避免现在就把 a11y 写进测试体系却在没有读屏客户端时全绿（假绿）。
- **预期收益**：把 a11y 退化成「控件静默失名」的静态可查项。
- **风险/成本**：极低，但**顺序依赖 P2-3**，单独做没有价值。
- **可验证方式**：`accessibleName()` 非空断言 + 一次 NVDA 对照。

---

## 4. 明确丢弃（不进入 backlog）

| 候选 | 丢弃理由 |
|---|---|
| 打包迁移到 Nuitka / PyOxidizer | PyOxidizer 生态已停滞；Nuitka 对本项目的收益（启动/体积）相对当前 `health_check` 实测（smoke 1.8–2.3 s）不足，而迁移会让全部打包守卫、`PyInstaller.spec`、winget 清单一起重做。 |
| 增量更新框架（TUF/tufup 类） | 已有主动更新检查 + 便携 zip；同一天发三版说明体积不是瓶颈，delta 更新的收益低于其密钥管理与分发链成本。 |
| 命令面板（Ctrl+K 类） | 功能面 <20 项，属于功能蔓延；同样的问题用标准键位 + 搜索框（P3-2）解决更便宜。 |

## 6. 主控评估（2026-11-26 Rev22 轮）

| 条目 | 裁决 | 理由 |
|---|---|---|
| P2-1 线程异常落盘 | **已采纳并落地** | `exc_dump.install_excepthook` 增装 `threading.excepthook`（同一 error.log，附线程名；不新增 except 分支，不破预算棘轮）；守卫 `tests/test_exc_dump.TestThreadExcepthook`（子进程正例 + 不装 hook 的阴性对照）。 |
| P2-2 WER LocalDumps | 暂缓 | 属维护者本机注册表配置（HKLM），非仓库内动作；需用户在本机执行并在真崩溃上验证。 |
| P2-3 a11y 最小验收 | 暂缓 | 半天量级 + NVDA 手工验收；与开放 beads `cuz` 合并处理，不拆两处挂账。 |
| P2-4 pytest-testmon | 暂缓 | 需两周观察期与 Python≥3.11 dev 环境，属本地工具偏好，留待用户决定。 |
| P3-1 签名资格判定 | 登记 | 资格判定是 30 分钟人工动作；不合资格即归档结论。 |
| P3-2 键位审计 | 登记 | 可与下次 UI 触碰批次合并。 |
| P3-3 a11y 自动化扩展 | 登记 | 依赖 P2-3 落地。 |

## 7. 本轮未覆盖的方向（下轮可选）

Qt 6 新 API 的实际收益（未取到证据，不臆测）、PyInstaller 之外的签名/公证流程细节、结构化日志的字段规范（项目已有 `applog` + 打码管线，改动收益不明）、多显示器/高 DPI 验收（需要真机，不是文档能解决的问题）。
