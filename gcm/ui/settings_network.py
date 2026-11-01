"""C1-2：「并行与网络」组的高级限流两行（Rev7 §3.2 X-1 / §3.3 X-2）。

**为什么单独成模块**：`settings_groups.py` 已 779 行，贴着项目 800 行硬上限
（`tests/test_g79_repo_hygiene::TestNoNewOversizedModules` 用集合相等断言，多出第 4 个
超限文件即红）。这与 G76 把动作型回调抽到 `settings_actions.py`、G70 把 delegate 抽到
`progress_delegate.py` 是同一范式 —— **新增 UI 逻辑落独立模块，不塞进老文件**。

**修的是两个「可配置性伪装」**（用户够不到，而引擎在读）：

| 字段 | 引擎侧读取点 | 修复前的真实状态 |
|------|------------|-----------------|
| `host_concurrency` | `ui/engine_factory.py:40` → `SyncEngine(host_concurrency=…)` | 引擎读到后又被 `engine.py` 的一行硬编码覆盖成 4（C1-1 已删）；设置页**无控件** → 用户永远改不了；`0=不限流` 语义不可达 |
| `time_budget_min` | `ui/engine_factory.py:39` → `time_budget=分钟×60` | 设置页**无控件** → 单仓时间预算（A5-3 花大代价做的降级链预算）用户碰不到 |

两者的上下限都从 `db/validators.int_range()` 派生（单一真相，`G80-B5` 范式），
不在这里写死数字。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtWidgets import QFormLayout, QLabel, QSpinBox

from ..db.validators import int_range
from ..i18n import tr

if TYPE_CHECKING:  # pragma: no cover - 仅类型
    from gcm.db.settings import Settings

    from .main_window import MainWindow


def add_host_concurrency_row(owner: MainWindow, settings: Settings,
                             form: QFormLayout) -> None:
    """同一 host 的并发上限（0 = 不限流）。"""
    owner.spin_host_concurrency = QSpinBox()
    owner.spin_host_concurrency.setRange(*int_range("host_concurrency"))
    owner.spin_host_concurrency.setValue(
        int(getattr(settings, "host_concurrency", 4) or 0))
    owner.spin_host_concurrency.setSpecialValueText(tr("不限流"))
    owner.spin_host_concurrency.setToolTip(tr(
        "同一平台（如 github.com）同时在跑的仓库数上限。默认 4 —— 300 个仓库同时打\n"
        "同一个 host 会成片触发 secondary rate limit，限流后「成片失败」会变成「整体减速」。\n"
        "设为 0 = 不限流（只有在你确认不会被平台限流时才这么设）。\n"
        "改动在下一批任务生效（本批已在跑的保持原并发）。"))
    owner.spin_host_concurrency.valueChanged.connect(
        owner.settings_panel.wiring.save_host_concurrency)
    form.addRow(tr("同平台并发上限（0=不限）："), owner.spin_host_concurrency)


def add_time_budget_row(owner: MainWindow, settings: Settings,
                        form: QFormLayout) -> None:
    """单仓库总时间预算（分钟，0 = 不限）。"""
    owner.spin_time_budget = QSpinBox()
    owner.spin_time_budget.setRange(*int_range("time_budget_min"))
    owner.spin_time_budget.setValue(int(getattr(settings, "time_budget_min", 0) or 0))
    owner.spin_time_budget.setSuffix(tr(" 分钟"))
    owner.spin_time_budget.setSpecialValueText(tr("不限"))
    owner.spin_time_budget.setToolTip(tr(
        "单个仓库的总时间预算（含镜像探测、重试、treeless→depth1 降级链全部阶段）。\n"
        "超预算即如实报「已用尽时间预算」并跳过剩余重试 —— 避免「600s×3 重试×4 候选」\n"
        "叠成五六十分钟、把整批拖死在个别仓库上。0 = 不限（旧行为）。\n"
        "弱网/大仓建议 15–30 分钟。"))
    owner.spin_time_budget.valueChanged.connect(
        owner.settings_panel.wiring.save_time_budget_min)
    form.addRow(tr("单仓时间预算（0=不限）："), owner.spin_time_budget)


def add_limit_hint(form: QFormLayout) -> None:
    """两行的共同说明（用户最容易误解的点：已在跑的任务不回溯）。"""
    hint = QLabel(tr(
        "提示：以上两项对「下一个开始的任务」生效；已在跑的仓库保持启动时的设置不变。"))
    hint.setWordWrap(True)
    hint.setEnabled(False)          # 用禁用色弱化，避免抢走注意力
    form.addRow(hint)
