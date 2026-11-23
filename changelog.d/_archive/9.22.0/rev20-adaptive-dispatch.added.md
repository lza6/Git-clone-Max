# 让感知调度在投放链路真实生效：退避拦截、慢 worker 事件、批次终态原因
R20-1: 同 host 退避窗口内跳过新投放，保留待发队列，其他 host 照常投放
R20-2: 慢 worker 判定发 task_slow 事件并写日志
R20-3: BATCH_FINISHED 负载新增 stop_reason
@verify 快集 2378 例 0 失败 / 覆盖率 81% / ruff + mypy + i18n + DRIFT + CONTRACT 全绿
