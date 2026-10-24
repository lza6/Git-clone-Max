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

## 判定标签（结论必须带其一）
- **已验证**：实际运行并观察到结果
- **静态确认**：读代码确认
- **合理推断**：有间接证据
- **待验证**：缺环境/权限

## 反伪验证
- 断言必须能失败（不要 `assertTrue(True)`；计数断言要精确）。
- 守卫必须覆盖**生产路径**（如 Qt 窗口必须 `show()` 后测）。
- 静态守卫不要被注释/changelog 骗（用 AST）。

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
