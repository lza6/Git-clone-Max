"""G49-3 CLI 批量克隆模式：python -m gcm --cli [urls.txt | -]。

- 复用 gcm.app.url_lib.parse_urls + gcm.app.engine.SyncEngine（QThreadPool 并行），
  离屏 QCoreApplication，不创建任何 GUI 窗口；
- 输出到 stdout：解析结果、每仓库结果行、汇总；退出码 0=全部成功，1=有失败，2=无有效输入；
- 地址行支持 URL/短格式/本地裸仓路径（目录存在时按本地仓处理）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# G50-3 控制台编码兑底：窄编码终端（cp1252/GBK）下中文输出不再抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


def _load_urls(src: str) -> list[str]:
    """读取输入：- 为 stdin；否则读文件（UTF-8）。"""
    if src == "-":
        text = sys.stdin.read()
    else:
        text = Path(src).read_text(encoding="utf-8")
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            lines.append(line)
    return lines


def _local_spec(path: Path):
    """本地裸仓/仓库目录 → RepoSpec（owner/repo 用目录名占位，url_https=绝对路径）。"""
    from gcm.models import RepoSpec
    resolved = path.resolve()
    name = resolved.stem or resolved.name  # r0.git -> r0
    return RepoSpec(owner=resolved.parent.name or "local",
                    repo=name,
                    url_https=str(resolved),
                    folder_name=name)


def build_specs(lines: list[str]) -> tuple[list, list[str]]:
    """解析地址行；无效 URL 且本地目录存在 → 本地仓；其余归无效。"""
    from gcm.app.url_lib import parse_urls
    specs, invalid = parse_urls("\n".join(lines))
    remaining: list[str] = []
    for raw in invalid:
        try:
            p = Path(raw)
            if p.exists() and (p / ".git").is_dir() or (str(p).endswith(".git") and p.is_dir()):
                specs.append(_local_spec(p))
            else:
                remaining.append(raw)
        except Exception:  # noqa: BLE001
            remaining.append(raw)
    return specs, remaining


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：argv 为 sys.argv[1:]（可能以 --cli 开头）。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--cli":
        args = args[1:]
    # M7：--dir <目录> 指定克隆输出根目录（默认当前目录/clones）
    root = Path.cwd() / "clones"
    if "--dir" in args:
        _i = args.index("--dir")
        if _i + 1 >= len(args):
            print("[CLI] --dir 缺少目录参数")
            return 2
        root = Path(args[_i + 1])
        del args[_i:_i + 2]
    src = args[0] if args else "-"
    try:
        lines = _load_urls(src)
    except Exception as e:
        print(f"[CLI] 读取失败：{e}")
        return 2
    if not lines:
        print("[CLI] 未提供仓库地址（文件为空或 stdin 无输入）")
        return 2
    specs, invalid = build_specs(lines)
    if invalid:
        print(f"[CLI] {len(invalid)} 行无法解析：{' ; '.join(invalid[:5])}")
    if not specs:
        print("[CLI] 无有效仓库地址")
        return 2
    print(f"[CLI] 开始并行克隆 {len(specs)} 个仓库（目标根：{root}）…")

    from PyQt6.QtCore import QCoreApplication, QEventLoop, QTimer
    app = QCoreApplication.instance() or QCoreApplication([])
    from gcm.app.engine import SyncEngine
    root.mkdir(parents=True, exist_ok=True)
    engine = SyncEngine(root=root, db=None, progress_path=None)
    results: list = []

    def _on_result(_i: int, r) -> None:
        results.append(r)
        print(f"  [{'OK' if r.status.value == 'success' else r.status.value.upper()}] "
              f"{r.spec.folder_name} :: {r.message or r.detail or ''}")

    def _on_line(_i: int, text: str, _lvl: str) -> None:
        print(f"  | {text}")

    engine.result.connect(_on_result)
    engine.line.connect(_on_line)
    loop = QEventLoop()
    engine.finished.connect(loop.quit)
    n = engine.launch(specs, check_existing=False)
    if n == 0:
        print("[CLI] 无可调度任务")
        return 0
    QTimer.singleShot(600000, loop.quit)  # 10 分钟兜底，避免无限挂
    loop.exec()
    app.processEvents()

    ok = sum(1 for r in results if r.status.value == "success")
    fail = sum(1 for r in results if r.status.value == "failed")
    other = len(results) - ok - fail
    print(f"[CLI] 完成：成功 {ok} · 失败 {fail} · 其它 {other}（共 {len(results)}）")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
