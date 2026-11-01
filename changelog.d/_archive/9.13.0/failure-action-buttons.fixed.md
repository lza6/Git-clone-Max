# 失败汇总不再只会「重试」：按错误类别给真能解决问题的按钮
原来每一类错误都只有一个「重试该类别」按钮 —— 对**认证失败 / 仓库已删除 / 磁盘满**这三类，重试是无效动作（retryable=False，点了只是让失败再发生一遍）。现在 errors.classify_error 产出 action_kind（retry / open_settings / switch_mirror / copy_diag / none），失败面板据此渲染：网络→重试、认证→去设置（真跳到网络分组）、未分类→诊断包、仓库不存在→占位「—」。
@verify tests/test_c2_ux.TestFailureActionKind（5）+ TestFailurePanelRendersAction（2）：每类都映射到白名单值 / 401 给 open_settings / 404 给 none / 三个按钮各自发出正确信号
