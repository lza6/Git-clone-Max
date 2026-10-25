"""后台任务状态登记表（G80-B7-1）。

解决什么问题
------------
应用里有 4 个"看不见的后台定时器"：自动更新检查、看门狗续跑、阈值告警扫描、
定时报表。它们**只有间隔设置，没有任何地方能看出它下次什么时候跑、上次跑成功了没**。
用户遇到"报表没生成""告警没响"时无从判断是没开、还没到点、还是上一次失败了。

设计取舍（为什么不改各调度器）
------------------------------
各调度器形态不同（QTimer / 线程 Event 循环 / 线程 + 回调），直接改造它们去暴露
next-run 会把「状态展示」和「调度」耦在一起，回归面大。这里用**旁挂登记表**：
每个调度器在"真的跑了一次"时调一次 `notify_run(key, error)`，
本模块负责记录时间并按 interval 推算下次时间。调度逻辑一行没动。

唯一边界：**进程内、非持久**。进程重启后 last_run 归零（与 EventBus 同一取舍），
需要跨重启审计请看 `notify.log` / `app.log` / 报表文件本身。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

# 4 个后台任务的标准 key（UI 与调度器共用，别写字符串字面量）
JOB_AUTO_UPDATE = "auto_update"
JOB_WATCHDOG = "watchdog"
JOB_ALERTS = "alerts"
JOB_REPORTS = "reports"


@dataclass
class JobStatus:
    """一个后台任务的当前状态（供 UI 直接渲染）。"""

    key: str
    title: str
    enabled: bool
    interval_sec: float = 0.0
    last_run_at: float = 0.0          # 0 = 本次进程内还没跑过
    last_error: str = ""
    last_result: str = ""

    @property
    def next_run_at(self) -> float:
        """下次预计运行时间（0 = 未知：未启用或没有间隔）。"""
        if not self.enabled or self.interval_sec <= 0:
            return 0.0
        if self.last_run_at <= 0:
            return 0.0  # 还没跑过：不编造时间，UI 显示「启动后首轮」
        return self.last_run_at + self.interval_sec


class BackgroundJobRegistry:
    """进程内后台任务状态表（线程安全）。"""

    def __init__(self, now_fn=None):
        self._now = now_fn or time.time
        self._lock = threading.RLock()
        self._status: dict[str, JobStatus] = {}
        self._order: list[str] = []

    def register(self, key: str, title: str, *, enabled: bool,
                 interval_sec: float = 0.0) -> None:
        """登记/更新一个后台任务（重复登记覆盖配置，保留 last_run/error）。"""
        with self._lock:
            cur = self._status.get(key)
            if cur is None:
                self._status[key] = JobStatus(
                    key=key, title=title, enabled=bool(enabled),
                    interval_sec=float(interval_sec or 0))
                self._order.append(key)
                return
            cur.title = title
            cur.enabled = bool(enabled)
            cur.interval_sec = float(interval_sec or 0)

    def notify_run(self, key: str, *, error: str = "", result: str = "") -> None:
        """调度器真的跑完一轮时调用（成功与失败都调）。"""
        with self._lock:
            cur = self._status.get(key)
            if cur is None:
                return  # 未登记的任务不凭空造条目
            cur.last_run_at = self._now()
            cur.last_error = str(error or "")
            cur.last_result = str(result or "")

    def snapshot(self) -> list[JobStatus]:
        """按登记顺序返回快照（拷贝，供 UI 渲染；不持有锁）。"""
        with self._lock:
            return [JobStatus(**vars(self._status[k])) for k in self._order]

    def reset(self) -> None:
        """测试用：清空登记。"""
        with self._lock:
            self._status.clear()
            self._order.clear()


# 进程级单例：调度器与 UI 共用（不传参，避免层层注入）
_REGISTRY = BackgroundJobRegistry()


def registry() -> BackgroundJobRegistry:
    return _REGISTRY


def notify_run(key: str, *, error: str = "", result: str = "") -> None:
    """便捷入口：调度器在跑完一轮后调用。"""
    _REGISTRY.notify_run(key, error=error, result=result)


def register_default_jobs(settings) -> None:
    """按当前设置登记 4 个后台任务的开关与间隔（启动时 / 设置保存后各调一次）。

    间隔单位从设置里转换：自动更新/看门狗/告警/报表都是「分钟」。
    """
    minutes = {
        JOB_AUTO_UPDATE: getattr(settings, "auto_update_minutes", 0) or 0,
        JOB_WATCHDOG: getattr(settings, "watchdog_interval_min", 0) or 0,
        JOB_ALERTS: getattr(settings, "g53_alerts_interval_min", 0) or 0,
        JOB_REPORTS: getattr(settings, "sched_report_interval_min", 0) or 0,
    }
    titles = {
        JOB_AUTO_UPDATE: "自动更新检查",
        JOB_WATCHDOG: "看门狗续跑",
        JOB_ALERTS: "阈值告警扫描",
        JOB_REPORTS: "定时报表",
    }
    enabled = {
        JOB_AUTO_UPDATE: bool(minutes[JOB_AUTO_UPDATE]),
        JOB_WATCHDOG: bool(getattr(settings, "watchdog_enabled", False)),
        JOB_ALERTS: bool(getattr(settings, "g53_alerts_enabled", False)),
        JOB_REPORTS: bool(getattr(settings, "sched_report_enabled", False)),
    }
    for key in (JOB_AUTO_UPDATE, JOB_WATCHDOG, JOB_ALERTS, JOB_REPORTS):
        _REGISTRY.register(key, titles[key], enabled=enabled[key],
                           interval_sec=float(minutes[key]) * 60.0)
