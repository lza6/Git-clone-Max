# Git-clone-Max

> GitHub 仓库批量并行下载 / 增量更新 / 入库追踪 的桌面工具（PyQt6）。
> **当前版本：v9.10.2**（G79b 终局审计：修复紧凑布局「编辑」孤儿按钮与同版本断言退化、更正与代码矛盾的 GC 过期处置；逐版本历史见 `gcm/__init__.py` 的 `__changelog__`）
> **v5.0：多平台直克隆** — GitLab / Gitee / Codeberg / Bitbucket / 自建主机（HTTPS/SSH/短格式/子组）同样支持。
> 克隆目录一律 `host__作者__仓库`（GitHub 为 `作者__仓库`），本地改动永不覆盖。

## 🚀 懒人小白一键使用

**双击 `dist\Git-clone-Max.exe`** 即可 —— 无需安装 Python、无需配置环境、无需手动安装依赖。

> ⚠️ 说明：exe 为 **单文件自包含** 打包（含 Python 运行时 + PyQt6 + 全部代码）。
> 首次双击 Windows SmartScreen 可能提示「未知发布者」，点 **更多信息 → 仍要运行** 即可。
> 软件运行时会调用系统已安装的 `git`（若未安装，请在 https://git-scm.com/download/win 安装一次）。

## 快速开始（源码运行）

双击 **`scripts\启动Git-clone-Max.bat`**（或右键 PowerShell 运行同目录 `.ps1`）。
启动器会自动：

1. 检测 `git` 与 `python`（py 启动器优先）
2. 在项目根创建虚拟环境 `.venv`（若不存在）
3. 自检 `PyQt6`，缺失则自动 `pip install -r requirements.txt`
4. 以 `python -m gcm` 启动 GUI

## 多平台运行（Linux / macOS 源码）

> 本项目测试在 Windows 上完成；Linux / macOS 上通过源码运行同样支持。
```bash
# 1. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate        # Linux / macOS

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动
python -m gcm
```

## uv 现代环境（G49-4）

> uv 自动解析 pyproject.toml（readme/urls/classifiers 元数据已补全），零手动 venv。
```bash
# GUI 启动
uv run python -m gcm

# CLI 批量克隆（无 GUI 离屏；地址来自 stdin（-）或文件）
printf 'https://github.com/lza6/Git-clone-Max\nhttps://github.com/vercel-labs/skills\n' | uv run python -m gcm --cli -
# 指定克隆输出根目录：--dir <dir>（默认 当前目录/clones）
uv run python -m gcm --cli --dir D:/repos urls.txt

# 便携模式：数据目录固定在可执行文件同级 data/（默认非便携走 %APPDATA%/Git-clone-Max，旧 data 沿用）
uv run python -m gcm --portable
```

> 注：`QT_QPA_PLATFORM=offscreen` 仅用于**无显示器环境 / CI**（如 `QT_QPA_PLATFORM=offscreen python -m gcm`），正常桌面环境无需设置。

## 功能

| 功能 | 说明 |
|------|------|
| 多平台 | GitHub / GitLab / Gitee / Codeberg / Bitbucket / 自建主机，HTTPS + SSH + 短格式 + 子组 + 自定义主机白名单，直接复制地址即下载 |
| 指定版本 | `owner/repo@v1.2.0` 语法指定分支/标签（`clone -b`），不改动仓库默认检出 |
| 仓库级 SSH 私钥 | 仓库详情里可为**单个仓库**指定 SSH 私钥（只记录**路径**，不保存私钥内容）；优先级 仓库级 > 全局设置；清除即回落全局。校验会拒绝「把私钥内容粘进来」、不存在、是目录、非私钥文件 |
| 按 host 凭据 | 设置页「按平台凭据」`host=token` 逐行注入（github→Bearer / gitlab→PRIVATE-TOKEN / 未登记不发）；`host_tokens` **逐条目加密落盘**，杜绝明文 |
| 镜像前缀 | **多镜像备援**：内置 4 个实测可用的国内 GitHub 镜像（默认启用），也可在设置页按 `host=镜像1,镜像2` 自定义；克隆前用 `git ls-remote` 短超时探测，取最快的可用镜像，**全部不可用/没有该仓库时自动回退源站**；有令牌/全局认证头时**不走镜像**（防凭据泄漏）；镜像克隆成功后 origin 立即恢复为源站（镜像只作取数通道）；SSH 不拼接；CLI 可用 `--no-mirror` 关闭 |
| 远端预检 | 「克隆前做远端可达性预检（10s）」开关，断网 10s 内明确提示不白等超时 |
| 浅克隆加速 | 满量 / 浅克隆（可调 depth）/ **单分支浅克隆**（`--depth N --single-branch`）；弱网自动降级 treeless → **depth1 保命模式** |
| 强制 IPv4 | 「强制 HTTP/1.1」开关，规避部分网络下 HTTP/2 兼容问题 |
| 批量并行 | 线程池 ≤32 并发（默认 8，设置页可调 1–32），仓库互不阻塞，重复地址自动去重 |
| 跨 host 命名 | `host__作者__仓库` 防同名冲突；GitHub 保持 `作者__仓库` |
| 断点续传 | 进度周期落盘（原子写）+ 取消缓存；中断后已完成的仓库下一轮自动跳过、其余继续 |
| 并发安全 | 引擎统一调度：SQLite `busy_timeout` 30s + 进度文件互斥锁，高并发下不闪退 / 锁死 |
| 增量更新 | 已存在 → `git fetch --prune` → `merge --ff-only`；无法快进退化为 rebase |
| 冲突保护 | 本地有改动且与远端分叉 → **标记冲突、保留本地、绝不覆盖** |
| 失败恢复 | 单个仓库失败不中断整体；网络类失败自动降级；平台限制错误明确提示不无效重试 |
| 三段式错误 | 每个失败给出「发生了什么 / 为什么 / 怎么办」；429/5xx 自动重试，401/403/404/磁盘满/平台限制不无效重试 |
| 重试队列可见 | 下载中心「重试队列」：失败次数、下次重试时间、错误类别、原因与建议；支持立即重试 / 移除 / 全部重试 / 清空；「显示已放弃」可查看超过上限的 DLQ 任务并重新入队（attempts 重置） |
| 事件流（SSE） | 设置页可开启「本地只读 API（含 SSE）」由 **GUI 进程内**托管（默认关，仅回环）；`GET /api/events` 实时推送批次/任务/进度/结果/错误/重试事件，`/api/events/snapshot` 供轮询型 agent。设置页「🧾 事件流」可直接查看结构化执行轨迹（类型过滤/自动刷新/复制 JSON） |
| 诊断包 | 一键导出 zip：状态报告 + 最近事件（黑匣子）+ app.log/notify.log 尾部，全部已打码 |
| 单任务控制 | 进度表搜索过滤 / 表头排序 / 单仓库暂停 / 右键重试、复制详情、打开目录 |
| 任务清单 | 输入区右键保存/加载任务清单（`data/lists`）；批量打标签 + 导出所选 CSV |
| 子模块 | 设置开关拉取 `--recurse-submodules`，工作区完整 |
| 代理/限速 | 手动填写或一键自动检测（环境变量 / Windows 注册表）；下载限速（lowSpeedLimit） |
| 仓库管理 | 标签 / 收藏置顶 / 黑名单（一键更新排除）/ 过期高亮 / 搜索过滤 / host 分布统计 |
| 数据洞察 | 统计中心（全局 + host + 30 天趋势 + 用量 + **6 维评分卡雷达图**）、报表筛选导出（CSV / Markdown / Excel(xlsx)）、每仓库备注、远端检查 |
| 任务入口 | GitHub Star 列表导入、本地仓库扫描导入、剪贴板监听、历史记录回填 |
| 多主题 | Deep / Light / Nord / 高对比（HC）四套主题 + 蓝/紫/青强调色 + 跟随系统深浅色（auto），重启恢复 |
| 首启向导 | 三步设置下载目录/并发/代理，零门槛上手 |
| 快捷键/动效 | Ctrl+Alt+S/U/M 全局热键；Ctrl+1/2/3 切页签；完成行动效淡出（可关，限 8 并发）；窗口尺寸与页签记忆 |
| 响应式窗口 | 1024/1280/1440 三档逻辑断点；主操作区常驻；快捷填充横向滚动；管理/设置窄屏分行；对话框按屏幕 90%×85% 自适应；100%–200% DPI |
| 进度表性能 | 进度列 delegate 自绘（500 行不创建 500 个 QProgressBar）；250ms 合批；批量插行关排序 |
| 可靠性 | token 加密落盘（有 DPAPI/keyring 时真加密；否则可逆混淆并在日志显式告警）、单实例锁、全局异常兜底 `error.log`、settings 备份恢复、数据库自动迁移备份 |
| 数据库 | SQLite `repos.db`（WAL）记录元数据 + `sync_history` 全量同步快照健康治理 |
| 一键更新 | 「仓库管理 → ⟳ 一键更新全部」对库中所有仓库增量同步（黑名单排除） |
| 黑匣子日志 | 实时 git 原生输出 + 关键字高亮 + 导出 txt（结构化 app.log + error.log 兜底；**日志落盘即打码**，token/URL 凭据不落明文） |

## 本仓库的 AI 协作约定

本项目在 `.agent/` 下维护**记忆、规则与技能**（供人或 AI 协作者开工前读取）：

- `.agent/rules.md` —— 协作铁律（验收标准、反假闭环、回滚）
- `.agent/memory/` —— 已测试范围、已知问题、关键决策、变更索引、审计历史
- `.agent/skills/` —— 加 API / 加功能 / 发版 / 回滚 / 验证矩阵 / 审查 / 审计 / 并发检查 / 计划文档清理
- `policy.md` —— 交付门禁：什么条件下允许宣称「完成」
- `test_ledger.md` —— 接缝注册表（S01–S30）与每条链路由哪个测试证明
- `evidence/` —— 每个任务节点的真实命令与输出证据

改动前请先读 `.agent/memory/tested-scope.md`，避免重复跑同一批验证；
结论请按 `policy.md` 的四档置信标签（已验证 / 静态确认 / 合理推断 / 待验证）表述。

## 目录结构

```
Git-clone-Max/
├── Git-clone-Max.spec       PyInstaller 打包配置（已入库，版本资源随 __version__）
├── dist/                    构建产物：Git-clone-Max.exe（onefile）+ Git-clone-Max-portable/（onedir）
├── gcm/                     主包
│   ├── __main__.py          入口（python -m gcm）；--portable / --cli / --minimized
│   ├── models.py            数据模型
│   ├── cli.py               CLI：--cli [--json] [--diag] [--serve H:P] [--allow-mutate]
│   ├── mcp_server.py        MCP stdio server（只读；GCM_MCP_ENABLE=1 显式启用）
│   ├── http_api.py          本地 HTTP API（只读；强制回环绑定；含 SSE 事件流）
│   ├── reports.py           报表导出（CSV / Markdown / xlsx）
│   ├── app/
│   │   ├── engine.py        统一调度引擎（并发/去重/进度/取消/事件发布）
│   │   ├── event_bus.py     进程内事件总线（回放/过滤/打码；SSE 数据源）
│   │   ├── errors.py        三段式错误分类（可重试 vs 永久失败）
│   │   ├── batch_state.py   批次状态机（转换校验 + 幂等计数）
│   │   ├── diag_bundle.py   诊断 zip（报告 + 事件 + 日志尾部）
│   │   ├── url_lib.py       通用 Git URL 解析（多平台）/ 跨 host 命名
│   │   ├── diag.py          网络/环境诊断
│   │   ├── alerts.py        阈值告警扫描 + 定时报表
│   │   ├── im_notify.py     企业 IM 推送（钉钉/飞书/企微）
│   │   └── worker.py        并行 worker（git 同步 + 结果回传）
│   ├── db/                  SQLite（repo_db / settings / lists / history / runtime_sessions）
│   ├── git/service.py       git 操作（clone/fetch/merge/rebase/冲突/平台限制识别）
│   ├── i18n/                轻量 i18n（zh/en）
│   └── ui/                  主题 + 三 Tab 主窗口 + 设置/统计/详情/重试队列/诊断/本地扫描
├── skills/                  agent 技能包（gcm-clone/archive/report/diag/resume，各含 SKILL.md）
├── scripts/                 启动器（bat/ps1）+ E2E 冒烟 / 健康检查 / 发布 / 基准 / CI 快集
├── docs/                    架构 / ADR / 数据模型 / agent 契约 / SOP / 发布清单
├── tests/                   单元 + 集成 + 真实 git E2E（140 个测试文件）
├── requirements.txt         运行时依赖（PyQt6）
└── pyproject.toml           项目元数据 + ruff / mypy 配置
```

## 重新打包 exe

使用入库的 spec（版本资源、Qt 模块裁剪、onedir 便携版均已配置好）：

```bash
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm Git-clone-Max.spec
```

产出：`dist/Git-clone-Max.exe`（onefile 主产物）+ `dist/Git-clone-Max-portable/`（onedir 便携版）。

便携版 zip 是发布的双产物之一，用仓库脚本生成（**别手工压缩** —— 手工产物会悄悄过期）：

```bash
python scripts/build_portable_zip.py           # 生成 dist/Git-clone-Max-portable.zip
python scripts/build_portable_zip.py --check   # 发布前校验 zip 是否比来源目录旧
```

> 实测教训：该脚本此前不存在，导致 dist 里的 exe 已重建到新版本而 zip 仍停在两周前，
> 发布时会传出「新 exe + 旧 zip」的不一致产物。`--check` 就是为拦住这种情况。

> `Git-clone-Max.spec` 的 `filevers/prodvers/FileVersion/ProductVersion` 必须与 `gcm/__init__.py` 的
> `__version__` 一致，否则 Windows exe 属性会显示旧版本 —— 由 `tests/test_g66_patch.py::TestPackagingVersionSync` 强制校验。

> **v5.0 起支持多平台直克隆**：GitLab / Gitee / Codeberg / Bitbucket / 自建主机，HTTPS + SSH + 短格式 + 子组均可；命名 `host__作者__仓库`（GitHub 保持 `作者__仓库`）。

## Agent / 脚本接入（G56）

三类入口，**全部默认只读**，统一契约见 [`docs/agent-contract.md`](docs/agent-contract.md)：

```bash
# 1) CLI 批量克隆（--json 时 stdout 只输出 JSON，进度走 stderr）
python -m gcm --cli --dir ./clones urls.txt
python -m gcm --cli --json --dir ./clones urls.txt
cat urls.txt | python -m gcm --cli --json --dir ./clones -

# 2) 网络/环境诊断（可配 --json）
python -m gcm --cli --diag --json

# 3) 本地只读 HTTP API（强制回环绑定；--allow-mutate 才放行写）
python -m gcm --cli --serve 127.0.0.1:8765

# 3.1) 实时事件流（SSE：批次/任务/进度/结果/错误/重试）
curl -N "http://127.0.0.1:8765/api/events?replay=20&heartbeat=15"
curl "http://127.0.0.1:8765/api/events/snapshot?limit=100&types=task_error"
```

退出码：`0` 全成功 / `1` 有失败 / `2` 无有效输入（MCP 未启用另为 `3`）。

MCP server（Claude Desktop / Cursor 可直接注册）：

```bash
GCM_MCP_ENABLE=1 python -m gcm.mcp_server        # 默认只读
GCM_MCP_ENABLE=1 python -m gcm.mcp_server --allow-export
```

> **安全边界（G66-6 起强制）**：本地 HTTP API 无鉴权，因此 `--serve` 只接受回环地址
> （`127.0.0.1` / `127.0.0.2` / `127.0.0.3` / `localhost`）；传 `0.0.0.0` 等非回环地址会被拒绝并以退出码 2 退出。
> 事件流（SSE）同样只走回环，且事件在入总线前已 redact 打码；**不要**把事件流原样转发到公网。
> IPv6（含 `::1`）暂不支持（本地 API 基于 AF_INET），传入会明确报错而非崩溃。
> 需要远程访问请自行用带鉴权的反向代理转发到回环地址。

仓库内 `skills/` 提供 5 个开箱技能包（克隆 / 归档 / 报表 / 诊断 / 续跑），每个含 `SKILL.md` + 可执行脚本，供 Claude Code / Codex 直接调用。

## 测试

```bash
python -m unittest discover -s tests -v
```

（`tests/test_core.py` 内置本地裸仓库全程 E2E：克隆 → 增量子提交 → 已最新 → 冲突保留 → 残留目录重建。）

## 覆盖率与回归（G45-1）

本地复现 CI 门禁（**快集** = CI 主矩阵实际入口；**全量** = 快集 + 慢集〔真实 git E2E〕。
耗时基线见 `docs/慢测定位.md`，这里不写死分钟数以免漂移。Windows 需把 git 加入 PATH，自定义安装如 scoop 请先发行 `$env:PATH="<git 安装目录>\cmd;$env:PATH"`）：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"

# 快集（CI 主矩阵实际入口；覆盖率门禁 Windows 81 / POSIX 80）
python -m coverage run --source=gcm scripts/ci_run_fast.py
python -m coverage report --fail-under=81

# 全量（含慢集：真实 git E2E）
python -m coverage run --source=gcm -m unittest discover -s tests
```

- 覆盖率门禁为**快集口径**：Windows 81 / POSIX 80（`ci.yml` 主矩阵按 `runner.os` 取值）。
  慢集（真实 git E2E）由独立 `slow` job 承接，不计入主矩阵门禁。
  `pyproject.toml` / `.coveragerc` 不设 `fail_under`，阈值只在 CI 与本文声明。
- 口径拆分见 `docs/慢测定位.md`；改动某区域时**优先按 `docs/验证记录-*.md` 的对应批次跑定向回归**，不必每次全量。
- v7.6.0（G45-6）起 `gcm/ui/main_window.py` 已拆分：
  `manage_panel.py`（管理页 UI/动作+信号回传）、`progress_table.py`（进度表/过滤/结果回填/引擎收尾）、
  `settings_panel.py`（设置回调）；控件名/方法名/信号全兼容，行为零变化。

## 后台挂机 / 长期运行

- **内存**：日志环形缓冲上限 3000 条自动裁剪；worker 任务完成后自动释放；不用全局定时器轮询。
- **并发安全**：进度文件进程级锁 + SQLite `busy_timeout=30000`；并发 32 实测无闪退 / 锁死。
- **退出保护**：任务运行中关闭会二次确认，确认后取消剩余任务并安全关闭数据库。
- **开机自启**（可选）：设置页勾选「开机自启」会创建任务计划登录时启动。

## 无障碍（G57-5 / G70）

- **键盘路径**：全窗口 Tab/Shift+Tab 可达；全局热键 `Ctrl+Alt+S`（开始/取消）、`Ctrl+Alt+U`（一键更新）、`Ctrl+Alt+M`（显示窗口）；页签 `Ctrl+1/2/3`；字号缩放 `Ctrl+=` / `Ctrl+-` / `Ctrl+0` 复位。
- **主题与对比**：Deep/Light/Nord/HC 四套主题 + 跟随系统深浅色；所有主题文本对比度 ≥4.5:1、输入边界 ≥3:1（`tests/test_g70_ui_upgrade.py` 逐主题断言）；主按钮文字按 accent 亮度自动选深/浅色。
- **读屏与焦点**：主要输入/按钮/表格有 `accessibleName`；表格与页签有可见焦点环；自绘趋势图与雷达图提供 `accessibleDescription` 文本替代。
- **空态与反馈**：下载/管理/设置搜索均有空态提示；过滤到 0 行时明确显示「没有匹配」。
- **已知限制**：Windows 屏幕阅读器（NVDA）人工走查未纳入自动化门禁；高 DPI 下图标由 `QStyle` 提供，个别平台皮肤可能略有差异。

## 发布

```bash
git tag v9.10.2
git push origin main --tags
python scripts/publish_release.py --tag v9.10.2   # 自动上传 exe 到 GitHub Release
```

## License

MIT
