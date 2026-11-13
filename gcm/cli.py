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
import time as _time
from pathlib import Path

from gcm import __version__ as CONTRACT_VERSION

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

# 契约 §2.2：「有失败」= 任一仓库 failed/conflict/cancelled。
# G80-B1 修复：原实现只看 failed，conflict/cancelled 会返回 0 且 ok=true，
# 下游自动化把「冲突保留」「被取消」当成成功 —— 与文档承诺不一致。
_FAILURE_STATUSES = ("failed", "conflict", "cancelled")
# Rev16 C3：run_clone 的兜底等待窗口（原 600.0 内联值，提出来供测试压缩与契约锚定）
_CLI_TIMEOUT_SEC = 600.0


def _timeout_finalize(app, engine, results: list, json_mode: bool) -> None:
    """兜底窗口到期仍在跑：取消 + 给未完成项合成 CANCELLED 终态（Rev16 C3）。

    原行为：循环退出后直接按已收结果算退出码 —— 引擎 busy=True、部分仓库
    永远没有 result，CLI 却返回 `ok=true` / exit 0，下游自动化把「部分成功」
    当「全成功」。复现：stub 引擎睡死，1 成功 / 8 请求 → ok=true。

    现行为：`cancel_all()` 让引擎为待发队列合成 CANCELLED；在跑的 worker 会
    在下一次取消检查点退出并报 CANCELLED，轮询泵到它们落地；引擎回到非 busy
    后再算退出码 —— 有 cancelled 就是非 0，契约与行为一致。
    """
    # cancel_all 内部对每个 flag/pending 项都有自己的兜底；它若仍抛出，让异常
    # 传到 run_clone 调用方（进程退出码非 0），好过静默返回 ok=true。
    engine.cancel_all()
    deadline = _time.time() + _CLI_TIMEOUT_FINALIZE_SEC
    while engine.busy and _time.time() < deadline:
        app.processEvents()
        _time.sleep(0.02)
    if engine.busy and json_mode:
        # worker 卡死在不可取消的子进程里：如实上报，不再谎报成功
        _eprint("[CLI] engine still busy after timeout finalize "
                "(worker stuck in uncancellable subprocess)")


# 超时收尾最多再等 30s 让 worker 走完取消检查点
_CLI_TIMEOUT_FINALIZE_SEC = 30.0


def _exit_code_of(counts: dict[str, int]) -> int:
    """按 docs/agent-contract.md §2.2 计算退出码。

    counts: 状态值 → 数量（如 {"success": 3, "conflict": 1}）。
    任一 failed/conflict/cancelled > 0 → 1；否则 0。
    """
    for status in _FAILURE_STATUSES:
        try:
            if int(counts.get(status) or 0) > 0:
                return ECODE_FAIL
        except (TypeError, ValueError):
            return ECODE_FAIL  # 脏计数按失败处理（保守）
    return ECODE_OK


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
        # G81（N-5）：取数通道与降级链（加性字段，只加不减）。
        # "" = 直连源站 / 全程常规克隆。老消费者按多余键忽略即可。
        "mirror_used": redact(getattr(r, "mirror_used", "") or ""),
        "degrade_path": getattr(r, "degrade_path", "") or "",
    }


def _error(code: int, message: str) -> dict:
    """统一错误 schema：{code, message, redacted}。"""
    return {"code": code, "message": message, "redacted": _redact(message)}


def _counts_of(results) -> dict[str, int]:
    """状态值 → 数量（退出码与 JSON `ok` 字段共用同一口径）。"""
    counts: dict[str, int] = {}
    for r in results:
        key = r.status.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def _run_json(app, engine, specs, results) -> dict:
    """收集并行同步结果并组装顶层 JSON 文档。"""
    counts = _counts_of(results)
    ok = counts.get("success", 0)
    fail = counts.get("failed", 0)
    other = len(results) - ok - fail
    return {
        # G66-2：契约 version 必须与 gcm.__version__ 同源，禁止硬编码（历史上写死 8.0.2 导致
        # 所有 agent 消费者读到的版本落后 4 个大版本）。
        "version": CONTRACT_VERSION,
        # G80-B1：`ok` 与退出码必须同源，否则会出现「exit 1 但 ok=true」的自相矛盾
        "ok": len(results) > 0 and _exit_code_of(counts) == ECODE_OK,
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
              check_existing: bool = False, no_mirror: bool = False) -> int:
    """共享执行体：并行克隆/同步 specs，返回退出码（0/1/2）。

    G69-21：等待方式由 `QEventLoop.exec()` 改为**轮询 `processEvents`**。
    原因：宿主进程若先创建又销毁过 QApplication（测试/嵌入式调用很常见），
    后续 `QEventLoop.exec()` 会立即返回（实测 0.0s、结果为空），CLI 误报成功。
    轮询在当前进程里始终可用，语义等价（同样的 600s 上限）。
    """
    from gcm.app.engine import SyncEngine
    root.mkdir(parents=True, exist_ok=True)
    # v9.12.1：内置国内镜像已永久清空（实测比直连慢 8 倍），CLI 一律直连源站。
    # `--no-mirror` 保留为**兼容开关**（旧脚本仍在传），现在恒等价于默认行为。
    engine = SyncEngine(root=root, db=None, progress_path=None,
                        mirror_use_presets=False)
    results: list = []
    invalid_out: list[dict] = [_error(2, f"无法解析：{line}") for line in invalid]
    if invalid_out and json_mode:
        _eprint(f"[CLI] {len(invalid_out)} 行无法解析：{_redact(' ; '.join(invalid[:5]))}")

    def _on_result(_i: int, r) -> None:
        results.append(r)
        line = (f"  [{'OK' if r.status.value == 'success' else r.status.value.upper()}] "
                f"{r.spec.folder_name} :: {_redact(r.message or r.detail or '')}")
        _eprint(line) if json_mode else print(line)

    def _on_line(_i: int, text: str, _lvl: str) -> None:
        _eprint(f"  | {text}") if json_mode else print(f"  | {text}")

    engine.result.connect(_on_result)
    engine.line.connect(_on_line)
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
    # 10 分钟兜底，避免无限等待；用轮询而非 QEventLoop（见 docstring）。
    deadline = _time.time() + _CLI_TIMEOUT_SEC
    while engine.busy and _time.time() < deadline:
        app.processEvents()
        _time.sleep(0.02)
    app.processEvents()
    if engine.busy:
        # Rev16 C3：兜底窗口到期仍在跑 = 有仓库未收敛。取消并给未完成项合成
        # CANCELLED 终态，退出码按「有 cancelled」算 1 —— agent/MCP/HTTP 消费
        # 方不能再把「部分成功」当「全成功」（复审实测：1 成功 / 8 请求 →
        # ok=true / exit 0）。
        _timeout_finalize(app, engine, results, json_mode)
    counts = _counts_of(results)
    ok = counts.get("success", 0)
    fail = counts.get("failed", 0)
    other = len(results) - ok - fail
    if json_mode:
        doc = _run_json(app, engine, specs, results)
        doc["invalid"] = invalid_out
        print(json.dumps(doc, ensure_ascii=False, indent=2))
    else:
        print(f"[CLI] 完成：成功 {ok} · 失败 {fail} · 其它 {other}（共 {len(results)}）")
    return _exit_code_of(counts)


def _serve_main(root: Path, bind: str, allow_mutate: bool, json_mode: bool) -> int:
    """启动只读本地 HTTP API（G56-4 可选）：--serve 127.0.0.1:port。"""
    from gcm.__main__ import get_data_dir
    from gcm.http_api import serve
    # G69-18 修复：`root` 是**克隆输出目录**，业务数据库不在那里。
    # 原实现不传 data_dir → create_server 把 data_dir 兜底成 root
    # → /api/repos、/api/status 读的是克隆目录里的库（干净安装下还会
    # 静默新建一个空库），与数据目录单源红线（CLAUDE.md §5）冲突。
    return serve(bind=bind, root=root, data_dir=get_data_dir(),
                 allow_mutate=allow_mutate, json_mode=json_mode)


def _fetch_release_main(repo: str, out_dir: str | None, json_mode: bool) -> int:
    """Rev17 Batch C：下载 OWNER/REPO 最新 Release 的本机平台附件。

    退出码：0 成功；1 无匹配资产/下载失败；2 参数错误。--json 输出结构化结果。
    """
    from gcm.app.release_fetch import download_asset, latest_release_assets, pick_asset
    out = Path(out_dir) if out_dir else Path.cwd() / "release-downloads"
    try:
        tag, assets = latest_release_assets(repo, timeout=15.0)
    except Exception as e:  # noqa: BLE001 —— 网络/API 失败必须给用户可读原因
        if json_mode:
            print(json.dumps({"ok": False, "error": str(e), "repo": repo}))
        else:
            print(f"[CLI] 获取 release 失败：{e}")
        return 1
    asset = pick_asset(assets)
    if asset is None:
        if json_mode:
            print(json.dumps({"ok": False, "error": "no matching asset",
                              "repo": repo, "tag": tag,
                              "assets": [a.name for a in assets]}))
        else:
            print(f"[CLI] {repo} 的最新 release（{tag}）没有匹配本机平台的附件")
        return 1
    try:
        path = download_asset(asset, out)
    except Exception as e:  # noqa: BLE001 —— 下载失败必须给用户可读原因
        if json_mode:
            print(json.dumps({"ok": False, "error": str(e), "repo": repo,
                              "asset": asset.name}))
        else:
            print(f"[CLI] 下载失败：{e}")
        return 1
    if json_mode:
        print(json.dumps({"ok": True, "repo": repo, "tag": tag,
                          "asset": asset.name, "path": str(path),
                          "size": asset.size}))
    else:
        print(f"[CLI] 已下载 {repo} {tag} → {path}（{asset.size} 字节）")
    return 0


def _diag_main(root: Path, json_mode: bool) -> int:
    """G56-1 网络/环境诊断：复用 gcm.app.diag 探测项，输出统一 schema。"""
    try:
        from gcm.app.diag import grade, run_diagnostics
        reports = run_diagnostics()
        g = grade(reports)
        if json_mode:
            doc = {
                "version": CONTRACT_VERSION,
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


def _print_help() -> int:
    """G66-8：`--help` 输出用法到 stdout 并返回 0。

    修复前 `python -m gcm --help` 会**静默走 GUI 分支**（`__main__` 的 `flags["cli"]`
    为假）→ 拉起窗口并阻塞，agent/脚本想查用法反而挂死。
    """
    print(
        "Git-clone-Max CLI\n"
        "\n"
        "用法：\n"
        "  python -m gcm --cli [--json] [--dir <目录>] <urls.txt | ->\n"
        "  python -m gcm --cli --diag [--json]\n"
        "  python -m gcm --cli --serve <host:port> [--allow-mutate] [--json]\n"
        "\n"
        "旗标：\n"
        "  --cli           进入 CLI 模式（否则启动 GUI）\n"
        "  --dir <目录>     克隆输出根目录（默认 ./clones）\n"
        "  --json          结构化 JSON 输出（stdout 仅 JSON，文本进度走 stderr）\n"
        "  --diag          网络/环境诊断（不克隆）。必须配 --cli：\n"
        "                  单独 `-m gcm --diag` 会走 GUI 单实例路径，被「已在运行」挡住\n"
        "  --serve H:P     启动本地只读 HTTP API（仅允许回环地址）\n"
        "  --allow-mutate  配合 --serve：放行 POST 写操作（默认只读）\n"
        "  --no-mirror     兼容开关（内置国内镜像已移除，默认即直连源站）\n"
        "  --dry-run       只做本地预检：打印\"将克隆/将更新/目录被占/重复/无效\"，\n"
        "                  不联网、不写盘（配 --json 输出完整计划）\n"
        "  --no-clean      prune 只报告模式：列出远端已删仓库，不删任何东西\n"
        "  --fetch-release OWNER/REPO [--out <目录>]\n"
        "                  下载该仓库最新 Release 中匹配本机平台的附件（断点续传）\n"
        "  --prune         删除远端已删仓库的数据库记录（不删本地目录；需 --yes）\n"
        "  --prune-untouched  连本地目录一起删（不可恢复；需 --yes；黑名单仓永不删）\n"
        "  --help, -h      显示本帮助\n"
        "\n"
        "输入：<urls.txt> 为地址清单文件；`-` 表示 stdin（每行一个，`#` 开头忽略）。\n"
        "\n"
        "退出码：0=全部成功 / 1=有失败 / 2=无有效输入或参数错误 / 3=MCP 未启用。\n"
        "\n"
        "契约（--json 顶层 schema、打码规则、只读边界）见 docs/agent-contract.md。\n"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：argv 为 sys.argv[1:]（可能以 --cli 开头）。

    旗标：
    - --dir <目录>  克隆输出根目录（默认当前目录/clones）
    - --json        结构化 JSON 输出（stdout 仅 JSON，文本进度到 stderr）
    - --diag        网络/环境诊断（不克隆）
    - --serve H:P   启动只读本地 HTTP API（--allow-mutate 放行写）
    - --allow-mutate 配合 --serve：放行 POST 写操作（默认只读）
    - --no-mirror    不使用内置国内镜像（默认使用）
    - --help, -h    显示帮助
    """
    from gcm import __version__
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "--cli":
        args = args[1:]
    if "--help" in args or "-h" in args:
        return _print_help()
    json_mode = "--json" in args
    if json_mode:
        args.remove("--json")
    diag = "--diag" in args
    if diag:
        args.remove("--diag")
    # G66-8 关键修复：--serve 必须连值一起从 args 摘除，否则它自己会被当成 bind
    # （原实现只置标志不移除 → args[0] 恒为 "--serve" → 100% 启动失败，
    #  而 docs 与 docstring 都写着 `--cli --serve 127.0.0.1:8765`）。
    serve_bind: str | None = None
    if "--serve" in args:
        _si = args.index("--serve")
        if _si + 1 >= len(args):
            print("[CLI] --serve 缺少 host:port 参数（例：--serve 127.0.0.1:8765）")
            return 2
        serve_bind = args[_si + 1]
        del args[_si:_si + 2]
    allow_mutate = "--allow-mutate" in args
    if allow_mutate:
        args.remove("--allow-mutate")
    no_mirror = "--no-mirror" in args
    if no_mirror:
        args.remove("--no-mirror")
    # G84-12：--dry-run 只算「这一批会发生什么」，不碰网络也不写盘。
    dry_run = "--dry-run" in args
    if dry_run:
        args.remove("--dry-run")
    # Rev17 Batch C：--fetch-release OWNER/REPO 下载最新 release 附件（HTTP 域，
    # 与 git 克隆分离）。--out <目录> 可选（默认 ./release-downloads）。
    fetch_repo: str | None = None
    if "--fetch-release" in args:
        _fi = args.index("--fetch-release")
        if _fi + 1 >= len(args):
            print("[CLI] --fetch-release 缺少 OWNER/REPO 参数")
            return 2
        fetch_repo = args[_fi + 1]
        del args[_fi:_fi + 2]
    fetch_out: str | None = None
    if "--out" in args:
        _oi = args.index("--out")
        if _oi + 1 >= len(args):
            print("[CLI] --out 缺少目录参数")
            return 2
        fetch_out = args[_oi + 1]
        del args[_oi:_oi + 2]
    # R-01 prune 三态（互斥；默认 no-clean 只报告不删）
    prune_mode = None
    for flag, m in (("--prune", "prune"),
                    ("--prune-untouched", "prune-untouched"),
                    ("--no-clean", "no-clean")):
        if flag in args:
            args.remove(flag)
            prune_mode = m
            break
    # M7：--dir <目录> 指定克隆输出根目录（默认当前目录/clones）
    root = Path.cwd() / "clones"
    if "--dir" in args:
        _i = args.index("--dir")
        if _i + 1 >= len(args):
            print("[CLI] --dir 缺少目录参数")
            return 2
        root = Path(args[_i + 1])
        del args[_i:_i + 2]
    if serve_bind is not None:
        # bind 已由 --serve 显式消费；无值时退回默认回环地址
        return _serve_main(root, serve_bind or "127.0.0.1:8765",
                           allow_mutate, json_mode)
    if diag:
        return _diag_main(root, json_mode)
    if fetch_repo is not None:
        return _fetch_release_main(fetch_repo, fetch_out, json_mode)
    if prune_mode is not None:
        # R-01：prune 扫描全库并按模式清理；不消耗 urls 输入，执行完即返回。
        # 审查 P1：--dry-run 与 prune 同用时，删除模式必须强制降为只报告 ——
        # 用户按既有习惯 `--prune --dry-run` 期望预览，绝不能直接删。
        if dry_run and prune_mode != "no-clean":
            prune_mode = "no-clean"
        # 审查 P2：删除真实数据的模式必须显式 --yes（json 模式是 agent 契约，
        # 由 agent 侧负责告知用户，故豁免）。
        yes = "--yes" in args
        if yes:
            args.remove("--yes")
        if prune_mode in ("prune", "prune-untouched") and not (yes or json_mode):
            print("[CLI] --prune / --prune-untouched 会删除数据库记录"
                  + ("与本地目录" if prune_mode == "prune-untouched" else "")
                  + "（不可撤销）。")
            print("[CLI] 先用 --no-clean 或 --dry-run 预览将删清单，"
                  "确认后加 --yes 执行。")
            return 2
        from gcm.__main__ import get_data_dir
        from gcm.app.prune import collect_prune_items, execute_prune
        from gcm.db.repo_db import Database
        from gcm.util.oplog import default_oplog
        data_dir = get_data_dir()
        db = Database(data_dir / "repos.db")
        try:
            items = collect_prune_items(db)
            counts = execute_prune(db, items, prune_mode,
                                   oplog=default_oplog(data_dir))
        finally:
            db.close()
        if json_mode:
            print(json.dumps({"version": __version__, "ok": True,
                              "mode": f"prune:{prune_mode}",
                              "pruned": counts["pruned"],
                              "dir_removed": counts["dir_removed"],
                              "skipped_protected": counts["skipped_protected"],
                              "reported": counts["reported"],
                              "items": [{"folder": it.folder_name, "url": it.url,
                                         "remote_gone": it.remote_gone,
                                         "protected": it.protected,
                                         "db_removed": it.db_removed,
                                         "dir_removed": it.dir_removed,
                                         "skipped_reason": it.skipped_reason}
                                        for it in items]},
                             ensure_ascii=False, indent=2))
        else:
            print(f"[CLI] prune（{prune_mode}）：清库 {counts['pruned']} · "
                  f"删目录 {counts['dir_removed']} · 保护跳过 "
                  f"{counts['skipped_protected']} · 仅报告 {counts['reported']}")
        return 0
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
    if dry_run:
        # G84-12：预览分支必须在**任何写盘/联网之前**返回（agent 用它做"先看看"）。
        from gcm.app.batch_plan import plan_batch
        plan = plan_batch("\n".join(lines), root)
        if json_mode:
            # `mode: "dry_run"` 是给消费者的判别位：这份 JSON **不是**克隆结果，
            # `ok` 只表示"预览算出来了"，不代表任何仓库成功（契约漂移防线）。
            # Rev17 T7：preview_id 是进程内 uuid（不落盘）——agent 可以拿它做
            # 「同一批输入两次预览是否一致」的比对锚；GUI 预览对话框同源
            # plan_batch，counts 一致即可确认"看到的就是会执行的"。
            import uuid as _uuid
            doc = {"version": __version__, "ok": True, "mode": "dry_run",
                   "preview_id": _uuid.uuid4().hex,
                   "plan": plan.to_dict()}
            print(json.dumps(doc, ensure_ascii=False, indent=2))
        else:
            for line in plan.summary_lines():
                print(f"[CLI] {line}")
            print("[CLI] 这是预览（--dry-run）：未联网、未写盘。")
        return 0
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
    return run_clone(app, specs, invalid, root, json_mode=json_mode,
                     no_mirror=no_mirror)


if __name__ == "__main__":
    sys.exit(main())
