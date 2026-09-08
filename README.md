# Git-clone-Max

git仓库的软件工具，支持批量git你想要的目标仓库和自动更新你git下来的仓库

---

> GitHub 仓库批量并行下载 / 增量更新 / 入库追踪 的桌面工具（PyQt6）。
> 克隆目录一律 `作者__仓库`，本地改动永不覆盖。

## 快速开始（Windows）

双击 **`scripts\启动Git-clone-Max.bat`**（或右键 PowerShell 运行同目录 `.ps1`）。
启动器会自动：

1. 检测 `git` 与 `python`（py 启动器优先）
2. 在项目根创建虚拟环境 `.venv`（若不存在）
3. 自检 `PyQt6`，缺失则自动 `pip install -r requirements.txt`
4. 以 `python -m gcm` 启动 GUI

## 功能

| 功能 | 说明 |
|------|------|
| 批量并行 | 线程池 ≤8 并发，仓库互不阻塞 |
| 断点续传 | `progress.json` 原子写入；中断后已完成的仓库下一轮自动跳过、其余继续 |
| 增量更新 | 已存在 → `git fetch --prune` → `merge --ff-only`；无法快进退化为 rebase |
| 冲突保护 | 本地有改动且与远端分叉 → **标记冲突、保留本地、绝不覆盖** |
| 数据库 | SQLite `repos.db` 记录仓库元数据 + `sync_history` 全量同步快照 |
| 一键更新 | 「仓库管理 → ⟳ 一键更新全部」对库中所有仓库增量同步 |
| 作者__仓库命名 | 同名仓库不冲突（如 `lza6__Git-clone-Max`） |
| 黑匣子日志 | 实时 git 原生输出 + 关键字高亮 + 导出 txt |
| 后台常驻 | 见下方「后台挂机」 |

## 目录结构

```
Git-clone-Max/
├── gcm/                    主包
│   ├── __main__.py         入口（python -m gcm）
│   ├── models.py           数据模型
│   ├── app/
│   │   ├── url_lib.py      地址解析 / 命名
│   │   └── worker.py       并行 worker + 断点续传
│   ├── db/repo_db.py       SQLite
│   ├── git/service.py      git 操作（clone/fetch/merge/rebase/冲突检测）
│   └── ui/                 主题 + 三 Tab 主窗口
├── scripts/                bat / ps1 启动器
├── tests/                  单元测试（含 git 真实 E2E）
├── requirements.txt
└── pyproject.toml
```

## 测试

```bash
python -m unittest discover -s tests -v
```

（`tests/test_core.py` 内置本地裸仓库全程 E2E：克隆 → 增量子提交 → 已最新 → 冲突保留 → 残留目录重建。）

## 后台挂机 / 长期运行

- **内存**：日志环形缓冲上限 3000 条自动裁剪；worker 任务完成后自动释放；不用全局定时器轮询。
- **退出保护**：任务运行中关闭会二次确认，确认后取消剩余任务并安全关闭数据库。
- **开机自启**（可选）：设置页勾选「开机自启」会创建任务计划登录时启动。

## 发布

```bash
git tag v1.0.0
git push origin main --tags
```

## License

MIT
