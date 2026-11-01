# 验证矩阵（按改动面选最小充分集）

> 原则：**按风险适度验证**，不是每次都跑全量；但也不能只跑一个文件就说完成。

| 改动面 | 必跑 | 可选/扩大 |
|--------|------|----------|
| 纯文案 / i18n | `ruff` + 对应 UI 测试 | 快集 |
| 单个纯函数 | 该模块单测 | 快集 |
| 设置项 / UI 接线 | `tests.test_g69_regressions` + 对应 UI 测试 | 快集 |
| 引擎 / git service | `tests.test_engine` + `test_core`（慢集） | 快集 + CI |
| 数据层 / 迁移 | `test_db_*` + 干净检出 | 慢集 |
| 对外契约（CLI/MCP/HTTP） | `test_g56_*` + 真实调用一次 | CI |
| 并发 / 锁 / 幂等 | 对应并发测试**连跑 30 次** | 压测脚本 |
| 打包 / 版本 | `health_check.py` + 版本守卫 | 快集 |
| CI / 测试运行器 | 快集全量 + 干净检出对照 | 推 CI 验证 |
| **增删 `Settings` 字段** | `TestG69_18NoZombieSettingsFields` + `tests.test_extra`（旧配置兼容） | 快集 |
| **改版本号 / 发版** | 版本单源 11 项 + `tests.test_g44_url_updater` + `tests.test_extra`（版本时间炸弹高发区） | 快集 + 打包 |
| **往仓库加文件 / 改 .gitignore** | `tests.test_g79_repo_hygiene`（9 项） | — |
| **新增/修改设置项** | 三段接线测试（控件 → 落盘 → **消费方属性真的变了**）+ **跨重启闭环**：改值 → 关窗 → 新建 MainWindow → 断言引擎读到新值 | 快集 |
| **改模块行数 / 超长函数 / 测试文件数 / 技能包数** | `python scripts/doc_drift_check.py` → 必须 `DRIFT 0` | — |
| **新增 >100 行函数** | `tests.test_function_size_budget`（集合相等，需登记） | — |
| **改并发 / 调度策略** | `tests.test_c1_wiring`（并发语义）+ `test_g43_engine`（流控）+ `test_g52_engine_integration` | 快集 |
| **改重投 / host 落库** | `test_g67_closure`（既有契约！）+ `test_g73_dlq` + `test_g52_retry_queue_extra` | 快集 |

## 判定标签（结论必须带其一）
- **已验证**：实际运行并观察到结果
- **静态确认**：读代码确认
- **合理推断**：有间接证据
- **待验证**：缺环境/权限

## 反伪验证
- 断言必须能失败（不要 `assertTrue(True)`；计数断言要精确）。
- 守卫必须覆盖**生产路径**（如 Qt 窗口必须 `show()` 后测）。
- 静态守卫不要被注释/changelog 骗（用 AST）。
- **跨重启路径**要单独验：`_launch` 走的路径与启动期构造走的路径是两条，只测前者会漏掉后者的接线缺失。
- **审计脚本自身也要自证**：v9.12.0 的闭环抽查脚本用文本匹配判断「硬编码是否删除」，命中的是**注释里对修复的描述** → 误报 FAIL。改为 AST 后 PASS。
- **视觉验收不能只截图**：`ui_screenshot_audit.py` 只存图不断言，1366×768 的按钮裁切因此躲过 4 轮迭代。
  关键控件要加 `visibleRegion == rect` 的几何断言。

## 外部受限项
付费 API / 第三方凭证：用 mock 覆盖 success/timeout/rate_limit/malformed/retry_exhausted，
并写清「做到哪一步 / 剩余最后一步 / 缺什么 / 如何降级」。

## G71 新增改动面
| 改动面 | 必跑 |
|--------|------|
| EventBus / SSE / 引擎事件 | `tests.test_g71_event_stream` + `tests.test_engine` |
| 错误分类 / 重试队列 | `tests.test_g71_event_stream` + `tests.test_g52_failure_panel` + `test_g52_retry_queue_extra` |
| 原子写 / progress / history / lists | `TestAtomicWrites` + `tests.test_g67_closure` |
| 诊断包 / 事件面板 / 重试面板 | `TestDiagBundle/TestEventPanel/TestRetryPanelRows` |
| 设置页本地 API | `TestLocalApiSettings` + `TestGuiHostsLocalApi` + `test_g68_settings_ui` |
| `git/service._update` 拆分 | `tests.test_g58_mirror_failover` + `tests.test_engine` |

## G72 新增改动面
| 改动面 | 必跑 |
|--------|------|
| 日志/打码 | `tests.test_g72_hardening.TestLogRedaction/TestRedactG72` + `tests.test_applog` |
| 原子写/诊断包 | `TestAtomicHardening/TestDiagHardening` + `tests.test_g67_closure` |
| 重试 jitter/批量 | `TestRetryJitter` + `tests.test_g52_retry_queue_extra` |
| SSE 续传/背压 | `TestSseG72` + `tests.test_g71_event_stream` |
