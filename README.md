# Git-clone-Max

> GitHub 仓库批量并行下载 / 增量更新 / 入库追踪 的桌面工具（PyQt6）。
> 克隆目录一律 `作者__仓库`，本地改动永不覆盖。

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

## 功能

| 功能 | 说明 |
|------|------|
| 批量并行 | 线程池 ≤32 并发（默认 8，设置页可调 1–32），仓库互不阻塞，重复地址自动去重 |
| 断点续传 | `progress.json` 进程级锁 + 引擎周期落盘（原子写）；中断后已完成的仓库下一轮自动跳过、其余继续 |
| 并发安全 | 引擎统一调度：SQLite `busy_timeout` 30s + 进度文件互斥锁，高并发下不再闪退 / 锁死 |
| 增量更新 | 已存在 → `git fetch --prune` → `merge --ff-only`；无法快进退化为 rebase |
| 冲突保护 | 本地有改动且与远端分叉 → **标记冲突、保留本地、绝不覆盖** |
| 失败恢复 | 单个仓库失败不中断整体；Windows 非法文件名/目录占用等平台限制错误给出明确提示且不无效重试 |
| 数据库 | SQLite `repos.db` 记录仓库元数据 + `sync_history` 全量同步快照 |
| 一键更新 | 「仓库管理 → ⟳ 一键更新全部」对库中所有仓库增量同步 |
| 作者__仓库命名 | 同名仓库不冲突（如 `lza6__Git-clone-Max`） |
| 黑匣子日志 | 实时 git 原生输出 + 关键字高亮 + 导出 txt |
| 下载完自动清空 | 所有仓库同步完成后自动清空输入框 |

## 目录结构

```
Git-clone-Max/
├── dist/Git-clone-Max.exe   成品可执行文件（双击即用）
├── gcm/                    主包
│   ├── __main__.py         入口（python -m gcm）
│   ├── models.py           数据模型
│   ├── app/
│   │   ├── engine.py       统一调度引擎（并发/去重/进度/取消）
│   │   ├── url_lib.py      地址解析 / 命名
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

## 测试

```bash
python -m unittest discover -s tests -v
```

（`tests/test_core.py` 内置本地裸仓库全程 E2E：克隆 → 增量子提交 → 已最新 → 冲突保留 → 残留目录重建。）

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