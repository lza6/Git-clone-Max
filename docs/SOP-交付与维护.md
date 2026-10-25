# SOP：Git-clone-Max 交付与维护标准（2026-10-13 校准）

> 面向独立开发者/交接方：本文档定义「改代码→验证→发布→维护」的标准流程，避免重复踩坑、保证交付质量。
> 版本号不写死在本文件：唯一事实源是 `gcm/__init__.py` 的 `__version__`。

## 1. 本地环境

| 项 | 值 |
|----|----|
| Python | ≥3.10（本机 3.14.3） |
| PyQt6 | ≥6.6（本机 6.11.0） |
| git | 系统已安装（Windows 需在 PATH） |
| 虚拟环境 | `.venv\Scripts\python.exe`（bat 启动器自动创建） |

## 2. 开发 → 验证 标准流程

```
0. 读 `gcm/__init__.py` 的 `__changelog__` + `CLAUDE.md` §6 定位未做的项（禁止重复实施已落地项）
1. 先写测试（RED）→ 实现（GREEN）→ 重构（IMPROVE）
2. 单测验证：python -m unittest tests.test_xxx -v
3. 质量闸：ruff check gcm scripts && mypy gcm
4. 覆盖率（快集口径，门禁 Windows 81 / POSIX 80）：
            python -m coverage run --source=gcm scripts/ci_run_fast.py
            python -m coverage report --fail-under=81
5. 真实 E2E：python scripts/e2e_smoke.py（本地裸仓）
6. 提交：按主题拆分 commit（docs/feat/test/fix），不用 git add .
7. 推送 main：git push origin main（禁 -f）
8. 发布：见 §4
```

## 3. 关键命令速查

| 操作 | 命令 |
|------|------|
| GUI 启动 | scripts\启动Git-clone-Max.bat |
| CLI 批量克隆（JSON） | python -m gcm --cli --json urls.txt |
| CLI 诊断 | python -m gcm --cli --diag --json |
| CLI HTTP API | python -m gcm --cli --serve 127.0.0.1:8765 |
| MCP server | GCM_MCP_ENABLE=1 python -m gcm.mcp_server |
| 变异测试 | python scripts/run_mutation_smoke.py |
| 内存基线 | python scripts/mem_baseline.py --batch 100 |
| 健康检查 | python scripts/health_check.py --json dist/health_check.json |

## 4. 发布流程（vX.Y.Z）

1. `gcm/__init__.py` bump 版本 + changelog 追加一行
2. 提交 + 推送 main（git push origin main）
3. `git tag vX.Y.Z && git push origin vX.Y.Z`
4. 构建：`python -m PyInstaller --noconfirm --clean Git-clone-Max.spec`
5. zip：`python scripts/build_portable_zip.py`（**别手工 Compress-Archive** —— 手工产物会悄悄过期；
   发布前用 `--check` 校验 zip 不比来源目录旧）
6. 健康检查：`python scripts/health_check.py --json dist/health_check.json`（版本一致/启动 OK）
7. Release：`gh release create vX.Y.Z --title ... --notes ...` + `gh release upload`
8. 验证：`gh release view vX.Y.Z --json assets`（digest 与本地产物 sha256 一致）

## 5. 维护排障

| 症状 | 排查 | 处置 |
|------|------|------|
| 启动崩溃 | data/error.log + app.log | 看 exc_dump 兜底日志 |
| 克隆失败 | CLI --diag 一键体检 | 按排错地图（帮助对话框）处理 |
| 卡死/挂起 | 全量测试分块跑定位 | 检查 QEventLoop/线程泄漏（G50-1 隔离铁律） |
| 覆盖率下跌 | coverage report --show-missing | 补新代码测试，勿改 omit 刷门禁 |
| SQLite 锁 | busy_timeout=30000 + BEGIN IMMEDIATE | 引擎单线程写库，勿跨线程共享连接 |

## 6. 交接清单（给下一位开发者）

- [ ] 新增能力已在 `docs/验证记录-*.md` 留下证据行；`__changelog__` 已追加
- [ ] docs/agent-contract.md 与 CLI/MCP/HTTP 实际行为一致
- [ ] docs/archive/working/ 下的 workflow_status 归档登记最新里程碑（2026-10-25 起不再常驻根目录）
- [ ] 无未提交工作区改动（git status 干净）
- [ ] 全量测试绿 + 快集覆盖率达门禁（Windows 81 / POSIX 80，见 `docs/RELEASE_CHECKLIST.md`）+ ruff 0 + mypy 0
