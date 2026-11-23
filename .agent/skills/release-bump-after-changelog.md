# 发版收口：changelog 先行时的版本 bump

## 适用场景

单源版本项目里，发版顺序是先聚合 changelog 碎片、再 bump 所有版本载体；`bump_version.py` 自带 verify 时会把“目标版本大于 changelog 首条”作为下一版推导规则。

## 前置读取

- `scripts/bump_version.py` 的 CARRIERS / verify / main
- `scripts/changelog.py` 的 build
- `tests/test_g84_bump_version.py` 的载体与自证守卫
- `docs/RELEASE_CHECKLIST.md`

## 步骤

1. `changelog.py new` 生成碎片，`changelog.py check` 校验。
2. `changelog.py build --version X.Y.Z` 把首条改为目标版本。
3. `bump_version.py --to X.Y.Z --write --allow-dirty --no-verify` 写版本载体；不要再用 `--bump minor`，否则会推导成下一版。
4. 独立跑 `test_g66_patch.TestPackagingVersionSync` + `test_g81_changelog`；`test_g84_bump_version` 用于验证脚本本身，不作为“当前版本应为下一版”的依据。
5. 提交版本变更，打 tag，构建/健康检查/winget，再发布。

## 验收标准

- `gcm.__version__ == X.Y.Z`，所有载体同步。
- changelog 首条与 `__version__` 一致。
- CI 三平台与 e2e/smoke 全绿。
- Release 双资产服务端 sha256 与本地一致。

## 证据要求

保存 `health_check --json` 输出、`git ls-remote --tags`、CI run URL、Release URL。

## 回滚方式

revert bump 提交、删除错误 tag/Release（需用户确认），恢复 changelog 碎片归档。

## 常见坑

- 先 build changelog 再 `--bump minor` → 目标版本变成 X.Y.(Z+1)。
- bump 脚本 verify 递归调用自身守卫测试 → 生成失败日志，误判回滚。
- 工作区有未提交改动时 bump 拒绝执行；确认是自己的发版改动才用 `--allow-dirty`。
