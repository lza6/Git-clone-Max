# Git-clone-Max

> GitHub 仓库批量并行下载 / 增量更新 / 入库追踪 的桌面工具（PyQt6）。
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

> 注：`QT_QPA_PLATFORM=offscreen` 仅用于**无显示器环境 / CI**（如 `QT_QPA_PLATFORM=offscreen python -m gcm`），正常桌面环境无需设置。

## 功能

| 功能 | 说明 |
|------|------|
| 多平台 | GitHub / GitLab / Gitee / Codeberg / Bitbucket / 自建主机，HTTPS + SSH + 短格式 + 子组 + 自定义主机白名单，直接复制地址即下载 |
| 指定版本 | `owner/repo@v1.2.0` 语法指定分支/标签（`clone -b`），不改动仓库默认检出 |
| 按 host 凭据 | 设置页「按平台凭据」`host=token` 逐行注入（github→Bearer / gitlab→PRIVATE-TOKEN / 未登记不发）；`host_tokens` **逐条目加密落盘**，杜绝明文 |
| 镜像前缀 | 设置页「镜像前缀」`host=https://ghproxy.com` 逐行配置，HTTPS 克隆自动拼接镜像（SSH 不拼） |
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
| 单任务控制 | 进度表搜索过滤 / 表头排序 / 单仓库暂停 / 右键重试、复制详情、打开目录 |
| 任务清单 | 输入区右键保存/加载任务清单（`data/lists`）；批量打标签 + 导出所选 CSV |
| 子模块 | 设置开关拉取 `--recurse-submodules`，工作区完整 |
| 代理/限速 | 手动填写或一键自动检测（环境变量 / Windows 注册表）；下载限速（lowSpeedLimit） |
| 仓库管理 | 标签 / 收藏置顶 / 黑名单（一键更新排除）/ 过期高亮 / 搜索过滤 / host 分布统计 |
| 数据洞察 | 统计中心（全局 + host + 30 天趋势 + 用量）、报表筛选导出（CSV / Markdown）、每仓库备注、远端检查 |
| 任务入口 | GitHub Star 列表导入、本地仓库扫描导入、剪贴板监听、历史记录回填 |
| 多主题 | Deep / Light / Nord 三套主题即时切换 + 跟随系统深浅色（auto），重启恢复 |
| 首启向导 | 三步设置下载目录/并发/代理，零门槛上手 |
| 快捷键/动效 | Ctrl+Alt+S/U/M 全局热键；完成行动效淡出（可关）；窗口尺寸记忆 |
| 可靠性 | token 加密落盘、单实例锁、全局异常兜底 `error.log`、settings 备份恢复、数据库自动迁移备份 |
| 数据库 | SQLite `repos.db`（WAL）记录元数据 + `sync_history` 全量同步快照健康治理 |
| 一键更新 | 「仓库管理 → ⟳ 一键更新全部」对库中所有仓库增量同步（黑名单排除） |
| 黑匣子日志 | 实时 git 原生输出 + 关键字高亮 + 导出 txt（结构化 app.log + error.log 兜底 + redact） |

## 目录结构

```
Git-clone-Max/
├── dist/Git-clone-Max.exe   成品可执行文件（双击即用）
├── gcm/                    主包
│   ├── __main__.py         入口（python -m gcm）
│   ├── models.py           数据模型
│   ├── app/
│   │   ├── engine.py       统一调度引擎（并发/去重/进度/取消）
│   │   ├── url_lib.py      通用 Git URL 解析（多平台）/ 跨 host 命名
│   │   └── worker.py       并行 worker（git 同步 + 结果回传）
│   ├── db/repo_db.py       SQLite（busy_timeout）/ progress 文件锁
│   ├── git/service.py      git 操作（clone/fetch/merge/rebase/冲突/平台限制识别）
│   └── ui/                 主题 + 三 Tab 主窗口
├── scripts/                bat / ps1 启动器
├── tests/                  单元测试（含真实 git E2E / 并发引擎测试）
├── requirements.txt
└── pyproject.toml
```

## 重新打包 exe

```bash
python -m pip install pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed --name "Git-clone-Max" \
  --add-data "gcm;gcm" --collect-all "PyQt6" gcm/__main__.py
```

> **v5.0 起支持多平台直克隆**：GitLab / Gitee / Codeberg / Bitbucket / 自建主机，HTTPS + SSH + 短格式 + 子组均可；命名 `host__作者__仓库`（GitHub 保持 `作者__仓库`）。

## 测试

```bash
python -m unittest discover -s tests -v
```

（`tests/test_core.py` 内置本地裸仓库全程 E2E：克隆 → 增量子提交 → 已最新 → 冲突保留 → 残留目录重建。）

## 覆盖率与回归（G45-1）

本地复现 CI 门禁（全量约 16-20 分钟；Windows 需把 git 加入 PATH，自定义安装如 scoop 请先发行 `$env:PATH="<git 安装目录>\cmd;$env:PATH"`）：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m coverage run --source=gcm -m unittest discover -s tests
python -m coverage report --fail-under=85
```

- 覆盖率门禁阈值与 CI 一致（`ci.yml` 的 `COVERAGE_FAIL_UNDER`），三平台矩阵跑同一门禁。
- v7.6.0（G45-6）起 `gcm/ui/main_window.py` 已拆分（1791→1214 行）：
  `manage_panel.py`（管理页 UI/动作+信号回传）、`progress_table.py`（进度表/过滤/结果回填/引擎收尾）、
  `settings_panel.py`（设置回调）；控件名/方法名/信号全兼容，行为零变化。

## 后台挂机 / 长期运行

- **内存**：日志环形缓冲上限 3000 条自动裁剪；worker 任务完成后自动释放；不用全局定时器轮询。
- **并发安全**：进度文件进程级锁 + SQLite `busy_timeout=30000`；并发 32 实测无闪退 / 锁死。
- **退出保护**：任务运行中关闭会二次确认，确认后取消剩余任务并安全关闭数据库。
- **开机自启**（可选）：设置页勾选「开机自启」会创建任务计划登录时启动。

## 发布

```bash
git tag v4.0.0
git push origin main --tags
python scripts/publish_release.py --tag v4.0.0   # 自动上传 exe 到 GitHub Release
```

## License

MIT