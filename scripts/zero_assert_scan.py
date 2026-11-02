"""AST scanner: find test methods with no assertions.

Usage:
    python -I zero_assert_scan.py <tests_dir> [--json out.json]

A test method is "zero-assertion" when its body contains:
  - no call to any self.assertX / self.fail / self.skipTest / pytest.raise/raises
  - no bare `assert` statement
  - no `with self.assertRaises(...)` / `pytest.raises(...)` context manager
Assertion helpers that live in a called helper method are NOT resolved
(cross-method call graph), so results are reported with the raw call list
for manual confirmation.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

ASSERT_PREFIXES = ("assert",)
ASSERT_EXACT = ("fail", "skipTest", "subTest")
PYTEST_ASSERT_ATTRS = ("raises", "warns", "fail", "skip")


def _attr_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_assertion_call(node: ast.Call) -> bool:
    func = node.func
    # self.assertX(...)
    if isinstance(func, ast.Attribute):
        name = func.attr
        if name.startswith(ASSERT_PREFIXES) or name in ASSERT_EXACT:
            return True
        # pytest.raises(...) / pytest.warns(...)
        if name in PYTEST_ASSERT_ATTRS:
            return True
        # mock ANY(...) style is not an assertion
        # unittest.mock.ANY is not a call we care about
        return False
    if isinstance(func, ast.Name) and func.id in ("fail", "raises", "warns"):
        return True
    return False


def _is_assert_ctx_manager(node: ast.With) -> bool:
    for item in node.items:
        ctx = item.context_expr
        if isinstance(ctx, ast.Call) and _is_assertion_call(ctx):
            return True
        # with self.assertRaises(...) as cm  -> same shape, covered above
    return False


def _walk_body(nodes: list[ast.stmt]) -> list[ast.AST]:
    """Yield all descendant nodes of the given statements."""
    out: list[ast.AST] = []
    for stmt in nodes:
        for child in ast.walk(stmt):
            out.append(child)
    return out


def analyze_function(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> dict:
    nodes = _walk_body(fn.body)
    asserts: list[str] = []
    calls: list[str] = []

    for n in nodes:
        if isinstance(n, ast.Assert):
            asserts.append(f"assert <expr> @L{n.lineno}")
        elif isinstance(n, ast.With) and _is_assert_ctx_manager(n):
            item = n.items[0].context_expr
            nm = _attr_name(item.func) if isinstance(item, ast.Call) else None
            asserts.append(f"with {nm}(...) @L{n.lineno}")
        elif isinstance(n, ast.Call):
            if _is_assertion_call(n):
                asserts.append(f"{_attr_name(n.func)}(...) @L{n.lineno}")
            else:
                nm = _attr_name(n.func)
                if nm:
                    calls.append(nm)

    # real (non-comment) statement count, excluding docstring
    body = list(fn.body)
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        body = body[1:]

    return {
        "name": fn.name,
        "lineno": fn.lineno,
        "end_lineno": getattr(fn, "end_lineno", None),
        "n_asserts": len(asserts),
        "asserts": asserts[:8],
        "real_stmts": len(body),
        "calls": sorted(set(calls))[:14],
        "n_calls": len(set(calls)),
    }


def collect_test_classes(tree: ast.Module) -> list[ast.ClassDef]:
    out = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out.append(node)
    return out


def analyze_file(path: Path) -> dict:
    src = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError as exc:  # pragma: no cover
        return {"file": path.name, "error": f"SyntaxError: {exc}", "tests": []}

    tests: list[dict] = []
    for cls in collect_test_classes(tree):
        for node in cls.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
                "test"
            ):
                info = analyze_function(node)
                info["class"] = cls.name
                tests.append(info)

    # module-level test functions too
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(
            "test"
        ):
            info = analyze_function(node)
            info["class"] = "<module>"
            tests.append(info)

    total_asserts = sum(t["n_asserts"] for t in tests)
    return {
        "file": path.name,
        "path": str(path),
        "n_tests": len(tests),
        "n_asserts": total_asserts,
        "tests": tests,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tests_dir")
    ap.add_argument("--json")
    ap.add_argument(
        "--exclude",
        nargs="*",
        default=["test_g66_patch.py"],
        help="filenames to skip",
    )
    args = ap.parse_args()

    root = Path(args.tests_dir)
    files = sorted(p for p in root.glob("test_*.py") if p.name not in set(args.exclude))

    results = [analyze_file(p) for p in files]

    zero: list[dict] = []
    for r in results:
        for t in r["tests"]:
            if t["n_asserts"] == 0:
                zero.append({"file": r["file"], **t})

    print("=" * 100)
    print("ZERO-ASSERTION TESTS")
    print("=" * 100)
    print(f"{'file':46} {'line':>5} {'test':52} stmts calls")
    for z in zero:
        print(
            f"{z['file']:46} {z['lineno']:>5} {(z['class'] + '.' + z['name'])[:52]:52} "
            f"{z['real_stmts']:>5} {z['n_calls']:>5}"
        )
    print(f"\nTOTAL ZERO-ASSERTION TESTS: {len(zero)}")

    print("\n" + "=" * 100)
    print("LOW ASSERT RATIO FILES (asserts/tests < 1.5) and files with asserts==0")
    print("=" * 100)
    print(f"{'file':46} {'tests':>6} {'asserts':>8} {'ratio':>7}")
    for r in sorted(results, key=lambda x: (x["n_asserts"] / max(x["n_tests"], 1))):
        if r["n_tests"] == 0:
            continue
        ratio = r["n_asserts"] / r["n_tests"]
        if ratio < 1.5:
            print(f"{r['file']:46} {r['n_tests']:>6} {r['n_asserts']:>8} {ratio:>7.2f}")

    tot_tests = sum(r["n_tests"] for r in results)
    tot_asserts = sum(r["n_asserts"] for r in results)
    print(
        f"\nGRAND TOTAL: {len(results)} files, {tot_tests} tests, {tot_asserts} asserts, "
        f"ratio {tot_asserts / max(tot_tests, 1):.2f}"
    )

    if args.json:
        Path(args.json).write_text(
            json.dumps(
                {"zero": zero, "files": results, "total_tests": tot_tests,
                 "total_asserts": tot_asserts},
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\nJSON written to {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
