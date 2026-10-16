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
import sys
import unittest
from pathlib import Path

SLOW = {
    "test_engine", "test_v7_fixes", "test_core", "test_extra", "test_v3_p0",
    "test_submodule", "test_publish", "test_scanner", "test_tag_branch",
    "test_progress_search",
}

# 依赖 runner 机器性能/时序的测试：其断言本身没错，但不该进「正确性 + 覆盖率」
# 门禁 —— 门禁要判定的是代码对不对，不是 CI 机器有多快。
# 这些模块由独立 job 承接（见 .github/workflows/ci.yml 的 perf-baseline job）。
HARDWARE_SENSITIVE = {
    "test_scanner_perf",     # 含绝对时间断言 assertLess(elapsed, 30)
    "test_mem_baseline",     # 真实构造引擎跑 100 仓库测 tracemalloc 峰值
}

ROOT = Path(__file__).resolve().parent.parent

# 崩溃诊断：记录「当前正在跑哪个模块」，atexit 时打印。
_current: list[str] = ["<未开始>"]


def _dump_inflight() -> None:
    """进程异常退出（含崩溃）时的兜底留痕，避免只留下一个 dot 流。"""
    print(f"\n[ci_run_fast] 进程退出，最后进入的模块：{_current[0]}", flush=True)


def _load(stem: str):
    return __import__(f"tests.{stem}", fromlist=["*"])


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
    stems = sorted(p.stem for p in tests_dir.glob("test_*.py")
                   if p.stem not in SLOW and p.stem not in HARDWARE_SENSITIVE)
    excluded = sorted(p.stem for p in tests_dir.glob("test_*.py")
                      if p.stem in HARDWARE_SENSITIVE)
    print(f"[ci_run_fast] 快集模块 {len(stems)} 个；"
          f"硬件敏感（另由独立 job 承接）{len(excluded)} 个：{excluded}", flush=True)

    loader = unittest.TestLoader()
    failed_modules: list[str] = []
    total_run = total_fail = total_err = total_skip = 0

    for stem in stems:
        _current[0] = stem
        # 进入每个模块**前**就落一行并强制 flush —— 原生崩溃时 atexit 不执行，
        # 这行是「死在哪个模块」的唯一可靠线索。
        print(f"[ci_run_fast] >>> {stem}", flush=True)
        try:
            mod = _load(stem)
        except Exception as exc:  # noqa: BLE001
            # 导入失败本身就是红灯，且必须点名——否则只会看到「0 test」。
            print(f"[ci_run_fast] !! import {stem} 失败: {exc!r}", flush=True)
            failed_modules.append(stem)
            total_err += 1
            continue

        suite = loader.loadTestsFromModule(mod)
        runner = unittest.TextTestRunner(verbosity=1, buffer=False)
        try:
            result = runner.run(suite)
        except Exception as exc:  # noqa: BLE001
            # runner 自身炸了（例如测试里 os._exit / 解释器状态损坏）
            print(f"[ci_run_fast] !! run {stem} 异常: {exc!r}", flush=True)
            failed_modules.append(stem)
            total_err += 1
            continue

        total_run += result.testsRun
        total_fail += len(result.failures)
        total_err += len(result.errors)
        total_skip += len(result.skipped)
        if not result.wasSuccessful():
            failed_modules.append(stem)
            # 逐条点名，确保失败用例名一定出现在 CI 日志里。
            for case, _tb in list(result.failures) + list(result.errors):
                print(f"[ci_run_fast] FAIL {case.id()}", flush=True)

    print(f"\n[ci_run_fast] 合计：Ran {total_run} tests · "
          f"failures={total_fail} errors={total_err} skipped={total_skip}", flush=True)
    if failed_modules:
        print(f"[ci_run_fast] 失败模块：{sorted(set(failed_modules))}", flush=True)
        return 1
    _current[0] = "<全部完成>"
    print("[ci_run_fast] OK", flush=True)
    return 0


if __name__ == "__main__":
    _force_utf8_stdout()          # 兜底：顶层异常消息也必须是可打印的
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001
        print(f"[ci_run_fast] 顶层未捕获异常: {exc!r}", flush=True)
        raise SystemExit(1)
