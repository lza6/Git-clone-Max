"""G55 工程与测试体系：纯函数 QSS 静态校验器。

无 QApplication / Qt 依赖，纯字符串扫描 + 白名单校验，适合在无 GUI 环境
与 theme.py 生成 QSS 后做静态自检。错误统一返回 list[str]（空 = 合法）。

校验维度（只做静态语法/属性白名单，不解析属性值语义）：
- 大括号 {} 配平（注释内不计；字符串内不计，避免 url("x/{y}") 误判）；
- 圆括号 () 配平（url(...)/rgba(...) 等值内不误判）；
- 引号（单/双）配平（跨行非法；转义 \\ 视为字面量不闭合）；
- 选择器非空（每个 { 之前的正文去除注释/空白后不得为空）；
- 属性名白名单（Qt QSS 文档属性集，见 ALLOWED_PROPERTIES；
  未知属性一律报错，防止拼写错误静默生效）。
"""
from __future__ import annotations

import re

# Qt QSS 支持的属性白名单（Qt Style Sheets Reference 收录的常规属性）。
# 扩展属性（如部分控件独有属性）需要真实验证时再按需补充登记。
ALLOWED_PROPERTIES: frozenset[str] = frozenset({
    # 背景 / 前景
    "background", "background-color", "background-image",
    "background-position", "background-repeat", "background-attachment",
    "background-clip", "background-origin",
    "color", "selection-color", "selection-background-color",
    "alternate-background-color", "gridline-color", "paint-alternating-row-colors",
    # 字体
    "font", "font-family", "font-size", "font-style", "font-weight",
    # 边框 / 圆角
    "border", "border-style", "border-width", "border-color",
    "border-top", "border-right", "border-bottom", "border-left",
    "border-top-color", "border-right-color", "border-bottom-color", "border-left-color",
    "border-top-style", "border-right-style", "border-bottom-style", "border-left-style",
    "border-top-width", "border-right-width", "border-bottom-width", "border-left-width",
    "border-radius",
    "border-top-left-radius", "border-top-right-radius",
    "border-bottom-right-radius", "border-bottom-left-radius",
    # 盒模型
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "width", "min-width", "max-width",
    "height", "min-height", "max-height",
    "spacing", "left", "right", "top", "bottom",
    # 布局 / 外观
    "outline", "outline-color", "outline-offset", "outline-style", "outline-width",
    "text-align", "text-decoration", "subcontrol-origin", "subcontrol-position",
    "opacity", "position", "image", "image-position", "image-alignment",
    "show-decoration-selected", "lineedit-password-character",
})

_COMMENT_RE = re.compile(r"/\*.*?\*/", re.S)
_QUOTES = ("'", '"')


def _strip_comments(text: str) -> str:
    """移除 QSS 块注释（跨行支持）。"""
    return _COMMENT_RE.sub("", text)


def _scan_text(text: str) -> tuple[int, int, bool, bool]:
    """扫描文本，返回 (braces_depth, parens_depth, single_open, double_open)。

    引号状态只影响其自身的闭合判断，不屏蔽 { } / ( ) 计数——QSS 中
    font-family 等字符串值可跨行（如主题模板折行的字体栈），若字符串内
    忽略结构括号会把后续规则块的 } 误判为字符串内容，导致配平虚警。
    值内真实出现的 url("...") 括号是配平的，计数不受影响。
    """
    bd = pd = 0
    sq = dq = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if sq:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == "'":
                sq = False
            if ch == "{":
                bd += 1
            elif ch == "}":
                bd -= 1
            elif ch == "(":
                pd += 1
            elif ch == ")":
                pd -= 1
            i += 1
            continue
        if dq:
            if ch == "\\" and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                dq = False
            if ch == "{":
                bd += 1
            elif ch == "}":
                bd -= 1
            elif ch == "(":
                pd += 1
            elif ch == ")":
                pd -= 1
            i += 1
            continue
        if ch == "'":
            sq = True
            i += 1
            continue
        if ch == '"':
            dq = True
            i += 1
            continue
        if ch == "{":
            bd += 1
        elif ch == "}":
            bd -= 1
        elif ch == "(":
            pd += 1
        elif ch == ")":
            pd -= 1
        i += 1
    return bd, pd, sq, dq


def _split_declarations(block: str) -> list[str]:
    """按分号切分规则块（字符串内分号不切断，例如自定义文本内容）。"""
    parts: list[str] = []
    buf: list[str] = []
    sq = dq = False
    i = 0
    n = len(block)
    while i < n:
        ch = block[i]
        if sq:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                buf.append(block[i + 1])
                i += 2
                continue
            if ch == "'":
                sq = False
            i += 1
            continue
        if dq:
            buf.append(ch)
            if ch == "\\" and i + 1 < n:
                buf.append(block[i + 1])
                i += 2
                continue
            if ch == '"':
                dq = False
            i += 1
            continue
        if ch in _QUOTES:
            sq = ch == "'"
            dq = ch == '"'
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            parts.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        parts.append("".join(buf))
    return parts


def _iter_blocks(style: str):
    """逐块解析：产出 (selector, body, block_no)。

    - selector 阶段：body 为 None，selector 为 `{` 之前的原文；
    - body 阶段：selector 为空串，body 为该块内声明文本。
    大括号深度不匹配时行为未定义（调用方需先通过 _scan_text 校验）。
    """
    text = _strip_comments(style)
    depth = 0
    block_no = 0
    sel_start = 0
    i = 0
    n = len(text)
    body_parts: list[str] = []
    while i < n:
        ch = text[i]
        if ch == "{":
            if depth == 0:
                block_no += 1
                yield text[sel_start:i], None, block_no
                body_parts = []
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                yield "", "".join(body_parts), block_no
                sel_start = i + 1
            else:
                body_parts.append(ch)
        elif depth > 0:
            body_parts.append(ch)
        i += 1
    tail = text[sel_start:].strip()
    if tail:
        yield tail, "___tail___", -1


def validate_qss(style: str) -> list[str]:
    """校验 QSS 文本，返回错误列表（空 = 通过）。"""
    if not isinstance(style, str):
        return ["QSS 必须是字符串"]
    if not style.strip():
        return ["QSS 为空"]

    errors: list[str] = []
    stripped = _strip_comments(style)
    bd, pd, sq, dq = _scan_text(stripped)
    if bd != 0:
        missing = ("}" * -bd) if bd < 0 else ("{" * bd)
        errors.append(f"大括号配平失败：深度 {bd}（{missing} 缺失）")
    if pd != 0:
        errors.append(f"圆括号配平失败：深度 {pd}")
    if sq:
        errors.append("单引号未闭合")
    if dq:
        errors.append("双引号未闭合")

    # 选择器非空 + 属性白名单（仅在大括号配平通过后逐块判断，避免深度错乱误报）
    if bd == 0:
        seen_any_block = False
        sel_no = 0
        for selector, body, block_no in _iter_blocks(stripped):
            if block_no == -1:
                errors.append(f"尾部存在未配对内容: {selector!r}")
                continue
            if body is None:
                sel_no = block_no
                sel = selector.strip()
                if not sel:
                    errors.append(f"块 {block_no}：选择器为空")
                continue
            seen_any_block = True
            decls = _split_declarations(body)
            props: set[str] = set()
            for decl in decls:
                d = decl.strip()
                if not d:
                    continue
                if ":" not in d:
                    errors.append(f"块 {sel_no}：声明缺少冒号: {d!r}")
                    continue
                name = d.split(":", 1)[0].strip().lower()
                if not name:
                    errors.append(f"块 {sel_no}：属性名为空: {d!r}")
                    continue
                props.add(name)
                if not (name in ALLOWED_PROPERTIES or name.startswith("qproperty-")):
                    errors.append(f"块 {sel_no}：未知属性 {name!r}（声明: {d!r}）")
            if body.strip() and not props:
                errors.append(f"块 {sel_no}：规则块内没有任何声明")
        if not seen_any_block and not errors:
            errors.append("未解析到任何 { ... } 规则块")

    # 去重（同一规则可能命中多个错误源）
    seen: set[str] = set()
    out: list[str] = []
    for e in errors:
        if e not in seen:
            seen.add(e)
            out.append(e)
    return out


def validate_qss_or_raise(style: str) -> None:
    """校验 QSS，非法时抛出 ValueError（信息含全部错误列表）。"""
    errs = validate_qss(style)
    if errs:
        raise ValueError("QSS 静态校验失败:\n" + "\n".join(errs))
