"""G55 工程与测试体系：内置小型变异测试冒烟脚本（无 mutmut 依赖）。

对三个纯函数做变异抽样（每个变异体仅含一处变异）：
- gcm/app/url_lib.py::parse_any_repo_url
- gcm/git/service.py::_is_networkish_error
- gcm/reports.py::_fetch_rows

变异算子（token 级、单点、作用于函数源码区间，绝不命中字符串/注释）：
- 比较运算：== <-> !=、< <-> >、<= <-> >=
- 布尔运算：and <-> or
- 常量翻转：True <-> False
- 成员运算：in <-> not in
- 删除参数默认值：`= None` 移除（仅签名含默认值的函数）

击杀判定：变异函数与原函数在同一组输入上输出或异常不同即击杀。
击杀率 = 击杀数 / 变异体数；等价变异体（行为恒同）计入未击杀清单。
纯函数；不触发网络、不启动 Qt。

用法：
    python scripts/run_mutation_smoke.py
退出码：总击杀率 >= 0.8 返回 0，否则 1。
"""
from __future__ import annotations

import ast
import importlib
import io
import re
import sys
import tokenize
from collections.abc import Callable
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# token 级单点变异
# ---------------------------------------------------------------------------

_OP_MUT: dict[str, str] = {
    "==": "!=",
    "!=": "==",
    "<=": ">=",
    ">=": "<=",
    "<": ">",
    ">": "<",
    "and": "or",
    "or": "and",
    "in": "not in",
}
_CONST_MUT: dict[str, str] = {"True": "False", "False": "True"}


def _mutate_single_tokens(body: str) -> list[str]:
    """token 级单点变异：仅替换源码 token 文本，不触碰字符串/注释。

    - 跳过 for 循环的 `in`（`for x in y` 变异成 `not in` 会产生语法错误，
      且属于循环结构而非成员判断，不在目标算子内）；
    - 跳过 def 签名行（参数注解/默认值属于签名，默认值单独用 _drop_default）。
    """
    mutants: list[str] = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(body).readline))
    except Exception:
        return mutants
    for i, tok in enumerate(toks):
        if tok.type not in (tokenize.OP, tokenize.NAME):
            continue
        text = tok.string
        # 跳过 def 签名行（第一个非缩进 token 之后、冒号+函数体之前）
        if tok.start[0] == 1:
            continue
        if text == "in" and _is_for_in(toks, i):
            continue
        if text in _OP_MUT:
            mutants.append(_replace_span(body, tok.start, tok.end, _OP_MUT[text]))
        elif text in _CONST_MUT:
            mutants.append(_replace_span(body, tok.start, tok.end, _CONST_MUT[text]))
    return mutants


def _is_for_in(toks, idx: int) -> bool:
    """判断 idx 位置的 `in` 是否属于 for 循环（for ... in ...）。"""
    # 向前找同一语句内的关键字 token：for（第 1 行内）
    for j in range(idx - 1, max(-1, idx - 8), -1):
        if toks[j].type == tokenize.NAME and toks[j].string == "for":
            # for 与 in 之间不得出现冒号（函数体/复合语句边界）
            return all(toks[k].string != ":" for k in range(j + 1, idx))
        if toks[j].string == ":":
            break
    return False


def _replace_span(body: str, start, end, new: str) -> str:
    sline, scol = start
    eline, ecol = end
    lines = body.splitlines(keepends=True)
    if sline == eline:
        line = lines[sline - 1]
        return "".join(lines[:sline - 1]) + line[:scol] + new + line[ecol:] + "".join(lines[sline:])
    raise ValueError("跨行 token 不支持")


def _mutate_not_in(body: str) -> list[str]:
    """把 `not in` 整体变异为 `in`（token 流辅助定位，not 与 in 为相邻 token）。"""
    out: list[str] = []
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(body).readline))
    except Exception:
        return out
    for i, tok in enumerate(toks[:-1]):
        if (tok.type == tokenize.NAME and tok.string == "not"
                and toks[i + 1].type == tokenize.NAME and toks[i + 1].string == "in"):
            out.append(_replace_span(body, tok.start, toks[i + 1].end, "in"))
    return out


def _drop_default(body: str) -> list[str]:
    """删除参数默认值（仅 def 行内 `= None`）。"""
    out: list[str] = []
    lines = body.splitlines()
    for i, ln in enumerate(lines):
        if not ln.strip().startswith("def "):
            continue
        new = re.sub(r"=\s*None(?=\s*[,)])", "", ln)
        if new != ln:
            lines[i] = new
            out.append("\n".join(lines))
    return out
# ---------------------------------------------------------------------------
# 源码/模块加载
# ---------------------------------------------------------------------------

def _func_source(mod_path: Path, fn_name: str) -> str:
    src = mod_path.read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == fn_name:
            seg = ast.get_source_segment(src, node)
            if seg is None:
                raise RuntimeError(f"get_source_segment 失败: {fn_name}")
            return seg
    raise RuntimeError(f"未找到函数 {fn_name}")


def _load_fn(mod_globals: dict, fn_name: str, source: str) -> Callable:
    ns = dict(mod_globals)
    exec(compile(source, "<mutant>", "exec"), ns)
    return ns[fn_name]


def _module_globals(mod_path: Path) -> dict:
    rel = mod_path.relative_to(ROOT).with_suffix("")
    dotted = ".".join(rel.parts)
    mod = importlib.import_module(dotted)
    return mod.__dict__


# ---------------------------------------------------------------------------
# 用例与 oracle
# ---------------------------------------------------------------------------

def _cases_parse_any() -> list[tuple[Any, Any]]:
    return [
        ("https://github.com/owner/repo", ("https://github.com/owner/repo.git", "owner__repo", "")),
        ("git@github.com:owner/repo.git", ("https://github.com/owner/repo.git", "owner__repo", "")),
        ("https://gitlab.com/group/sub/repo",
         ("https://gitlab.com/group/sub/repo.git", "gitlab.com__sub__repo", "")),
        ("owner/repo", ("https://github.com/owner/repo.git", "owner__repo", "")),
        ("https://github.com/o/r@v1.2.0", ("https://github.com/o/r.git", "o__r@v1.2.0", "v1.2.0")),
        ("https://evil.com/a/b", None),
        ("", None),
    ]


def _cases_networkish() -> list[tuple[str, bool]]:
    return [
        ("fatal: unable to access 'https://x/': Failed to connect", True),
        ("remote: Connection reset by peer", True),
        ("fatal: repository not found", False),
        ("fatal: Authentication failed", False),
        ("error: invalid path 'a:b'", False),
        ("fatal: destination path 'x' already exists", False),
        ("Random message", False),
    ]


class _DbWrap:
    """sqlite3 连接包装：reports._fetch_rows 访问 db._conn。"""

    def __init__(self, conn):
        self._conn = conn


def _make_sqlite_db(rows):
    """构造真实 sqlite3 内存库（repos + sync_history 表），行结构与 reports 期望一致。"""
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE repos (id INTEGER PRIMARY KEY, owner TEXT, repo TEXT, host TEXT)")
    conn.execute("""CREATE TABLE sync_history (
        id INTEGER PRIMARY KEY, repo_id INTEGER,
        status TEXT, action TEXT, message TEXT, commits INTEGER,
        duration_ms TEXT, started_at TEXT)""")
    cur = conn.cursor()
    for i, r in enumerate(rows, 1):
        cur.execute("INSERT INTO repos (id, owner, repo, host) VALUES (?,?,?,?)",
                    (i, r["owner"], r["repo"], r["host"]))
        if r.get("started_at") is not None:
            cur.execute("""INSERT INTO sync_history
                (id, repo_id, status, action, message, commits, duration_ms, started_at)
                VALUES (?,?,?,?,?,?,?,?)""",
                (i, i, "success", "clone", "", 1, 100, r["started_at"]))
    conn.row_factory = sqlite3.Row
    conn.commit()
    import atexit
    atexit.register(conn.close)
    return _DbWrap(conn)
def _base_rows():
    return [
        {"owner": "a", "repo": "r1", "host": "github.com", "started_at": "2026-01-02 10:00:00"},
        {"owner": "b", "repo": "r2", "host": "gitlab.com", "started_at": "2026-01-03 10:00:00"},
        {"owner": "c", "repo": "r3", "host": "github.com", "started_at": None},
    ]


def _dup_rows():
    return [
        {"owner": "d", "repo": "r4", "host": "github.com", "started_at": None},
        {"owner": "d", "repo": "r4", "host": "github.com", "started_at": None},
        {"owner": "e", "repo": "r5", "host": "gitlab.com", "started_at": None},
    ]

def _cases_fetch_rows() -> list[tuple[Any, Any]]:
    """_fetch_rows：sqlite 连接 -> 行数。filters 含 host/status/日期范围。"""
    cases = [
        ((_make_sqlite_db(_base_rows()), None), 3),
        ((_make_sqlite_db(_base_rows()), {"start": "2026-01-01", "end": "2026-12-31"}), 3),
        ((_make_sqlite_db(_base_rows()), {"start": "2026-12-01"}), 0),
        ((_make_sqlite_db(_base_rows()), {"start": "2026-12-01", "end": "2026-01-01"}), 0),
        ((_make_sqlite_db(_base_rows()), {"start": "2026-01-01"}), 3),
        ((_make_sqlite_db(_dup_rows()), None), 2),
        ((_make_sqlite_db(_dup_rows()), {"status": "success"}), 2),
        ((_make_sqlite_db(_base_rows()),), 3),  # 依赖默认参数 filters=None
        ((_make_sqlite_db(_base_rows()), {"host": "github.com"}), 2),
        ((_make_sqlite_db(_base_rows()), {"host": "gitlab.com"}), 1),
        ((_make_sqlite_db(_base_rows()), {"end": "2026-12-31"}), 3),
        ((_make_sqlite_db(_base_rows()), {"status": "failed"}), 0),
    ]
    return cases


def _oracle_for(fn_name: str, fn: Callable):
    def oracle(args: Any):
        if fn_name == "parse_any_repo_url":
            spec = fn(args)
            if spec is None:
                return None
            return (spec.url_https, spec.folder_name, spec.ref)
        if fn_name == "_fetch_rows":
            rows = fn(*args)
            return len(rows)
        return fn(args)
    return oracle


def _safe_call(oracle, args):
    try:
        return oracle(args)
    except Exception as e:  # noqa: BLE001
        return f"EXC:{type(e).__name__}"
# ---------------------------------------------------------------------------
# 击杀统计
# ---------------------------------------------------------------------------

def _run_target(fn_name: str, mod_path: Path,
                cases: list[tuple[Any, Any]]) -> tuple[int, int, list[str]]:
    globs = _module_globals(mod_path)
    orig_src = _func_source(mod_path, fn_name)
    orig = _load_fn(globs, fn_name, orig_src)
    expected = [_safe_call(_oracle_for(fn_name, orig), args) for args, _ in cases]

    bodies = _mutate_single_tokens(orig_src)
    bodies += _mutate_not_in(orig_src)
    if fn_name == "_fetch_rows":
        bodies += _drop_default(orig_src)

    seen: set[str] = set()
    uniq: list[str] = []
    for b in bodies:
        if b not in seen:
            seen.add(b)
            uniq.append(b)

    killed = 0
    survivors: list[str] = []
    for idx, body in enumerate(uniq, 1):
        try:
            fn = _load_fn(globs, fn_name, body)
        except Exception as e:  # noqa: BLE001
            survivors.append(f"#{idx}(compile:{type(e).__name__})")
            continue
        oracle = _oracle_for(fn_name, fn)
        diff = False
        for args, _exp in cases:
            if _safe_call(oracle, args) != expected[_index(args, cases)]:
                diff = True
                break
        if diff:
            killed += 1
        else:
            survivors.append(f"#{idx}")
    return killed, len(uniq), survivors


def _index(args, cases):
    for i, (a, _) in enumerate(cases):
        if repr(a) == repr(args):
            return i
    raise KeyError


def main() -> int:
    targets = [
        ("parse_any_repo_url", ROOT / "gcm" / "app" / "url_lib.py", _cases_parse_any()),
        ("_is_networkish_error", ROOT / "gcm" / "git" / "service.py", _cases_networkish()),
        ("_fetch_rows", ROOT / "gcm" / "reports.py", _cases_fetch_rows()),
    ]
    print("=" * 74)
    print("G55 mutation smoke: target pure functions, kill-rate >= 80%")
    print("=" * 74)
    total_k = total_m = 0
    for fn_name, mod, cases in targets:
        killed, total, survivors = _run_target(fn_name, mod, cases)
        rate = killed / total if total else 1.0
        total_k += killed
        total_m += total
        print(f"{fn_name}: mutants={total} killed={killed} kill_rate={rate:.2%}")
        if survivors:
            print("  survivors: " + ", ".join(survivors))
    overall = total_k / total_m if total_m else 1.0
    print("-" * 74)
    print(f"OVERALL: mutants={total_m} killed={total_k} kill_rate={overall:.2%}")
    ok = overall >= 0.8
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
