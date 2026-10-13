"""打包便携版 zip：dist/Git-clone-Max-portable/ → dist/Git-clone-Max-portable.zip。

用法：
    python scripts/build_portable_zip.py [--check]

为什么需要这个脚本：便携版 zip 是发布的双产物之一（`publish_release.py`、
`health_check.py`、`docs/RELEASE_CHECKLIST.md` 都依赖它），但此前**没有任何脚本
生成它** —— 只能手工压缩。后果是它会悄悄过期：实测发现 dist 里的 exe 已重建到
新版本，而 zip 仍停在两周前的旧版本，发布时会传出「新 exe + 旧 zip」的不一致产物。

内容结构（与历史产物保持一致，勿改）：
    zip 根目录直接是
        Git-clone-Max.exe
        _internal/...
    即 zip 的是 **portable 目录的内容**，而不再套一层 `Git-clone-Max-portable/`。

`--check`：只校验「zip 是否存在且不早于 portable 目录内容」，不重新打包；
用于发布前确认产物未过期。
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
PORTABLE_DIR = DIST / "Git-clone-Max-portable"
PORTABLE_ZIP = DIST / "Git-clone-Max-portable.zip"
EXE_NAME = "Git-clone-Max.exe"


def _newest_mtime(root: Path) -> float:
    """portable 目录内最新文件 mtime（不含目录本身）。"""
    latest = 0.0
    for p in root.rglob("*"):
        if p.is_file():
            try:
                latest = max(latest, p.stat().st_mtime)
            except OSError:
                continue
    return latest


def check() -> int:
    """校验便携版 zip 是否存在且不比其来源目录旧。"""
    if not PORTABLE_DIR.is_dir():
        print(f"[FAIL] 缺少便携版目录 {PORTABLE_DIR}（先跑 PyInstaller）")
        return 1
    if not PORTABLE_ZIP.is_file():
        print(f"[FAIL] 缺少便携版 zip {PORTABLE_ZIP}（跑本脚本不带 --check 生成）")
        return 1
    zip_mtime = PORTABLE_ZIP.stat().st_mtime
    src_mtime = _newest_mtime(PORTABLE_DIR)
    if zip_mtime + 1 < src_mtime:
        print(f"[FAIL] zip 已过期：zip={zip_mtime:.0f} < 目录最新={src_mtime:.0f}"
              "（zip 比来源旧 → 发布物不一致）")
        return 1
    with zipfile.ZipFile(PORTABLE_ZIP) as z:
        names = z.namelist()
        bad = z.testzip()
    if bad:
        print(f"[FAIL] zip 内容损坏：{bad}")
        return 1
    if EXE_NAME not in names:
        print(f"[FAIL] zip 根目录缺少 {EXE_NAME}")
        return 1
    print(f"[OK] 便携版 zip 未过期（{len(names)} 条目，{PORTABLE_ZIP.stat().st_size} 字节）")
    return 0


def build() -> int:
    """重新打包便携版 zip（覆盖同名旧文件）。"""
    if not PORTABLE_DIR.is_dir():
        print(f"[FAIL] 缺少便携版目录 {PORTABLE_DIR}（先跑 PyInstaller）")
        return 1
    if not (PORTABLE_DIR / EXE_NAME).is_file():
        print(f"[FAIL] {PORTABLE_DIR / EXE_NAME} 不存在")
        return 1
    if PORTABLE_ZIP.exists():
        try:
            PORTABLE_ZIP.unlink()
        except PermissionError:
            print(f"[FAIL] 无法覆盖 {PORTABLE_ZIP}（文件被占用？）"
                  "—— 请先关闭正在运行的 Git-clone-Max")
            return 1
    count = 0
    with zipfile.ZipFile(PORTABLE_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(PORTABLE_DIR.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(PORTABLE_DIR).as_posix())
                count += 1
    size = PORTABLE_ZIP.stat().st_size
    print(f"[OK] 已生成 {PORTABLE_ZIP}（{count} 条目，{size} 字节）")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="打包/校验便携版 zip")
    ap.add_argument("--check", action="store_true", help="只校验是否过期，不重新打包")
    args = ap.parse_args(argv)
    return check() if args.check else build()


if __name__ == "__main__":
    raise SystemExit(main())
