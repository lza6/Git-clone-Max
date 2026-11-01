# 运行时依赖与 CI 静态检查器全部锁版本
requirements.txt 由 PyQt6>=6.6 改为 PyQt6==6.11.0（与 CI 单一真相）；CI 的 Lint/Type 步骤不再裸装 ruff/mypy，改读 requirements-build.txt —— 此前「文件里有锁」被误当成「CI 已锁」，CI 绿了本地可能红。
@verify tests/test_c1_wiring.TestDependencyPinning（4 例：两文件精确锁、CI 不裸装、PyQt6 单源）
