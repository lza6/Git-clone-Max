"""应用入口：数据目录解析 + 主窗口/CLI 启动（G45-3 / G49-2 / G49-3 / G49-6 / G56-3/4）。"""
from __future__ import annotations

import os
import sys
from pathlib import Path


def _default_base() -> Path:
    """可执行文件（打包）/源码根目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def get_data_dir(portable: bool = False) -> Path:
    """数据目录解析（G49-2）：

    1. GCM_DATA_DIR 环境变量 → 直接用（最高优先级，已有行为）；
    2. --portable（portable=True）→ <exe/源码>/data（现状默认，随程序移动）；
    3. 默认 → %APPDATA%/Git-clone-Max；但若 <exe/源码>/data 已存在
       （旧版本用户升级）则继续沿用，保证数据不丢、无缝升级；
       无 APPDATA 的极简环境退回 <源码>/data。
    """
    env = os.environ.get("GCM_DATA_DIR")
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    base = _default_base()
    legacy = base / "data"
    if portable:
        legacy.mkdir(parents=True, exist_ok=True)
        return legacy
    if legacy.exists():
        return legacy
    for var in ("APPDATA", "XDG_DATA_HOME"):
        ap = os.environ.get(var)
        if ap:
            d = Path(ap) / "Git-clone-Max"
            d.mkdir(parents=True, exist_ok=True)
            return d
    legacy.mkdir(parents=True, exist_ok=True)
    return legacy


def _parse_flags(argv=None) -> dict:
    """解析 CLI 旗标：--portable / --minimized / --cli / --json / --diag / --serve / 写放行。"""
    args = list(sys.argv[1:] if argv is None else argv)
    return {
        "portable": "--portable" in args,
        "minimized": "--minimized" in args,
        "cli": "--cli" in args,
        "json": "--json" in args,
        "diag": "--diag" in args,
        "serve": "--serve" in args,
        "allow_mutate": "--allow-mutate" in args,
        # G66-8：--help/-h 必须在任何分支之前拦截（原先会落进 GUI 分支并挂死）
        "help": "--help" in args or "-h" in args,
    }


# G80-B1：仅 GUI 生效的全局标志，不属于 CLI argv ——
# 若原样交给 `cli.main`，它会被当成「URLs 文件名」（实测：读取失败 "No such file
# or directory: '--portable'" → exit 2，且 `--cli --portable` / `--portable --cli`
# 两种顺序都中招）。cli.py 只剥自己认识的标志，剥不到这两个。
_GUI_ONLY_FLAGS = ("--portable", "--minimized")


def _strip_global_flags(argv) -> list:
    """剥掉 GUI 专属全局标志，避免 CLI 把它们当输入文件名。"""
    return [a for a in argv if a not in _GUI_ONLY_FLAGS]


def _enable_event_sink(portable: bool = False) -> None:
    """G84-5：把事件落盘挂到全局总线（GUI / CLI / --serve 三形态唯一入口）。

    `app/event_sink.py` 是 Rev8 C5-3 的产物，实现完整但**此前没有任何生产调用点**
    （全仓只有测试引用）→ 用户机器上「事件跨重启可审计」从未成立。
    这里统一挂载；失败只记日志，绝不影响启动与克隆。
    """
    try:
        from gcm.app.event_sink import enable_event_sink
        enable_event_sink(get_data_dir(portable=portable))
    except Exception as e:      # noqa: BLE001 —— 观测增强，失败不得拖垮入口
        try:
            from gcm.app.applog import warn as _warn
            _warn(f"事件落盘挂载失败（不影响启动与克隆）：{e}")
        except Exception:       # 日志系统自身不可用 → 只能放弃提示，但显式返回
            return


def _init_applog(portable: bool = False) -> None:
    """S-01：进程默认日志路径在唯一入口挂载（GUI / CLI / --serve 三形态都覆盖）。

    全库 28 处 `applog.info/warn/error(...)` 都不传路径 —— handler 挂载权此前
    交给「调用方传路径」，于是 app.log 从未生成、留痕全进黑洞。必须早于
    Database/SettingsStore 构造，否则开库期的日志同样丢。
    """
    try:
        from gcm.app.applog import set_default_log_path
        set_default_log_path(get_data_dir(portable=portable) / "app.log")
    except Exception as e:      # noqa: BLE001 —— 观测增强，失败不得拖垮入口
        try:
            import sys as _sys
            print(f"[applog] 日志初始化失败（不影响启动与克隆）：{e}", file=_sys.stderr)
        except Exception:
            return


def main(argv=None) -> int:
    flags = _parse_flags(argv)

    # G66-8：帮助优先于一切分支（含 --cli 之外的裸 `python -m gcm --help`）
    if flags["help"]:
        from gcm.cli import _print_help
        return _print_help()

    # G84-5：事件落盘在**唯一入口**挂载，三种形态（GUI / --cli / --cli --serve）都覆盖
    _enable_event_sink(portable=flags["portable"])
    # S-01：app.log 默认路径同样在唯一入口挂载（必须早于任何 Database/开库动作）
    _init_applog(portable=flags["portable"])

    # G49-3：CLI 批量克隆模式（离屏，不创建 GUI 窗口）
    if flags["cli"]:
        from gcm.cli import main as cli_main
        raw = list(sys.argv[1:] if argv is None else argv)
        return cli_main(_strip_global_flags(raw))

    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication

    data_dir = get_data_dir(portable=flags["portable"])
    # G06-2 single-instance：第二个实例被拦截并聚焦首个窗口
    from gcm.app.single_instance import SingleInstanceLock
    lock = SingleInstanceLock(data_dir / ".instance.lock")
    if not lock.try_acquire():
        print("Git-clone-Max 已在运行，本实例将退出。")
        return 0

    # G06-3 全局异常兜底：未捕获异常写 data/error.log
    from gcm.app.exc_dump import install_excepthook
    install_excepthook(data_dir / "error.log")

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setApplicationName("Git-clone-Max")

    from gcm.db.repo_db import Database
    from gcm.db.settings import SettingsStore
    from gcm.ui.main_window import MainWindow

    db = Database(data_dir / "repos.db")
    settings = SettingsStore(data_dir / "settings.json")
    # G49-1：按 settings.language 初始化 i18n（设置页切换后重启生效）
    # 注意：`load()` 必须在这一步就发生 —— 下面的"凭据解不开"告警依赖它记账
    # （审查发现：先问 `undecryptable_fields()` 再 `load()`，恒为空 → 永不告警）。
    try:
        from gcm.i18n import set_language
        lang = str(getattr(settings.load(), "language", "zh") or "zh")
        set_language(lang)
        # 启动后如实告知"有凭据解不开"（换机器/DPAPI 后端缺失）：用户需要知道
        # 自己要重填哪个字段，而不是等到某个私有仓克隆失败才猜。
        unreadable = settings.undecryptable_fields()
        if unreadable:
            from gcm.app.applog import warn as _warn
            _warn("以下设置项的密文无法解密，已按原值保留（不会覆盖）："
                  + "、".join(unreadable))
    except Exception:
        pass

    # G49-6：--minimized 启动时若托盘可用则隐入托盘（不 show 主窗）
    win = MainWindow(data_dir=data_dir, db=db, settings=settings,
                     start_hidden=bool(flags["minimized"]))
    if not win.should_auto_hide():
        win.show()
        # G52-2：真实启动且窗口可见 → 调度续跑检测（测试/隐藏启动不弹）
        try:
            win._schedule_resume_check()
        except Exception:
            pass
    try:
        return app.exec()
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
