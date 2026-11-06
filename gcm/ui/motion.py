"""动效 token（Rev14 R14-12）：时长与缓动的唯一来源。

**为什么要有这张表**：220 / 180 / 600 三个数字此前散在 `batch_conclusion.py`
与 `row_anim.py` 里，各自硬编码。改一处就会与另一处失去比例关系 —— 把"开始"
调慢却忘了"结束脉冲"，同一套界面会出现两种节奏，用户感到的是"有时候响应快
有时候迟钝"，而不是"动效变慢了"。收进一处后，任何一个变化都整体生效。

**为什么没有 `hover` 时长**：Qt QSS 不支持 `transition`，hover 是即时换色，
没有"时长"这个概念（见 `qss_build.py` 顶部说明）。`FAST_MS` 留给将来需要
真过渡的自绘动画（例如 Pill 的状态切换），当前无消费点，故不导出。

**`reduced-motion` 不走这里**：动效开关是**调用方**的判断（`self._motion` /
`prefers_reduced_motion` / 用户设置 `ck_animations`），token 只描述"多长"。
把开关塞进 token 会让"时长"和"要不要动"两个概念混在一起。
"""
from __future__ import annotations

#: 状态切换：告知"已经开始了"（批次进度条从 0 填充到当前值）。
BASE_MS = 220
#: 终态脉冲：告知"结束了"（透明度 0.35 → 1.0 一次）。
PULSE_MS = 180
#: 行完成消隐：定位"刚刚完成的那一行"（行底色由语义色渐变回面板色）。
SLOW_MS = 600
#: 微反馈预留档（当前无消费点，供自绘过渡使用）。
FAST_MS = 120

#: 统一缓动：OutCubic —— 先快后慢，符合"开始"的物理直觉。
EASING_NAME = "OutCubic"


def easing():
    """返回 `QEasingCurve.Type.OutCubic`。

    延迟导入 Qt：本模块要能被纯逻辑测试（无 `QApplication`）直接 import，
    以便断言"UI 里不再出现裸毫秒数"这类静态事实。
    """
    from PyQt6.QtCore import QEasingCurve
    # 按 `EASING_NAME` 查表 —— 直接写死 `OutCubic` 会让那个常量成为"改了没效果"的
    # 第二份真相（审查实测：改 EASING_NAME 不产生任何影响）。
    return getattr(QEasingCurve.Type, EASING_NAME, QEasingCurve.Type.OutCubic)
