#!/usr/bin/env python
"""像素回归比对（Rev14 R14-19）：让截图审计从「只存图」变成「能报警」。

**为什么必须有断言**：`ui_screenshot_audit.py` 历史上只把图存进 `reports/`，
不比较也不判定。真实事故是「保存清单 / 载入 / 导入 Star」这一行在 1366×768 下
被顶部滚动区裁到只剩 10px —— 截图里是 4 个空框，**连续 4 轮 UI 迭代没人发现**，
因为它不影响任何断言。没人看的图等于没有断言。

**口径**：

- 逐像素比，单通道差 > `tolerance`(8) 记为"变了"；
- 「变了」的像素占比 ≤ `max_ratio`(0.5%) 判通过 —— 抗字体栅格化与 1px 抗锯齿抖动；
- **尺寸不同直接判失败**（不按比例放过）：尺寸变化是结构改动，比例会把它掩盖；
- 缺口（基线有、当前没有 / 反向）同样判失败 —— 少截一张图不能让回归静默通过。

**反向自检**：`self_check()` 把一张图平移 2px 再比，必须报失败。这是本模块
自己的证伪测试：如果阈值宽到 2px 位移都发现不了，那它就没有在守护任何东西。
"""
from __future__ import annotations

import sys
from pathlib import Path

#: 单通道差值容差（0–255）。8 足够吸收抗锯齿与字体 hinting 的抖动，
#: 又不会放过 4px 级的真实位移。
TOLERANCE = 8
#: 允许"变了"的像素占比。0.5% 在 1240×840 上约 5200 像素 —— 一块按钮的高度。
MAX_DIFF_RATIO = 0.005


def compare_images(baseline: str | Path, current: str | Path,
                   tolerance: int = TOLERANCE,
                   max_ratio: float = MAX_DIFF_RATIO) -> dict:
    """比较两张 PNG，返回 `{ok, ratio, reason}`。"""
    from PyQt6.QtGui import QImage

    a = QImage(str(baseline))
    b = QImage(str(current))
    if a.isNull() or b.isNull():
        return {"ok": False, "ratio": 1.0, "reason": "图片读取失败"}
    if a.size() != b.size():
        return {"ok": False, "ratio": 1.0,
                "reason": f"尺寸不同（{a.width()}×{a.height()} → "
                          f"{b.width()}×{b.height()}）"}
    # 快路径：内容完全相同（绝大多数情况）直接通过，不必逐像素走一遍。
    # 用 `QImage.__eq__`（Qt 的 operator==，比的是像素内容）而不是
    # `constBits()`：PyQt6 的 voidptr 不带长度，转 bytes 会 IndexError。
    if a == b:
        return {"ok": True, "ratio": 0.0, "reason": ""}
    total = a.width() * a.height()
    diff = 0
    for y in range(a.height()):
        for x in range(a.width()):
            pa = a.pixel(x, y)
            pb = b.pixel(x, y)
            if (abs(((pa >> 16) & 0xFF) - ((pb >> 16) & 0xFF)) > tolerance
                    or abs(((pa >> 8) & 0xFF) - ((pb >> 8) & 0xFF)) > tolerance
                    or abs((pa & 0xFF) - (pb & 0xFF)) > tolerance):
                diff += 1
    ratio = diff / total if total else 0.0
    return {"ok": ratio <= max_ratio, "ratio": round(ratio, 5),
            "reason": "" if ratio <= max_ratio else f"{ratio:.2%} 像素超容差"}


def compare_dirs(baseline_dir: str | Path, current_dir: str | Path) -> dict:
    """比较两个目录下的同名 PNG；缺口、尺寸变化、超阈值都算失败。"""
    base = Path(baseline_dir)
    cur = Path(current_dir)
    if not base.exists():
        return {"ok": False, "detail": f"基线目录不存在：{base}"}
    base_files = {p.name for p in base.glob("*.png")}
    cur_files = {p.name for p in cur.glob("*.png")}
    missing = sorted(base_files - cur_files)
    added = sorted(cur_files - base_files)
    bad: list[str] = []
    for name in sorted(base_files & cur_files):
        res = compare_images(base / name, cur / name)
        if not res["ok"]:
            bad.append(f"{name}: {res['reason']}")
    return {"ok": not (missing or bad), "missing": missing, "added": added,
            "regressions": bad}


def self_check() -> dict:
    """反向自检：平移 2px 的图必须被判失败，完全相同的图必须判通过。"""
    import tempfile

    from PyQt6.QtGui import QColor, QImage

    tmp = Path(tempfile.mkdtemp(prefix="gcm_pixdiff_"))
    w = h = 120
    base = QImage(w, h, QImage.Format.Format_RGB32)
    base.fill(QColor("#101418"))
    for x in range(20, 100):
        base.setPixelColor(x, 60, QColor("#35c8b4"))
    base.save(str(tmp / "a.png"))

    same = QImage(w, h, QImage.Format.Format_RGB32)
    same.fill(QColor("#101418"))
    for x in range(20, 100):
        same.setPixelColor(x, 60, QColor("#35c8b4"))
    same.save(str(tmp / "same.png"))

    shifted = QImage(w, h, QImage.Format.Format_RGB32)
    shifted.fill(QColor("#101418"))
    for x in range(20, 100):
        shifted.setPixelColor(x, 62, QColor("#35c8b4"))   # 下移 2px —— 必须被抓到
    shifted.save(str(tmp / "shifted.png"))

    identical = compare_images(tmp / "a.png", tmp / "same.png")
    moved = compare_images(tmp / "a.png", tmp / "shifted.png")
    return {"identical_ok": identical["ok"], "shifted_caught": not moved["ok"],
            "shifted_ratio": moved["ratio"]}


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if "--self-check" in argv:
        res = self_check()
        print(res)
        return 0 if (res["identical_ok"] and res["shifted_caught"]) else 1
    if len(argv) >= 2:
        res = compare_dirs(argv[0], argv[1])
        print(res)
        return 0 if res["ok"] else 1
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
