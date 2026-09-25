"""G49-3 / G56-3 CLI：python -m gcm --cli [--json] [--diag] [--serve host:port] [urls.txt | -]。

- 复用 gcm.app.url_lib.parse_urls + gcm.app.engine.SyncEngine（QThreadPool 并行），
  离屏 QCoreApplication，不创建任何 GUI 窗口；
- 文本模式输出到 stdout：解析结果、每仓库结果行、汇总；退出码 0=全部成功 / 1=有失败 / 2=无有效输入；
- --json：stdout 只输出结构化 JSON（顶层 schema 见 docs/agent-contract.md），
  文本进度保留到 stderr；错误统一 {code, message, redacted}；
- --diag：输出网络/环境诊断（可配 --json）；
- --serve 127.0.0.1:port：启动只读本地 HTTP API（gcm/http_api.py），--allow-mutate 才放行写。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# G50-3 控制台编码兑底：窄编码终端（cp1252/GBK）下中文输出不再抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


# 退出码（docs/agent-contract.md §退出码）
ECODE_OK = 0
ECODE_FAIL = 1
ECODE_INVALID = 2
# MCP 显式启用失败（G56-2）：GCM_MCP_ENABLE 未开启时 python -m gcm.mcp_server 退出 3
ECODE_MCP_DISABLED = 3


def _redact(text: str) -> str:
    """打码 URL 内嵌凭据 / 授权头 / PRIVATE-TOKEN；gcm.util.redact 不存在时兜底。"""
    try:
        from gcm.util.redact import redact as _r
        return _r(text)
    except Exception:  # noqa: BLE001
        return text


def _eprint(text: str) -> None:
    """--json 模式文本进度走 stderr，stdout 只保留 JSON。"""
    print(text, file=sys.stderr)


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



def _result_dict(r) -> dict:
    """把 SyncResult 转成契约 schema 的 results 条目（含打码）。

    schema：{url, status, action, path, message, code}
    - url 用原始 spec.url_https（本地路径保留，打码后输出）
    - path 用结果落盘路径（相对时转绝对）
    - code 为统一错误码（success 时为 0）；message 为人类可读文本（含 redact）
    """
    from gcm.util.redact import redact
    status = r.status.value if hasattr(r.status, "value") else str(r.status)
    action = r.action.value if hasattr(r.action, "value") else str(r.action)
    message = r.message or r.detail or ""
    return {
        "url": redact(str(r.spec.url_https)),
        "status": status,
        "action": action,
        "path": str(Path(r.path).resolve()) if r.path else "",
        "message": redact(message),
        "code": 0 if status == "success" else 1,
    }


def _error(code: int, message: str) -> dict:
    """统一错误 schema：{code, message, redacted}。"""
    return {"code": code, "message": message, "redacted": _redact(message)}


def _run_json(app, engine, specs, results) -> dict:
    """收集并行同步结果并组装顶层 JSON 文档。"""
    ok = sum(1 for r in results if r.status.value == "success")
    fail = sum(1 for r in results if r.status.value == "failed")
    other = len(results) - ok - fail
    return {
        "version": "8.0.2",
        "ok": fail == 0 and len(results) > 0,
        "counts": {
            "success": ok,
            "failed": fail,
            "other": other,
            "total": len(results),
        },
        "results": [_result_dict(r) for r in results],
        "invalid": [],
    }


def run_clone(app, specs, invalid: list[str], root: Path, json_mode: bool,
              check_existing: bool = False) -> int:
    """共享执行体：并行克隆/同步 specs，返回退出码（0/1/2）。"""
    from PyQt6.QtCore import QEventLoop, QTimer

    from gcm.app.engine import SyncEngine
    root.mkdir(parents=True, exist_ok=True)
    engine = SyncEngine(root=root, db=None, progress_path=None)
    results: list = []
    invalid_out: list[dict] = [_error(2, f"无法解析：{line}") for line in invalid]
    if invalid_out and json_mode:
        _eprint(f"[CLI] {len(invalid_out)} 行无法解析：{_redact(' ; '.join(invalid[:5]))}")

    def _on_result(_i: int, r) -> None:
        results.append(r)
        line = (f"  [{'OK' if r.status.value == 'success' else r.status.value.upper()}] "
                f"{r.spec.folder_name} :: {r.message or r.detail or ''}")
        _eprint(line) if json_mode else print(line)

    def _on_line(_i: int, text: str, _lvl: str) -> None:
        _eprint(f"  | {text}") if json_mode else print(f"  | {text}")

    engine.result.connect(_on_result)
    engine.line.connect(_on_line)
    loop = QEventLoop()
    engine.finished.connect(loop.quit)
    n = engine.launch(specs, check_existing=check_existing)
    if n == 0:
        if json_mode:
            doc = _run_json(app, engine, specs, results)
            doc["invalid"] = invalid_out
            doc["ok"] = False
            print(json.dumps(doc, ensure_ascii=False, indent=2))
        else:
            print("[CLI] 无可调度任务")
        return 0
    QTimer.singleShot(600000, loop.quit)  # 10 分钟兜底，避免无限挂
    loop.exec()
    app.processEvents()
    ok = sum(1 for r in results if r.status.value == "success")
    fail = sum(1 for r in results if r.status.value == "failed")
    other = len(results) - ok - fail
    if json_mode:
        doc = _run_json(app, engine, specs, results)
        doc["invalid"] = invalid_out
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        print(f"[CLI] 完成：成功 {ok} · 失败 {fail} · 其它 {other}（共 {len(results)}）")
    return ECODE_FAIL if fail else ECODE_OK


def _serve_main(root: Path, bind: str, allow_mutate: bool, json_mode: bool) -> int:
    """启动只读本地 HTTP API（G56-4 可选）：--serve 127.0.0.1:port。"""
    from gcm.http_api import serve
    return serve(bind=bind, root=root, allow_mutate=allow_mutate, json_mode=json_mode)


def _diag_main(root: Path, json_mode: bool) -> int:
    """G56-1 网络/环境诊断：复用 gcm.app.diag 探测项，输出统一 schema。"""
    try:
        from gcm.app.diag import grade, run_diagnostics
        reports = run_diagnostics()
        g = grade(reports)
        if json_mode:
            doc = {
                "version": "8.0.2",
                "ok": g.startswith("绿"),
                "counts": {"success": 0, "failed": 0, "other": len(reports), "total": len(reports)},
                "results": [],
                "invalid": [],
                "diag": {"grade": g, "items": reports},
            }
            print(json.dumps(doc, ensure_ascii=False, indent=2))
        else:
            print(f"[DIAG] 总评：{g}")
            for item in reports:
                print(f"  [{item.get('status')}] {item.get('name')} :: {item.get('detail') or ''}")
        return 0 if g.startswith("绿") else 1
    except Exception as e:  # noqa: BLE001
        _eprint(f"[DIAG] 诊断失败：{e}")
        return 1


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：argv 为 sys.argv[1:]（可能以 --cli 开头）。

    旗标：
    - --dir <目录>  克隆输出根目录（默认当前目录/clones）
    - --json        结构化 JSON 输出（stdout 仅 JSON，文本进度到 stderr）
    - --diag        网络/环境诊断（不克隆）
    - --serve H:P   启动只读本地 HTTP API（--allow-mutate 放行写）
    - --allow-mutate 配合 --serve：放行 POST 写操作（默认只读）
    """
    from gcm import __version__
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--cli":
        args = args[1:]
    json_mode = "--json" in args
    if json_mode:
        args.remove("--json")
    diag = "--diag" in args
    if diag:
        args.remove("--diag")
    serve = "--serve" in args
    allow_mutate = "--allow-mutate" in args
    if allow_mutate:
        args.remove("--allow-mutate")
    # M7：--dir <目录> 指定克隆输出根目录（默认当前目录/clones）
    root = Path.cwd() / "clones"
    if "--dir" in args:
        _i = args.index("--dir")
        if _i + 1 >= len(args):
            print("[CLI] --dir 缺少目录参数")
            return 2
        root = Path(args[_i + 1])
        del args[_i:_i + 2]
    if serve:
        bind = args[0] if args else "127.0.0.1:8765"
        return _serve_main(root, bind, allow_mutate, json_mode)
    if diag:
        return _diag_main(root, json_mode)
    src = args[0] if args else "-"
    try:
        lines = _load_urls(src)
    except Exception as e:
        err = _error(2, f"读取失败：{e}")
        if json_mode:
            print(json.dumps({"version": __version__, "ok": False,
                              "counts": {"success": 0, "failed": 0, "other": 0, "total": 0},
                              "results": [], "invalid": [err]}, ensure_ascii=False, indent=2))
        else:
            print(f"[CLI] 读取失败：{e}")
        return 2
    if not lines:
        if json_mode:
            print(json.dumps({"version": __version__, "ok": False,
                              "counts": {"success": 0, "failed": 0, "other": 0, "total": 0},
                              "results": [], "invalid": []}, ensure_ascii=False, indent=2))
        else:
            print("[CLI] 未提供仓库地址（文件为空或 stdin 无输入）")
        return 2
    specs, invalid = build_specs(lines)
    if json_mode:
        _eprint(f"[CLI] 开始并行克隆 {len(specs)} 个仓库（目标根：{root}）…")
    else:
        if invalid:
            print(f"[CLI] {len(invalid)} 行无法解析：{_redact(' ; '.join(invalid[:5]))}")
        print(f"[CLI] 开始并行克隆 {len(specs)} 个仓库（目标根：{root}）…")
    if not specs:
        if json_mode:
            doc = {"version": __version__, "ok": False,
                   "counts": {"success": 0, "failed": 0, "other": 0, "total": 0},
                   "results": [], "invalid": [_error(2, "无有效仓库地址")]}
            print(json.dumps(doc, ensure_ascii=False, indent=2))
        else:
            print("[CLI] 无有效仓库地址")
        return 2

    from PyQt6.QtCore import QCoreApplication
    app = QCoreApplication.instance() or QCoreApplication([])
    return run_clone(app, specs, invalid, root, json_mode=json_mode)


if __name__ == "__main__":
    sys.exit(main())
