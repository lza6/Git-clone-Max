"""G45-9 打包产物健康检查：发布前对 dist/ 产物做存在性、SHA-256、版本一致性、
离屏启动冒烟检查，输出 JSON 台账（stdout，可用 --json <path> 落盘）。

检查项与断言：
1. artifacts_exist : dist/Git-clone-Max.exe 与 dist/Git-clone-Max-portable.zip 必须存在。
2. exe/zip sha256 : 分块读取（1MiB）计算，避免大文件整读。
3. 版本一致性      : 产物内版本必须等于源码 gcm/__init__.py 的 __version__。
   - 产物版本主路径：用 PyInstaller 的 CArchiveReader 打开 onefile exe 的
     PKG/CArchive 归档，提取数据条目 gcm/__init__.py（spec datas 包含
     ('gcm','gcm')，TOC 键以反斜杠分隔），正则读取 __version__。
   - 降级路径：PyInstaller 不可用或提取失败时，读便携版 onedir
     dist/Git-clone-Max-portable/_internal/gcm/__init__.py。
   - 失败语义：提取失败(archive_version=None) 或与源码不一致 → 断言 FAIL，
     JSON 台账如实记录 archive_version 与差异；不改产物、不改源码。
4. started_ok      : QT_QPA_PLATFORM=offscreen + GCM_DATA_DIR=<临时目录>，
     Popen 启动 exe，等待约 8s，进程仍存活（未闪退）。
5. repos_db_created: 冒烟期间临时数据目录生成 repos.db 或 settings.json。
   冒烟结束：proc.terminate()（5s 内未退出则 kill()）并 wait 回收，删除临时目录，
   不残留进程/文件。

退出码：全部断言通过 → 0；任一失败 → 1（JSON 仍输出，便于登记失败原因）。

用法：
    python scripts/health_check.py
    python scripts/health_check.py --json dist/health_check.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
EXE = DIST / "Git-clone-Max.exe"
ZIP = DIST / "Git-clone-Max-portable.zip"
SRC_INIT = ROOT / "gcm" / "__init__.py"
ONEDIR_INIT = DIST / "Git-clone-Max-portable" / "_internal" / "gcm" / "__init__.py"

# PyInstaller CArchive 的 TOC 键用反斜杠分隔（spec datas ('gcm','gcm') 打包后为 gcm/__init__.py）
ARCHIVE_INIT_KEY = "gcm/__init__.py"
INIT_MARKER = re.compile(rb'__version__\s*=\s*["\']([^"\']+)["\']')
CHUNK = 1024 * 1024  # 分块读 1MiB
SMOKE_WAIT_S = 12.0


def _sha256_chunked(path: Path) -> str:
    """大文件分块 SHA-256，避免整文件读入内存。"""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(CHUNK)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _version_from_text(data: bytes) -> str | None:
    m = INIT_MARKER.search(data)
    return m.group(1).decode("utf-8", errors="replace") if m else None


def _source_version() -> str | None:
    try:
        return _version_from_text(SRC_INIT.read_bytes())
    except OSError:
        return None


def _archive_version(exe_path: Path) -> str | None:
    """从 onefile 归档提取 gcm/__init__.py 读版本号（主路径）。

    TOC 键不硬编码反斜杠：按归一化名称匹配，跨平台稳健。
    """
    try:
        from PyInstaller.archive.readers import CArchiveReader
        archive = CArchiveReader(str(exe_path))
        key = next(
            (k for k in archive.toc if k.replace("\\\\", "/") == ARCHIVE_INIT_KEY),
            None,
        )
        if key is None:
            return None
        return _version_from_text(archive.extract(key))
    except Exception:
        return None


def _onedir_version() -> str | None:
    """降级：便携版 onedir 展开后的源码文件（spec datas 同样携带 gcm/）。"""
    try:
        return _version_from_text(ONEDIR_INIT.read_bytes())
    except OSError:
        return None


def _artifact_version(exe_path: Path) -> tuple[str | None, str]:
    """产物版本 + 提取路径说明（失败语义：返回 None 即无法确认）。"""
    v = _archive_version(exe_path)
    if v is not None:
        return v, "pyinstaller-archive"
    v = _onedir_version()
    if v is not None:
        return v, "onedir-fallback"
    return None, "unavailable"


def _run_smoke(exe_path: Path, wait_s: float) -> tuple[bool, bool, str]:
    """离屏启动冒烟；返回 (started_ok, repos_db_created, note)。"""
    tmp = Path(tempfile.mkdtemp(prefix="gcm_health_"))
    proc: subprocess.Popen | None = None
    out_fh = err_fh = None
    try:
        env = os.environ.copy()
        env["QT_QPA_PLATFORM"] = "offscreen"
        env["GCM_DATA_DIR"] = str(tmp)
        out_fh = open(tmp / "child_stdout.log", "wb")
        err_fh = open(tmp / "child_stderr.log", "wb")
        proc = subprocess.Popen([str(exe_path)], env=env,
                                stdout=out_fh, stderr=err_fh)
        start_t = time.time()
        while time.time() < start_t + wait_s:
            if proc.poll() is not None:
                break
            if (tmp / "repos.db").is_file() or (tmp / "settings.json").is_file():
                break
            time.sleep(0.5)
        time.sleep(0.3)
        if proc.poll() is None:
            db_ok = (tmp / "repos.db").is_file()
            settings_ok = (tmp / "settings.json").is_file()
            note = f"alive after {wait_s:g}s; repos.db={db_ok} settings.json={settings_ok}"
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
            return True, db_ok or settings_ok, note
        note = f"exited early rc={proc.returncode}"
        errlog = tmp / "error.log"
        if errlog.is_file():
            note += "; data/error.log=" + repr(
                errlog.read_text(encoding="utf-8", errors="replace")[:400])
        return False, False, note
    finally:
        for fh in (out_fh, err_fh):
            if fh is not None:
                fh.close()
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Git-clone-Max 打包产物健康检查")
    ap.add_argument("--exe", type=Path, default=EXE, help="onefile exe 路径")
    ap.add_argument("--zip", type=Path, default=ZIP, help="portable zip 路径")
    ap.add_argument("--wait", type=float, default=SMOKE_WAIT_S, help="启动冒烟等待秒数")
    ap.add_argument("--json", type=Path, default=None, metavar="PATH",
                    help="JSON 台账落盘路径（可选）")
    args = ap.parse_args()

    started = time.perf_counter()
    fails: list[str] = []

    def report(ok: bool, tag: str, msg: str) -> None:
        print(f"[{'OK' if ok else 'FAIL'}] {tag}: {msg}")
        if not ok:
            fails.append(tag)

    artifacts_exist = args.exe.is_file() and args.zip.is_file()
    report(artifacts_exist, "artifacts_exist",
           f"exe={args.exe.is_file()} zip={args.zip.is_file()}")

    exe_sha = _sha256_chunked(args.exe) if args.exe.is_file() else ""
    zip_sha = _sha256_chunked(args.zip) if args.zip.is_file() else ""
    print(f"[OK] exe sha256={exe_sha[:16]}... zip sha256={zip_sha[:16]}...")

    src_v = _source_version()
    art_v, art_src = _artifact_version(args.exe)
    report(src_v is not None, "source_version",
           f"gcm/__init__.py __version__={src_v!r}")
    report(art_v is not None, "artifact_version",
           f"from {art_src} __version__={art_v!r}")
    version_ok = bool(src_v and art_v and src_v == art_v)
    report(version_ok, "version_match",
           f"src={src_v!r} artifact={art_v!r} ({art_src})")

    started_ok, db_ok, note = _run_smoke(args.exe, args.wait)
    report(started_ok, "started_ok", note)
    report(db_ok, "repos_db_created",
           "repos.db/settings.json 已生成于 GCM_DATA_DIR" if db_ok else "缺失")

    ledger = {
        "version": src_v or "",
        "archive_version": art_v or "",
        "exe_sha256": exe_sha,
        "zip_sha256": zip_sha,
        "exe_size": args.exe.stat().st_size if args.exe.is_file() else 0,
        "zip_size": args.zip.stat().st_size if args.zip.is_file() else 0,
        "started_ok": started_ok,
        "repos_db_created": db_ok,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "artifacts_exist": artifacts_exist,
    }
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[INFO] 台账已写入 {args.json}")

    print(json.dumps(ledger, ensure_ascii=True, indent=2))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())