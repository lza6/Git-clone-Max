# 可用性专项（Rev12·尼尔森十原则）：撤销/重做基建 + 菜单栏 + 帮助随时可得
@verify `unittest tests.test_rev12_usability` 28 用例 OK（含真 SQLite 删→撤销→字段比对、20 线程并发写不丢、落盘打码断言）；`test_g69_regressions` 引用环 OK；`ruff check gcm` / `mypy gcm` 绿；`doc_drift_check.py` DRIFT 0；截图 reports/ui-rev12
