# 动效微反馈档 FAST_MS 落地真实消费点
事件流面板复制成功后按钮文字以 120ms 微反馈回弹（reduced-motion/offscreen 降级为 QTimer 恢复）；顺手清掉 halfclone_actions 的重复导入（ruff F811）。
@verify tests.test_rev14_feedback 22 例绿 + g71 54 例回归绿；ruff/mypy/doc_drift/i18n 全过
