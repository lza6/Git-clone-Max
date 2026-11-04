# 取消整树终止 / 关窗不再冻结 / error.log 打码 / MCP 资源与提示词
取消时 POSIX 侧让子进程自成会话并按进程组终止（含「不对自己进程组开火」的防自杀保护），子模块更新补上单仓时间预算；关窗收尾先置灰+状态栏+隐藏再阻塞，预算从 12s 收到 ≤4s；error.log 落盘前统一 redact（它曾是唯一未打码的持久化出口），诊断包纳入 error.log；MCP initialize 的 resources/prompts 从空对象变成可用（3 个只读资源 + 2 条提示词，未知 URI 回 -32602）。
@verify tests/test_g84_shutdown_and_cancel（8 例）+ test_g84_exc_redact（6 例）+ test_g84_mcp_resources（7 例，真 stdio 握手）；定向回归 test_g67_closure/test_c1_wiring/test_submodule/test_core 91 例 OK
