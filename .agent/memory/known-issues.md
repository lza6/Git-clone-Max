# 已知问题与边界（known-issues）

> 只记录**真实存在**且尚未解决的问题。修掉一个就删一条。
> 最后更新：2026-10-17（计划文档二次审计后）

## 未闭环功能

| 项 | 现状 | 影响 | 建议 |
|----|------|------|------|
| `G53-1` 雷达图 / `health_card` 列 | 统计中心有**派生视图**（6 维），但未落 `health_card` 列、无雷达图 | 用户能看到评分，但拿不到可查询的历史列 | 若要落列，需 DB 迁移 + 回填；先确认产品是否需要（T3） |
| `G58-2` 镜像测速择优 | 未实现（只有静态「镜像前缀」配置） | 用户需自己选镜像 | 可做：对 `mirror_prefix` 里的候选做 HEAD 测速排序 |

## 已撤销 / 不适用（不是缺陷，别再当 todo）

| 项 | 结论 | 依据 |
|----|------|------|
| `G58-1` 统一限流 | **不适用**：模块在 v9.2.0 因零生产调用点被删除；git 侧限速由 `settings.rate_limit_kbps` → `http.lowSpeedLimit` 承担 | `gcm/__init__.py` changelog 9.2.0；`docs/perf-observability.md` §3 |
| `G58-3` 代理凭据 | **已闭环**：`proxy` 已加密落盘（`_encrypt_token` / `_decrypt_or_keep`）；完整代理 URL 直接注入 `http_proxy`，git 原生支持 `user:pass@host` | `gcm/db/settings.py:366,407-409`；`gcm/git/service.py:492-494` |
| `G54-4` 引导清单 | **已闭环**（v9.4.3） | `gcm/ui/checklist.py`；`tests/test_g69_checklist.py` |

## 已知不稳定

| 项 | 现象 | 频率 | 说明 |
|----|------|------|------|
| `test_g67_closure.TestShutdownClearsInProgress` | 高负载下偶发失败 | 3 次全量里约 1 次 | 子进程 + 立刻 shutdown 的竞态；单独跑稳定 |
| `test_single_instance.TestSingleInstanceCrossProcess` | 同上 | 同上 | 跨进程锁 + 端口时序 |
| `test_g66_patch` 的 schtasks 用例 | 已改为与语言无关判据，但**外部工具行为**仍受环境影响 | 低 | 只断言「无 U+FFFD + 可无损回编码」 |

## 环境边界

- **CI 主矩阵用 `pip install PyQt6`（未锁版本）**；`requirements-build.txt` 锁了 ruff/mypy/coverage/pyinstaller。上游发新版会让 CI 行为漂移。
- **CI 只在 Linux 跑慢集**；Windows 特有的编码/路径问题必须本机验证。
- **打包构建未纳入 CI**：exe 需人工重建 + `health_check.py` 验证。

## 历史遗留（不建议动）

- 老 tag（`v1.0.1` / `v1.1.0` / `v2.0.1` / `v2.0.2` / `v4.3.1`）指向的提交 `__version__` 落后一个 patch。改历史 tag 风险大于收益。
