# 新增 API（CLI / MCP / HTTP）

## 适用场景
给本项目加一个对外可调用的入口，或修改现有契约。

## 前置读取（按序）
1. `.agent/memory/tested-scope.md` — 相关验证是否已跑过
2. `.agent/memory/change-index.md` — 该模块最近改了什么
3. `docs/agent-contract.md` — 现有契约（退出码 / JSON schema / 打码规则）
4. `skills/gcm-feature/SKILL.md` — 七步工作流与历史坑

## 步骤
1. **定契约先于写代码**：参数、默认值、必填、返回字段、错误码、超时、鉴权、幂等。
2. **默认只读**：写操作必须显式开关（`--allow-mutate` / `--allow-export` / `GCM_MCP_ENABLE=1`）。
3. **版本单源**：契约里的版本号只能来自 `gcm.__version__`（有守卫）。
4. **脱敏**：任何出站文本过 `gcm/util/redact.redact`。
5. **数据目录**：只能走 `gcm.__main__.get_data_dir()`，不得自拼 `<repo>/data`。
6. 实现 → 单测（含错误路径）→ 文档同步（`docs/agent-contract.md`）→ changelog。

## 验收
```bash
curl/CLI 真实调用一次，贴出返回；退出码符合契约
python -m unittest tests.test_g56_contract tests.test_g56_cli_json   # 或对应模块
python -m ruff check gcm && python -m mypy gcm
```
- 至少覆盖：正常 / 参数缺失 / 非法参数 / 权限不足 / 超时或外部失败。
- 契约文档里的示例必须能**照抄跑通**。

## 回滚
契约变更需向后兼容；破坏性变更必须升 minor 并在 changelog 写明。

## 常见坑（本项目真实踩过）
- 契约文档版本号与 `__version__` 漂移 → 有守卫 `test_contract_doc_version_matches_package`。
- 参数错误抛出未捕获异常 → 打断 stdio 通道。必须转成结构化错误。
- 非 UTF-8 控制台写中文 → `UnicodeEncodeError`。stdio 服务必须 `reconfigure(encoding="utf-8")`。
- 自拼数据目录 → 安装版读到 `_internal/data` 的空库。
