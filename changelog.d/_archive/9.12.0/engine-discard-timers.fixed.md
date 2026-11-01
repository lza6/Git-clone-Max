# 替换引擎时不再留下跑着的进度心跳与卡死巡检
discard() 此前只停 _retry_timer；_progress_timer（100ms 心跳）与 _stuck_timer（30s 巡检）继续跑 —— 每次 _launch 替换引擎都留一对活定时器：旧引擎的过期进度会被 emit 给已换过引擎的 UI，巡检还持续往 app.log 写「疑似卡死」。
@verify tests/test_c1_wiring.TestDiscardStopsAllTimers（带 QApplication 真实断言三个定时器）
