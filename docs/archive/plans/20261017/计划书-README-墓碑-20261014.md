本目录已清空（2026-10-14 · v9.2.0）。

原计划 / 台账类文档（下一步改进指南、验收清单、project_specs、db_structure、
规划总览、参考结果计划指南、跨端响应式优化计划、v8 历史、扫描证据 zip）
在 G67 台账全部闭环并经用户确认后删除。

它们承载的真实现状已迁移到（避免死链）：

    docs/data-model.md          数据模型 / 迁移链 / 跨进程锁（原 db_structure.md）
    docs/architecture.md        分层架构、ADR、模块规模基线
    docs/code-gems.md           可复用代码范式
    docs/product-brief.md       产品定位与增长（含各方向现状校准）
    docs/慢测定位.md             慢集清单与耗时基线
    docs/验证记录-*.md           历次验证证据（命令 + 真实输出摘要）
    docs/reports/               历次审计报告（HTML 只读快照）
    docs/RELEASE_CHECKLIST.md   发布流程

迭代历史与逐版本交付记录：`gcm/__init__.py` 的 `__changelog__`。
未修的已知边界与技术债：仓库根 `CLAUDE.md` §6。
