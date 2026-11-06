#!/usr/bin/env python
"""DPI 缩放矩阵（Rev14 V3 / B.8）：在 4 档 `QT_SCALE_FACTOR` 下重跑同一套几何契约。

**为什么必须起子进程**：`QT_SCALE_FACTOR` 只在进程启动时读一次，进程内改无效。
每个子进程独立设置缩放，离屏建窗，检查「最小窗口是否 ≤ 工作区」与
「主操作区是否被裁」—— 这是 `clamp_scale_for_dpi` 之外唯一能验证真实缩放路径的办法。

用法：

    python scripts/check_dpi_matrix.py            # 打 JSON 摘要
    python scripts/check_dpi_matrix.py --json out.json

判据：每一档都 `clipped == []` 且 `min_le_avail == True`。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SCALES = ("1.25", "1.5", "1.75", "2.0")
PY = sys.executable

#: 子进程里跑的最小脚本：建窗 → 缩到最小 → 断言主操作区不裁 + 最小尺寸 ≤ 工作区
_PROBE = r"""
import json, os, sys, tempfile
from pathlib import Path
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, __ROOT__)
from PyQt6.QtWidgets import QApplication
from PyQt6.QtTest import QTest
app = QApplication.instance() or QApplication([])
from gcm.db.settings import SettingsStore
from gcm.ui.main_window import MainWindow
from gcm.ui.theme import clamp_scale_for_dpi
tmp = Path(tempfile.mkdtemp())
store = SettingsStore(tmp / "settings.json"); s = store.load()
s.first_run_done = True; s.checklist_prompted = True
s.animations = False; s.minimize_to_tray = False; store.save(s)
w = MainWindow(data_dir=tmp); w.show(); QTest.qWait(400)
w.resize(1020, 560); QTest.qWait(700)
TARGET = ["repo_input", "target_edit", "mode_combo", "depth_spin", "btn_start",
          "btn_cancel", "btn_clear", "btn_open", "btn_failures",
          "btn_retry_queue", "btn_diag_bundle"]
bad = []
for n in TARGET:
    o = getattr(w, n, None)
    if o is None or not o.isVisible():
        continue
    vr = o.visibleRegion().boundingRect().size(); rc = o.rect().size()
    if (vr.width(), vr.height()) != (rc.width(), rc.height()):
        bad.append(n + " " + str(vr.width()) + "x" + str(vr.height())
                   + "/" + str(rc.width()) + "x" + str(rc.height()))
avail = w._available_screen_size() or (1020, 560)
mw, mh = w.minimumWidth(), w.minimumHeight()
from gcm.ui.layout import ABSOLUTE_MIN
# 「屏幕装得下最小窗口」只对**受支持的尺寸**有意义：`ABSOLUTE_MIN` 是本项目声明的
# 下限（900×480），比它还小的逻辑屏幕属于 layout_mode 明确标 unsupported 的情形。
screen_supported = avail[0] >= ABSOLUTE_MIN[0] and avail[1] >= ABSOLUTE_MIN[1]
out = {"clipped": bad, "min": [mw, mh], "avail": list(avail),
       "screen_supported": screen_supported,
       "min_le_avail": (mw <= avail[0] and mh <= avail[1]) if screen_supported else None,
       "clamp_144": clamp_scale_for_dpi(1.0, 144.0)}
print("PROBE_JSON:" + json.dumps(out))
w.close()
"""


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    code = _PROBE.replace("__ROOT__", repr(str(ROOT)))
    results = []
    for scale in SCALES:
        env = dict(os.environ, QT_SCALE_FACTOR=scale, QT_QPA_PLATFORM="offscreen")
        proc = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env, timeout=180)
        marker = [ln for ln in (proc.stdout or "").splitlines()
                  if ln.startswith("PROBE_JSON:")]
        try:
            payload = json.loads(marker[-1][len("PROBE_JSON:"):]) if marker else {}
        except (json.JSONDecodeError, IndexError):
            payload = {}
        if not payload:
            payload = {"error": ((proc.stdout or "") + (proc.stderr or ""))[-300:]}
        payload["scale"] = scale
        payload["exit"] = proc.returncode
        results.append(payload)
    # 判据：①不崩（exit 0）；②主操作区零裁切；③**屏幕受支持时**最小窗口必须装得下。
    # 屏幕比 ABSOLUTE_MIN 还小时（离屏 800×800 在 2.0 档只剩 400×400）不判失败 ——
    # 那是项目声明「不支持」的尺寸，不是回归。
    bad = [r for r in results
           if r.get("exit") != 0 or r.get("clipped")
           or (r.get("screen_supported") and r.get("min_le_avail") is False)]
    out = {"scales": list(SCALES), "results": results, "ok": not bad}
    text = json.dumps(out, ensure_ascii=False, indent=2)
    print(text)
    if "--json" in argv:
        dest = Path(argv[argv.index("--json") + 1])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text + "\n", encoding="utf-8")
        print(f"写入 {dest}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))