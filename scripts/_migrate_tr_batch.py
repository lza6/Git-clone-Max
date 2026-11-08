"""R14-18 i18n 批次迁移工具（D40 纪律：纯插入 + 前后 AST 断言 + 索引比对）。

用法：python scripts/_migrate_tr_batch.py gcm/ui/xxx.py
规则：
1. 只包**非 docstring、非 f-string 片段、未包 tr()** 的含中文单行字符串；
2. 含引号/反斜杠/换行的字面量跳过（留给人工批次）；
3. f-string 的字面片段按 tr(...).format() 模式单独处理（本批跳过）。
"""
import ast
import sys
from pathlib import Path


def byte_to_char(linestr: str, byte_off: int) -> int:
    if byte_off <= 0:
        return 0
    return len(linestr.encode("utf-8")[:byte_off].decode("utf-8", errors="ignore"))


def main(path: str) -> None:
    target = Path(path)
    src = target.read_text(encoding="utf-8")
    ast.parse(src)  # 前置断言
    tree = ast.parse(src)

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef,
                             ast.AsyncFunctionDef, ast.ClassDef)):
            if ast.get_docstring(node, clean=False) and node.body:
                docstrings.add(id(node.body[0].value))
    joined_parts = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            for v in node.values:
                if isinstance(v, ast.Constant):
                    joined_parts.add(id(v))
    tr_args = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr":
            for arg in node.args:
                tr_args.add(id(arg))
    # tr() 已经有键的集合（索引比对用）
    tr_keys_before = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr":
            if node.args and isinstance(node.args[0], ast.Constant):
                tr_keys_before.add(node.args[0].value)

    # 模块级/类级常量不迁：①app 层没有 tr；②模块级 tr() 在 set_language 前
    # 求值 → 语言切换后不更新（i18n 时序坑）。这些留给人工按需处理。
    const_values = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
            values = (node.values if isinstance(node, ast.Assign)
                      else [node.value])
            for v in values:
                for n in ast.walk(v):
                    if isinstance(n, ast.Constant):
                        const_values.add(id(n))
    edits = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if (id(node) in docstrings or id(node) in tr_args
                or id(node) in joined_parts or id(node) in const_values):
            continue
        v = node.value
        if not any('\u4e00' <= c <= '\u9fff' for c in v):
            continue
        if "\n" in v or node.lineno != node.end_lineno:
            continue
        edits.append((node.end_lineno, node.end_col_offset,
                      node.lineno, node.col_offset))

    lines = src.splitlines(keepends=True)
    edits.sort(key=lambda t: (t[0], t[1]), reverse=True)
    applied = skipped = 0
    for end_ln, end_col, start_ln, start_col in edits:
        line = lines[start_ln - 1]
        cs = byte_to_char(line, start_col)
        ce = byte_to_char(line, end_col)
        literal = line[cs:ce]
        if not literal.startswith(('"', "'")):
            skipped += 1
            continue
        inner = literal[1:-1]
        if '"' in inner or "\\" in inner or "'" in inner:
            skipped += 1
            continue
        lines[start_ln - 1] = line[:cs] + 'tr("' + inner + '")' + line[ce:]
        applied += 1

    new_src = "".join(lines)
    ast.parse(new_src)  # 后置断言（D40 纪律①）
    # tr() 键集合单调不减（D40 纪律②）
    tree2 = ast.parse(new_src)
    tr_keys_after = set()
    for node in ast.walk(tree2):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr":
            if node.args and isinstance(node.args[0], ast.Constant):
                tr_keys_after.add(node.args[0].value)
    lost = tr_keys_before - tr_keys_after
    assert not lost, f"tr() 键集合缩水：{lost}（D40 纪律②）"
    target.write_text(new_src, encoding="utf-8")
    print(f"{path}: applied={applied} skipped={skipped}")


if __name__ == "__main__":
    main(sys.argv[1])
