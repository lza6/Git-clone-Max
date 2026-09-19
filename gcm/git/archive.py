"""G48-7 归档下载：浅克隆 + git archive HEAD → zip（免入库，只要代码快照）。"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def _git(cwd: Path, *args: str):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {(r.stderr or '').strip()[:200]}")
    return r


def download_archive(url: str, dest_zip, ref: str = "HEAD", timeout: int = 600) -> Path:
    """浅克隆 url 到临时目录，git archive HEAD 打包 zip，随后清理临时目录。"""
    dest_zip = Path(dest_zip)
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="gcm_arch_"))
    try:
        clone = tmp / "src"
        _git(tmp, "clone", "--depth", "1", str(url), str(clone))
        _git(clone, "archive", "--format=zip", "--output", str(dest_zip), ref)
        return dest_zip
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
