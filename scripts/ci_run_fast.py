"""G59-5 CI 快集运行器：运行非慢集测试（全量 - 慢集黑名单）。

慢集清单与 docs/慢测定位.md 一致（真实 git E2E，默认全量 ~14.7min）。
快集用于主矩阵 coverage 门禁，慢集由 CI 独立 slow job 承接。

用法：python scripts/ci_run_fast.py [--coverage]

G69-18 加固（本轮）：
  1. **失败必须可诊断**。原实现直接 `TextTestRunner().run()`，一旦进程崩溃
     （SIGSEGV / 0xC0000409）或 runner 异常，**不会打印任何失败用例名**，
     只留下一个 dot 流和退出码 —— 这正是该 job 连续 80+ 次红灯却无人能定位的根因。
     现改为逐模块运行 + 每个模块独立打印结果摘要 + 顶层 try/except + `atexit`
     兜底，保证「无论怎么死，都能从日志看出死在哪个模块」。
  2. **硬件相关的绝对阈值断言不进正确性门禁**。`test_scanner_perf`（绝对 30s）
     与 `test_mem_baseline`（真实跑引擎测内存）依赖 runner 机器性能，
     放在覆盖率门禁里会造成「本机绿、CI 红」的不可复现红灯。
     移入独立 job（CI 的 `fast-smoke` / `slow` 已承接同类语义），
     由 `HARDWARE_SENSITIVE` 清单显式排除 —— 排除的是**跑法**，不是削弱断言。
"""
from __future__ import annotations

import atexit
import faulthandler
import gc
import os
import sys
import unittest
from pathlib import Path

SLOW = {
    "test_engine", "test_v7_fixes", "test_core", "test_extra", "test_v3_p0",
    "test_submodule", "test_publish", "test_scanner", "test_tag_branch",
    "test_progress_search",
}

# 依赖 runner 机器性能/时序的**单个用例**：其断言本身没错，但不该进
# 「正确性 + 覆盖率」门禁 —— 门禁要判定的是代码对不对，不是 CI 机器有多快。
# 这些用例由独立 job 承接（见 .github/workflows/ci.yml 的 perf-baseline job）。
#
# ⚠️ G69-18 更正：早期版本按**整模块**排除这两个文件，导致
#   `test_scanner_perf` / `test_mem_baseline` 里另外 **13 条与硬件无关的正确性用例**
#   在 Windows/macOS 主矩阵里被静默跳过，覆盖率也从 81.26% 掉到 80.76%
#   （只剩 0.24pp 余量）。现在改为：**模块照跑**，只让那两个用例自己跳过 ——
#   由 `GCM_FAST_SUITE=1` 驱动（见下方 main() 设置）。
HARDWARE_SENSITIVE = {
    "test_scanner_perf",     # 其中 test_cancelled_stops_early 含绝对时间断言
    "test_mem_baseline",     # 其中 test_measure_returns_fields 真实跑引擎测内存
}

ROOT = Path(__file__).resolve().parent.parent

# 崩溃诊断：记录「当前正在跑哪个模块」，atexit 时打印。
_current: list[str] = ["<未开始>"]


def _dump_inflight() -> None:
    """进程异常退出（含崩溃）时的兜底留痕，避免只留下一个 dot 流。"""
    print(f"\n[ci_run_fast] 进程退出，最后进入的模块：{_current[0]}", flush=True)


def _force_utf8_stdout() -> None:
    """让本脚本的输出**永不因编码而崩**。

    G69-18：Windows runner 的控制台代码页是 cp1252，而本脚本会打印中文进度。
    不处理的话 `print` 直接抛 `UnicodeEncodeError` —— 一个诊断脚本自己
    把 CI 弄红，把「可诊断」变成「新的红灯」。GitHub 日志本身是 UTF-8，
    所以这里显式改用 UTF-8，并对无法编码的字符降级替换而不是抛异常。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    _force_utf8_stdout()
    sys.path.insert(0, str(ROOT))
    # G69-18：**原生崩溃**（SIGSEGV / 0xC0000409）不会执行 atexit、也不会抛
    # Python 异常 —— 此时唯一的线索是 faulthandler 在信号处理里打出的 C 级栈。
    # 本轮正是靠它把「CI 连续 80+ 次红灯、无人能定位」变成可定位。
    # 三平台通用；写 stderr 以免污染 stdout 的机器可读输出。
    try:
        faulthandler.enable()
    except Exception:
        pass
    atexit.register(_dump_inflight)

    tests_dir = ROOT / "tests"
    # G69-18：让硬件敏感用例**自己**跳过（它们用 `GCM_FAST_SUITE=1` 判定），
    # 而不是整模块排除 —— 后者会连带砍掉同文件里与硬件无关的正确性用例，
    # 并静默缩小跨平台测试面与覆盖率。
    os.environ["GCM_FAST_SUITE"] = "1"
    stems = sorted(p.stem for p in tests_dir.glob("test_*.py")
                   if p.stem not in SLOW)
    hw_modules = sorted(p.stem for p in tests_dir.glob("test_*.py")
                        if p.stem in HARDWARE_SENSITIVE)
    print(f"[ci_run_fast] 快集模块 {len(stems)} 个；"
          f"其中 {len(hw_modules)} 个模块只跳过其硬件敏感用例：{hw_modules}", flush=True)

    loader = unittest.TestLoader()
    failed_modules: list[str] = []

    for stem in stems:
        _current[0] = stem
        # 进入每个模块**前**就落一行并强制 flush —— 原生崩溃时 atexit 不执行，
        # 这行是「死在哪个模块」的唯一可靠线索。
        print(f"[ci_run_fast] >>> {stem}", flush=True)
        try:
            _run_one(stem, loader, failed_modules)
        except Exception as exc:  # noqa: BLE001
            # runner 自身炸了（例如测试里 os._exit / 解释器状态损坏）
            print(f"[ci_run_fast] !! run {stem} 异常: {exc!r}", flush=True)
            failed_modules.append(stem)
        # 注意：这里**不做** gc.collect() —— 在模块边界销毁控件后，
        # 下一个模块的 processEvents() 会撞上悬垂的 Qt 对象（实测仍崩）。
        # 循环 GC 整轮关闭，只在全部跑完后回收一次（见下）。

    # 全部模块跑完：此刻已无后续 processEvents，回收是安全的；
    # 主要是为了给进程退出前的内存一个收口（不依赖它来防崩）。
    try:
        gc.enable()
        gc.collect()
    except Exception:
        pass

    print(f"\n[ci_run_fast] 合计：Ran {_totals['run']} tests · "
          f"failures={_totals['fail']} errors={_totals['err']} "
          f"skipped={_totals['skip']}", flush=True)
    if failed_modules:
        print(f"[ci_run_fast] 失败模块：{sorted(set(failed_modules))}", flush=True)
        return 1
    _current[0] = "<全部完成>"
    print("[ci_run_fast] OK", flush=True)
    return 0


_totals = {"run": 0, "fail": 0, "err": 0, "skip": 0}


def _run_one(stem: str, loader, failed_modules: list[str]) -> None:
    """跑单个模块。

    ⚠️ 关于原生崩溃（G69-18）——**当前采用「整轮关闭循环 GC」**，理由是实测：

    | 配置 | 结果 |
    |------|------|
    | 不做任何处理 | CI 稳定段错误（`exit 139`），本机约 2/3 概率复现 |
    | 模块之间 `gc.collect()` | 仍崩（在模块边界销毁控件，下一模块 processEvents 时炸） |
    | 每个用例后 close+deleteLater 全部顶层控件 | **更早崩**（`test_g52_resume_dialog` 就炸） |
    | **整轮 `gc.disable()`，结束时不回收** | **Ran 1066 · 0 崩溃（唯一一次全绿）** |

    机制（实测证据，非推测）：`MainWindow` 等控件由**引用环**维持 ——
    `del w` 之后**不被引用计数回收**，只有 `gc.collect()` 才回收（一次 369 个对象）。
    于是销毁时机落到自动循环 GC 上，而它在**任意分配点**触发 —— 包括
    `QApplication.processEvents()` 内部。Qt 在事件分发中途销毁控件与其子定时器，
    随后 `QTimerInfoList::activateTimers` 解引用已释放对象 → 原生崩溃。

    这与全部观测吻合：崩溃点随机漂移、`PYTHONMALLOC=debug` 会改变结果、测试越多越易崩。

    代价：整轮不回收循环垃圾，内存会高于常态（测试进程，可接受）。
    根治方案是让每个用例确定性收尾，但那需要逐个改造 31 个构造 MainWindow 的测试文件
    —— 已登记为后续工作，不用「跳过/重试」掩盖。
    """
    gc.disable()
    try:
        mod = __import__(f"tests.{stem}", fromlist=["*"])
    except Exception as exc:  # noqa: BLE001
        # 导入失败本身就是红灯，且必须点名——否则只会看到「0 test」。
        print(f"[ci_run_fast] !! import {stem} 失败: {exc!r}", flush=True)
        failed_modules.append(stem)
        _totals["err"] += 1
        return

    suite = loader.loadTestsFromModule(mod)
    result = unittest.TextTestRunner(
        verbosity=1, buffer=False).run(suite)

    _totals["run"] += result.testsRun
    _totals["fail"] += len(result.failures)
    _totals["err"] += len(result.errors)
    _totals["skip"] += len(result.skipped)
    if not result.wasSuccessful():
        failed_modules.append(stem)
        # 逐条点名，确保失败用例名一定出现在 CI 日志里。
        for case, _tb in list(result.failures) + list(result.errors):
            print(f"[ci_run_fast] FAIL {case.id()}", flush=True)

if __name__ == "__main__":
    _force_utf8_stdout()          # 兜底：顶层异常消息也必须是可打印的
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        # 别把 Ctrl-C 报成「未捕获异常」并吞掉退出码语义（130 = SIGINT）
        raise SystemExit(130) from None
    except BaseException as exc:  # noqa: BLE001
        print(f"[ci_run_fast] 顶层未捕获异常: {exc!r}", flush=True)
        raise SystemExit(1) from exc
