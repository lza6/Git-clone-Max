# ADR-004：生态 agent 化（CLI --json / MCP / HTTP API 只读优先）

- 状态：Accepted（v8.1.0 / G56）
- 背景：让批量克隆/归档/报表/诊断可被 agent/脚本/第三方调用，但外部写操作风险高
- 决策：
  - CLI --json：顶层 schema {version,ok,counts,results,invalid} + 退出码 0/1/2/3，错误统一 {code,message,redacted}
  - MCP server：只读工具（list_repos/search_repos/status/export_report 需显式 enable），`GCM_MCP_ENABLE=1` 才启动
  - HTTP API：只绑 127.0.0.1、无 CORS、默认只读；`--allow-mutate` 才放行写 + 审计落盘
  - 契约文档 docs/agent-contract.md 与实现一致（实测验证）
- 后果：agent 可安全消费；写操作显式授权 + 审计；契约可测试
- 相关：G56-1..5、docs/agent-contract.md
