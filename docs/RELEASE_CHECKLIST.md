# Release 发布清单

> G29-7 · 每个版本发布前按顺序走查，全部勾选才能宣称发布闭环。
> 证据登记写入 `docs/验证记录-<日期>.md`（命令 + 真实输出摘要）。
>
> ⚠️ **不要只看本清单的正向勾选**：发布前必须实际打开 GitHub Actions 看三平台结论。**不得**以本清单全勾为据宣称「CI 绿」——
> 发布前请实际打开 GitHub Actions 看三平台结论。

## 0. 前置

- [ ] `python -m unittest discover -s tests` → 全绿（记录 Ran N tests OK）
- [ ] `python -m coverage run --source=gcm scripts/ci_run_fast.py && python -m coverage report`
      → 快集 TOTAL ≥ 门禁（Windows 81 / POSIX 80，见 `.github/workflows/ci.yml` 的 `COVERAGE_FAIL_UNDER`）
- [ ] `git status` 无未登记改动；无硬编码密钥；`gcm/util/redact.py` 回归通过（token 三态打码）
- [ ] `python -m unittest tests.test_g66_patch` → 守卫全绿（版本单源 / 打包版本同步 / 子进程解码 /
      时间炸弹 / HTTP 回环 / 钩子注入 / 跨线程 Qt 控件；**用例数不写死在本文件**，避免文档漂移）
- [ ] `python -m unittest tests.test_g67_docs_freshness` → 文档版本与路径新鲜度守卫绿

## 1. 版本与打包

> **构建前必须先关闭正在运行的应用**（G66 实测坑）：
> `dist\Git-clone-Max.exe` 正在运行时，PyInstaller 覆盖会失败并报
> `PermissionError: [WinError 5] 拒绝访问: ...\dist\Git-clone-Max.exe`
> （同理：`scripts/health_check.py` 会拉起 exe 做存活验证，跑完后可能留下
> 托盘实例，下次构建就会被它挡住）。构建前先确认：
> ```bash
> tasklist /FI "IMAGENAME eq Git-clone-Max.exe"      # 应无输出
> ```
> 有残留则 `taskkill /PID <pid> /F`（应用空闲时安全；有进行中任务时它会落
> progress.json，可用 G52-2 续跑找回）。


- [ ] `gcm/__init__.py`：`__version__` bump；`__changelog__` 追加一行（版本号 + 一句话能力 + 全量测试/覆盖率）
- [ ] `gcm/__init__.py`：`__changelog__` 追加本版本一行
- [ ] `docs/验证记录-<日期>.md`：新增版本节登记（条目 | 实现要点 | 验证命令与输出摘要 | 覆盖率证据）
- [ ] `python -m PyInstaller Git-clone-Max.spec`（onefile 主产物）
- [ ] `python scripts/build_portable_zip.py`（生成/刷新便携版 zip；**必须在 PyInstaller 之后跑**）
- [ ] `python scripts/build_portable_zip.py --check` → `[OK]`（拦住「新 exe + 旧 zip」）
- [ ] 产物 sha256 记录（本地）

## 2. 冒烟

- [ ] 双击/命令行启动 exe（或 from source `python -m gcm`）→ 主窗口正常、托盘正常
- [ ] 下载中心：粘贴地址 → 开始 → 完成 → 状态正确回落
- [ ] 管理页：导入/更新/删除/备份 任一走查
- [ ] G71 新入口：设置页「🧾 事件流」可打开并显示批次/任务事件；「重试队列」可打开
- [ ] G71 本地 API：设置页开启「本地 API / 事件流」→ `curl -N http://127.0.0.1:8765/api/events` 有 `retry:`/连接帧；关闭设置后端口释放
- [ ] G71 诊断包：下载中心「诊断包」/ 诊断对话框导出 zip，解压含 `manifest.json`/`events.jsonl`，且不含明文 token
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