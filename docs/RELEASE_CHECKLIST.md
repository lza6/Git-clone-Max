# Release 发布清单

> G29-7 · 每个版本发布前按顺序走查，全部勾选才能宣称发布闭环。
> 证据登记同步回 `计划书/验收清单.md`。

## 0. 前置

- [ ] `python -m unittest discover -s tests` → 全绿（记录 Ran N tests OK）
- [ ] `python -m coverage run --source=gcm -m unittest discover -s tests && python -m coverage report`
      → TOTAL ≥ 85%（若未达门禁，标注实际值）
- [ ] `git status` 无未登记改动；无硬编码密钥；`gcm/util/redact.py` 回归通过（token 三态打码）

## 1. 版本与打包

- [ ] `gcm/__init__.py`：`__version__` bump；`__changelog__` 追加一行（版本号 + 一句话能力 + 全量测试/覆盖率）
- [ ] `计划书/下一步改进指南.md`：对应条目标记 `[✅ 已落地]`
- [ ] `计划书/验收清单.md`：新增版本节登记（条目 | ✅ | 实现要点 | 验证命令与输出摘要 | 覆盖率证据）
- [ ] `python -m PyInstaller Git-clone-Max.spec`（onefile 主产物）
- [ ] 产物 sha256 记录（本地）

## 2. 冒烟

- [ ] 双击/命令行启动 exe（或 from source `python -m gcm`）→ 主窗口正常、托盘正常
- [ ] 下载中心：粘贴地址 → 开始 → 完成 → 状态正确回落
- [ ] 管理页：导入/更新/删除/备份 任一走查
- [ ] 关闭 → `tasklist` 无残留 `Git-clone-Max.exe` / `python -m gcm`（G21-4）

## 3. 发布

- [ ] `python scripts/publish_release.py --dry-run` → 校验通过（产物存在、远端状态、sha256 一致）
- [ ] 本地产物 sha256 与 Release body 中公布的 sha256 一致
- [ ] `python scripts/publish_release.py --tag vX.Y.Z`（或默认 tag）→ 上传 + 服务端下载校验 OK
- [ ] Release 页面核对：附件名、tag、body、sha256 行

## 4. 复验

- [ ] 从 Release 独立下载 exe，sha256 与本地产物一致
- [ ] 下载产物真实启动冒烟 OK
- [ ] tag 已推送：`git ls-remote --tags origin | grep vX.Y.Z`

---

*首个 v7 版本发布时按本清单走查并登记。*