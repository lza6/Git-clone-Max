"""changelog 碎片工具（G81 / N-4）：new / build / check。

为什么要有它：`gcm/__init__.py` 曾同时承载「版本号 + 76 条 changelog」，
150/163 行是 changelog 文本、最长单条 1,347 字符、被 88/320 次提交触碰（27.5%）。
任何一次改动都容易在这个巨型 tuple 上冲突。拆分后的模型：

    开发期：往 `changelog.d/` 丢碎片（一个小文件，几乎不会冲突）
    发布时：`build` 把碎片聚合成一条，插到 `gcm/_changelog_data.py` 顶部
    平时：`check` 守护格式与一致性（CI / 本地都跑）

碎片命名：`<slug>.<category>.md`，category 取 added / changed / fixed / internal
（对齐 Keep-a-Changelog 与 towncrier 的惯用分类）。

碎片格式（极简，不引 YAML 依赖）：

    # <一句话能力>
    <细节第 1 行>
    <细节第 2 行>
    @verify <验证方式：跑了什么、结果如何>

第一条 `# ` 是标题，其余非 `@verify` 行拼成细节，`@verify` 行进 `· <验证>` 段。

用法：
    python scripts/changelog.py new --category fixed --slug mirror-cred-leak
    python scripts/changelog.py check
    python scripts/changelog.py build --version 9.11.0

退出码：0 = 成功/一致性通过；1 = 校验失败；2 = 用法错误。
"""
from __future__ import annotations

import argparse
import ast
import datetime as _dt
import pathlib
import re
import sys

# CI Windows runner 的 stdout 默认 cp1252 —— 中文 [OK]/[FAIL] 输出直接
# UnicodeEncodeError（实测 e37b511 三平台红）。脚本入口强制 UTF-8。
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

ROOT = pathlib.Path(__file__).resolve().parent.parent
FRAGMENT_DIR = ROOT / "changelog.d"
DATA_FILE = ROOT / "gcm" / "_changelog_data.py"
INIT_FILE = ROOT / "gcm" / "__init__.py"

CATEGORIES = ("added", "changed", "fixed", "internal")
_FRAGMENT_RE = re.compile(
    r"^(?P<slug>[a-z0-9][a-z0-9._-]*)\.(?P<cat>" + "|".join(CATEGORIES) + r")\.md$")


class Fragment:
    __slots__ = ("path", "slug", "category", "title", "details", "verify")

    def __init__(self, path: pathlib.Path):
        self.path = path
        m = _FRAGMENT_RE.match(path.name)
        if not m:
            raise ValueError(
                f"碎片文件名不合规：{path.name}"
                f"（应为 <slug>.({'|'.join(CATEGORIES)}).md）")
        self.slug = m.group("slug")
        self.category = m.group("cat")
        title, details, verify = "", [], ""
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line:
                continue
            if line.startswith("@verify"):
                verify = line[len("@verify"):].strip()
            elif line.startswith("# ") and not title:
                title = line[2:].strip()
            elif not title:
                raise ValueError(f"{path.name}: 第一条非空行必须是 '# <标题>'")
            else:
                details.append(line)
        if not title:
            raise ValueError(f"{path.name}: 缺少标题行")
        self.title = title
        self.details = details
        self.verify = verify

    def render(self, version: str) -> str:
        body = f"{version}: {self.title}"
        if self.details:
            body += " - " + " ".join(self.details)
        if self.verify:
            body += " · " + self.verify
        return body


def load_fragments() -> list[Fragment]:
    if not FRAGMENT_DIR.is_dir():
        return []
    out: list[Fragment] = []
    for p in sorted(FRAGMENT_DIR.glob("*.md")):
        if p.name.upper() == "README.MD":
            continue
        out.append(Fragment(p))
    return out


def read_entries() -> tuple[str, ...]:
    """读 `_changelog_data.py` 的 ENTRIES（AST 解析，不执行模块）。"""
    tree = ast.parse(DATA_FILE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign) and getattr(
                getattr(node.target, "id", ""), "__str__", lambda: "")() == "ENTRIES":
            return tuple(ast.literal_eval(node.value))
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "ENTRIES" for t in node.targets):
            return tuple(ast.literal_eval(node.value))
    raise SystemExit(f"[ERR] {DATA_FILE} 里找不到 ENTRIES")


def write_entries(entries: tuple[str, ...]) -> None:
    """重写 `_changelog_data.py`，保留文件头说明（只换 ENTRIES 段）。"""
    text = DATA_FILE.read_text(encoding="utf-8")
    head = text[:text.index("ENTRIES: tuple[str, ...] = (")]
    body = "".join(f"    {e!r},\n" for e in entries)
    DATA_FILE.write_text(
        head + "ENTRIES: tuple[str, ...] = (\n" + body + ")\n", encoding="utf-8")


def cmd_new(args: argparse.Namespace) -> int:
    FRAGMENT_DIR.mkdir(exist_ok=True)
    slug = re.sub(r"[^a-z0-9._-]+", "-", args.slug.lower()).strip("-")
    if not slug:
        print("[ERR] --slug 清洗后为空", file=sys.stderr)
        return 2
    path = FRAGMENT_DIR / f"{slug}.{args.category}.md"
    if path.exists():
        print(f"[ERR] 碎片已存在：{path.relative_to(ROOT)}", file=sys.stderr)
        return 1
    path.write_text(
        f"# {args.title}\n"
        + ("\n".join(args.detail) + "\n" if args.detail else "")
        + (f"@verify {args.verify}\n" if args.verify else ""),
        encoding="utf-8")
    print(f"[OK] 已创建 {path.relative_to(ROOT)}")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    problems: list[str] = []
    try:
        frags = load_fragments()
    except ValueError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        return 1
    for f in frags:
        if len(f.title) < 8:
            problems.append(f"{f.path.name}: 标题太短（{len(f.title)} 字符）")
    entries = read_entries()
    if not entries:
        problems.append("ENTRIES 为空")
    # 首条必须带当前版本前缀 —— 防止改了 __version__ 却忘了写 changelog
    init_src = INIT_FILE.read_text(encoding="utf-8")
    m = re.search(r'__version__\s*=\s*"([^"]+)"', init_src)
    if not m:
        problems.append("gcm/__init__.py 里找不到 __version__")
    else:
        ver = m.group(1)
        if not entries[0].startswith(f"{ver}: "):
            problems.append(
                f"ENTRIES 首条未以 {ver!r} 开头（当前：{entries[0][:40]!r}…）"
                f" —— 发版后必须 build 出对应条目")
    if problems:
        for p in problems:
            print(f"[FAIL] {p}", file=sys.stderr)
        return 1
    print(f"[OK] changelog 一致：{len(entries)} 条历史，{len(frags)} 条待发布碎片")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    ver = args.version.strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", ver):
        print(f"[ERR] 版本号格式应为 X.Y.Z：{ver!r}", file=sys.stderr)
        return 2
    frags = load_fragments()
    if not frags:
        print("[ERR] changelog.d/ 下没有待发布碎片", file=sys.stderr)
        return 1
    # 同类别按文件名排序，保证可重放（同样的输入必然产出同样的条目顺序）
    order = {c: i for i, c in enumerate(CATEGORIES)}
    frags.sort(key=lambda f: (order[f.category], f.slug))
    entry = " + ".join(f.render(ver) for f in frags)
    entries = read_entries()
    if entries and entries[0].startswith(f"{ver}: "):
        print(f"[ERR] {ver} 已存在条目，拒绝重复 build", file=sys.stderr)
        return 1
    write_entries((entry,) + entries)
    archive = FRAGMENT_DIR / "_archive" / ver
    archive.mkdir(parents=True, exist_ok=True)
    for f in frags:
        f.path.replace(archive / f.path.name)
    print(f"[OK] 已聚合 {len(frags)} 条碎片为 {ver} 条目；"
          f"碎片归档到 changelog.d/_archive/{ver}/")
    if not args.no_date:
        print(f"[提示] 记得同步 __version__ = \"{ver}\" 与其它 5 处版本载体")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="changelog 碎片工具")
    sub = ap.add_subparsers(dest="cmd", required=True)

    n = sub.add_parser("new", help="新建一条待发布碎片")
    n.add_argument("--category", choices=CATEGORIES, required=True)
    n.add_argument("--slug", required=True, help="短名（小写字母/数字/连字符）")
    n.add_argument("--title", required=True, help="一句话能力（≥8 字符）")
    n.add_argument("--detail", action="append", default=[],
                   help="细节行，可重复")
    n.add_argument("--verify", default="", help="验证方式（进 · 段）")
    n.set_defaults(func=cmd_new)

    c = sub.add_parser("check", help="校验碎片格式与版本一致性")
    c.set_defaults(func=cmd_check)

    b = sub.add_parser("build", help="把碎片聚合成一条并写入 _changelog_data.py")
    b.add_argument("--version", required=True)
    b.add_argument("--no-date", action="store_true", help="不提示日期")
    b.set_defaults(func=cmd_build)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
