"""文件 SHA256 计算与比对（G80-B4-2）：脚本与 UI 共用一份实现。

为什么单独成模块：`scripts/health_check.py` 里已有一份分块 sha256（1MiB 分块，
避免把 41MB 的 exe 整读进内存），而「更新后校验下载的安装包」需要同一套算法。
两份实现迟早漂移（一份改了分块大小、另一份没改），因此抽到这里，
脚本与 UI 都调它 —— 校验值不一致时必须能确定"是文件不同"而不是"算法不同"。
"""
from __future__ import annotations

from pathlib import Path

CHUNK_SIZE = 1 << 20  # 1 MiB

# 四种结论，UI 直接按它选文案（不要用 True/False 丢信息）
OUTCOME_MATCH = "match"
OUTCOME_MISMATCH = "mismatch"
OUTCOME_NO_BASELINE = "no_baseline"
OUTCOME_UNREADABLE = "unreadable"


def sha256_file(path: str | Path, chunk_size: int = CHUNK_SIZE) -> str:
    """分块计算文件 sha256（小写十六进制）。文件不可读时抛 OSError。"""
    import hashlib

    h = hashlib.sha256()
    with open(Path(path), "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def verify_by_sha256(path: str | Path, expected: str) -> tuple[bool, str, str]:
    """比对文件与期望的 sha256。

    返回 (ok, actual_sha, outcome)：
    - `("match")` 一致；`("mismatch")` 不一致；
    - `("no_baseline")` 没给期望值（**明说无法比对，不假装成功**）；
    - `("unreadable")` 文件读不了（actual 为空串）。
    期望值里允许带 `sha256:` 前缀、空格与大写，比对前统一归一化。
    """
    want = str(expected or "").strip().lower()
    if want.startswith("sha256:"):
        want = want[len("sha256:"):].strip()
    if not want:
        return (False, "", OUTCOME_NO_BASELINE)
    try:
        actual = sha256_file(path)
    except OSError:
        return (False, "", OUTCOME_UNREADABLE)
    if actual == want:
        return (True, actual, OUTCOME_MATCH)
    return (False, actual, OUTCOME_MISMATCH)
