# 独立对抗性复审结论

**总体判定：本轮变更无生产回归、无假闭环主线，四条提交全部可放行；但发现 1 个证据漂移（应修）+ 3 个低危失真（建议修）。** 所有结论均经本机实测或远端 API 实证，非推断。

---

## A. 测试有效性 —— 修复方向正确且必要，但 commit message 的根因表述失真

**实测证据**（本机 git 2.53.0.windows.2，复刻 `_make_repo`）：

| 破坏对象 | `fsck --connectivity-only` 结果 | `_compute_health_text` 判定 |
|---|---|---|
| blob | rc=0，stderr 空 | “健康” ← **假绿** |
| tree | rc=128，fatal: loose object corrupt | “对象库异常” ✅ |
| commit | rc=128，fatal: loose object corrupt | “对象库异常” ✅ |

新测试循环 `cat-file -t` 定位 commit 再破坏，5 次重复试验全部稳定触发异常分支；ubuntu CI run 44481468922 中 `test_c6_health_check` 实跑 `Ran 4 tests OK`（日志实锤，非 skip）。

**发现 A-1【低危·事实失真】** `9197567` 提交信息称“Windows 本机 git 仍会 fatal”——实测 git 2.53 Windows 上 blob 损坏同样 rc=0 假绿，“blob 假绿”是 `--connectivity-only` 的通用行为而非 ubuntu 独有。真正的历史机制是：旧测试 `next(glob(...))` 破坏的是**字典序第一个松散对象**，而 `_make_repo` 的 tree（`9375a5…`）恰好恒排最前，所以测试一直绿；只有当某次仓库内容变化使 blob 排到第一位时才偶发假绿。新测试显式按类型定位，消除了对 glob 字典序的依赖——修法完全正确，但 docstring（`tests/test_c6_health_check.py:63-67`）里“Windows 本机 git 仍会 fatal”这半句应顺手更正，避免下个人按错误心智模型排查。

**发现 A-2【低危·存量】** `tests/test_c6_health_check.py:11` `import ast` 未使用（ruff F401），`1e6acf6` 版本即存在。CI 只查 `gcm/` 不会红，但既然本轮动了该文件，建议顺手删除。

无平台盲区：`cat-file -t` 输出纯 ASCII；`corrupted=False` 时 `assertTrue` 会 fail loud（git 整体不可用时 `_make_repo` 第一步就 `check=True` 炸，与原版行为一致，非新增盲区）。

## B. winget/发布链 —— exe 链四点闭合，zip 链三点闭合；但守卫在 CI 上恒 skip

**实测核验**（全部真实执行）：

| 环节 | 值 | 一致性 |
|---|---|---|
| 本机 `dist/Git-clone-Max.exe` | `25b42d25…` / 43,064,326 B | 基准 |
| winget 清单 `InstallerSha256` | `25b42d25…` | ✅ |
| `gen_winget_manifest.py --check` 本机 | rc=0 | ✅ |
| GitHub Release 资产 exe（实下载 43MB） | `25b42d25…` / 43,064,326 B | ✅ |
| Release body exe sha | 含 `25b42d25…`，旧 `fc340838…` 已清除 | ✅ |
| dist zip ↔ Release 资产 zip ↔ body zip sha | 均 `7cda5bbd…` / 82,521,623 B | ✅ |

**发现 B-1【中危·守卫缺口】** 防回归守卫没有 CI 兜底：
- `tests/test_g80_winget_manifest.py:56` `test_committed_manifest_matches_dist_exe` 在缺 `dist/Git-clone-Max.exe` 时 `skipTest`——CI 三平台 runner 均无 dist，**该守卫在 CI 上永远 skip**（run 44481468922 ubuntu 日志实锤 `skipped=1`）；
- `gen_winget_manifest --check` 未进任何 CI job（`ci.yml` 全文无引用）。

即清单漂移只能靠“本机恰好有 dist”发现——这次正是本机守卫抓到的，但下一次若在干净环境发版就没有防线。**建议修法**：在 `ci.yml` 的 `packaging` job（tag 触发、windows runner 必然构建出 dist）的 Health check 步骤后追加 `python scripts/gen_winget_manifest.py --check`。tag 构建时清单若未随产物更新会直接红，正好卡在发版门禁上，成本一行。

## C. 编码守卫 —— 完整满足 G66-4

新增的 `cat-file` 调用带 `encoding="utf-8", errors="replace"`（`tests/test_c6_health_check.py:76-77`）✅。`TestSubprocessTextDecoding`（AST 扫 gcm/scripts/tests 三目录）本机实跑 rc=0。且守卫链真实有效：中间版本 `9197567`（漏 encoding）被 CI 抓红（run 44479919661 failure），`501ecee` 修复后三平台全绿（run 44481468922 success）——这正是 G66-4 该起的作用。无遗漏调用。

## D. 流程风险 —— 1 个应修证据漂移 + 2 个低危

**发现 D-1【中危·证据与产物漂移，应修】** `evidence/health_rev18_rebuild.json` 的 zip 字段是错的：
- 已提交值：`zip_sha256=894e1339…`、`zip_size=82520089`
- 真实值（本机 dist 与 Release 资产实测一致）：`7cda5bbd…` / `82,521,623 B`

时间线还原：health_check 于 14:00:45 写盘（当时 zip 还是旧构建）→ 14:01:07 zip 重新打包 → 14:01:25 提交，**evidence 没有随新 zip 重跑**。exe 字段正确（exe 13:59:56 先于 evidence）。这正是项目反假闭环铁则要抓的“证据与产物不符”。**修法**：重跑 `python scripts/health_check.py --json evidence/health_rev18_rebuild.json` 后 amend 或追加提交（conservative profile 下等你拍板）。

**发现 D-2【低危·文档漂移】** `docs/验证记录-2026-11-14-Rev18.md` §8 止于 11-14 的 run 43605629455；本轮 11-19 的 winget 重对齐（run 44469225490 绿）、G66-4 抓红（run 44479919661）、修复后全绿（run 44481468922）及 fsck 测试修复均未登记。按项目惯例（§4 第 6 步）守卫类变更应补记，且 B-1/A-1 的教训（守卫 CI skip、`--connectivity-only` 不解 blob）值得进 known-issues。

**发现 D-3【低危·口径偏差】** `52cf50e` 提交信息说“补登记 5 条”，实际 `issues.jsonl` 新增 8 条（含 `b75` POSIX 测试缺陷、`n53` probe3）。台账本身自洽（24 闭环恢复 + 32 总数 + 仅 13k/cuz 两条真机验收 open，与 ICM 记录吻合），不算假闭环，但数字口径不准。

**生产代码零改动** ✅：diff 仅触及 `packaging/winget/*.yaml`、`tests/test_c6_health_check.py`、`.beads/*.jsonl`、`evidence/`。工作区未提交的 `evidence/realcalls-rev17.json` 改动（v9.21.0 握手记录）不属于本轮 diff，未纳入审查。

---

## 处置建议（按优先级）

1. **应修**：重跑 health_check 刷新 `evidence/health_rev18_rebuild.json` 的 zip sha/size（D-1）。
2. **建议**：packaging job 加 `gen_winget_manifest.py --check`（B-1，一行，堵住 CI 盲区）。
3. **顺手**：删 `tests/test_c6_health_check.py:11` 未用 `import ast`；更正 docstring 中“Windows 仍会 fatal”表述（A-1/A-2）。
4. **建议**：验证记录补 11-19 三次 CI 与 fsck 修复一段（D-2）。

需要我直接执行 1–3 吗？改动都很小且可逆。
