## 发现

1. **P1(待验证)| main_window.py `_make_alert_emitter`** | diff 删除了该函数内的 `import weakref`（五条已接受取舍均未覆盖此改动，属未申报的非纯编辑），且 diff 全文无任何模块级 `import weakref` 接管；该函数体确实使用 weakref（docstring「用弱引用」+「先建引用再定义闭包」+ 原局部 import 被 ruff 静默说明它被使用）。若模块级缺此 import，首次调用 `weakref.ref(owner)` 抛 NameError → 被 `wire_background_services` 的 `except Exception: owner.alert_scanner = None` 吞掉 → **告警扫描器静默失效且无日志**——与本轮刚抓过的 `owner._fmt_dt` 被 `except pass` 吞掉完全同形，且已列的四项真 E2E（smoke/克隆/下载/diag）均不覆盖告警链 | 一次 grep 确认 main_window.py 模块级有 `import weakref`：有 → 本条降为冗余 import 清理，无问题；无 → 恢复该行。本条决定最终判定

2. **P3 | startup_wiring.py 模块头** | `from ..models import SyncResult, SyncStatus  # noqa: F401` 为死 import——136 行全文零引用，是纯搬移无中生有的对象 | 删除，待注解真正用到时再加

3. **P3 | startup_wiring.py `wire_background_services` 告警段** | `except Exception: owner.alert_scanner = None` 静默吞（忠实保留原状，非本次引入），而并排的 register_default_jobs / `_setup_queue_runner` 两处异常都走 `_applog_warn` 留痕——不一致；且正是这个吞法把发现 1 的潜在失败变成无痕 | 不属本次搬移范围，下轮同位补 `_applog_warn`

## 四维核查通过项

- **① 纯性**：七段一一对应（G33-6→`record_startup_elapsed`；G37-4→`wire_auto_update_timer`；G52-2/3 + G59-7 + 报表 + G80-B7-1 + G80-B7-3 + crypto→`wire_background_services`），调用位置与相对顺序逐行比对同原，所有 `getattr` 默认值、kwargs、连接目标零漂移，注释随段迁移；T1a 三常量 96/112/560 与原字面量等值且有消费守卫。唯一非纯点 = 发现 1。
- **② 引用环**：`_scan_repos` 仅捕获 `_self_ref`（weakref）与 `scan_alerts` 函数对象；内层 lambda 的 `me` 是调用期局部；无任何新闭包强持 owner；新模块无模块级状态；Watchdog/QTimer 连接 bound method 均为搬移前既有模式（parent-child 销毁 + G69-18 守卫覆盖）。
- **③ 线程亲和**：三个 wire 函数在 `__init__` 内 GUI 线程同步执行，`QTimer(owner)` 与 2500/300/500 三个 singleShot 亲和性与原完全一致，未引入任何跨线程创建或跨线程 emit。
- **④ 异常留痕**：register/queue 均有日志；crypto 的 `pass` 为已接受取舍且有 TestCryptoWarningLogged 行为覆盖；唯一静默缺口 = 发现 3。

判定 PASS 的前提是发现 1 的 grep 落在「模块级已有 import」一侧（ruff 0 error / mypy Success / 2267 测试全绿均间接支持该侧）；若落在另一侧，本搬移判 FAIL，修复为恢复一行。

VERDICT: PASS
