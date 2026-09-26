# ADR-003：凭据加密落盘 + redact 全出口

- 状态：Accepted（v5.1.0 / G06 / G28）
- 背景：token/host_tokens/webhook token 明文落盘风险
- 决策：
  - token 加密：DPAPI（Windows）→ Keyring → XOR 降级链（gcm/app/token.py）
  - host_tokens 逐条目加密；webhook token 同规则
  - redact() 全出口打码：日志/CLI --json/MCP/HTTP API/诊断复制
- 后果：磁盘无明文凭据；日志/导出无泄漏；host 白名单限制凭据仅发登记主机
- 相关：G28-1、G38-1、G44、G56 契约
