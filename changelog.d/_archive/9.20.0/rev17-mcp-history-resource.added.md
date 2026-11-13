# MCP 新增 gcm://history 只读资源：最近 50 条全库同步记录
agent 无需翻库即可判断「上次跑成没成」；JOIN owner/repo、出站 redact、空库返回空数组不建文件（_EmptyDB 契约保持）。agent-contract §4.2 同步。
@verify tests/test_g84_mcp_resources 8 例真实 stdio 握手（含空库读 history）
