# 发布检查清单

## 前置
`.agent/memory/tested-scope.md` 全部通过；工作区干净。

## 步骤
```bash
# 1) 质量闸
./.venv/Scripts/python.exe -m ruff check gcm
./.venv/Scripts/python.exe -m mypy gcm

# 2) 快集全量（含覆盖率门禁口径）
rm -f .coverage
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m coverage run --source=gcm scripts/ci_run_fast.py
./.venv/Scripts/python.exe -m coverage report --include="gcm/*"

# 3) 版本单源（必须全绿）
QT_QPA_PLATFORM=offscreen ./.venv/Scripts/python.exe -m unittest   tests.test_g66_patch.TestContractVersionSingleSource   tests.test_g66_patch.TestPackagingVersionSync tests.test_g67_docs_freshness

# 4) 构建 + 健康检查
./.venv/Scripts/python.exe -m PyInstaller --noconfirm Git-clone-Max.spec
./.venv/Scripts/python.exe scripts/build_portable_zip.py
./.venv/Scripts/python.exe scripts/health_check.py --json dist/health_check.json
# 期望：version 与 __version__ 一致 · started_ok true · artifacts_exist true

# 5) 发布
./.venv/Scripts/python.exe scripts/publish_release.py --tag vX.Y.Z
```

## 验收
- Release 双产物存在且**服务端 sha256 与本地一致**（脚本会自动校验）。
- tag 指向的提交 `__version__` == tag（`git show <tag>:gcm/__init__.py`）。
- CI 三平台全绿。

## 回滚
Release 可删除重发；tag 不重打（需要时升 patch）。

## 常见坑
- 旧 exe / 旧 zip 混在一起发布 → `build_portable_zip.py --check`。
- 版本号只改 `__version__` 不改 spec/iss/pyproject → 守卫会红。
- 发布说明里混入模板维护者注 → 用 HTML 注释包裹。
