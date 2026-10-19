# 新增功能（含 UI）

## 适用场景
加一个用户可见的功能。

## 前置读取
`.agent/memory/decisions.md`（为什么这样做）· `change-index.md` · `docs/architecture.md` · `docs/code-gems.md`

## 步骤
1. **先确认它真的不存在**：`grep -rn "<关键名>" gcm/ tests/`。
   只在 tests 里出现 = 虚伪实现。
2. **定层级**：纯逻辑落 `gcm/app/`，持久化落 `gcm/db/`，UI 落 `gcm/ui/`（**新逻辑独立模块**）。
3. **接线两侧**（设置类功能）：
   - 写入侧：UI 控件 + `connect` 到处理函数；
   - 消费侧：真的有模块读它并改变行为。
4. **信号连接用绑定方法，不要 `lambda` 捕获 self**（会成 Qt 引用环 → 段错误）。
5. **模态弹窗必须过 `_can_show_modal()`**（不可见窗口 `exec()` 会永久阻塞）。
6. **i18n**：新文案走 `tr()`，键同时进 `zh.py` / `en.py`。
7. 测试：真实构造对象断言行为（不是只 grep 源码）；补永久守卫。

## 验收
```bash
python -m unittest <对应测试>
python -m unittest tests.test_g69_regressions      # 接线/生命周期守卫
python -m ruff check gcm && python -m mypy gcm
```
- 真实构造一次 UI，断言控件存在 + 操作后**状态真的变了**（不是只有字段赋值）。

## 常见坑
- 功能写了但没入口 / 入口存在但后端没消费 → 见 `decisions.md` D-04。
- 设置项只写不读或只读不写 → 有守卫 `test_settings_ui_has_entry_for_previously_unreachable_fields`。
- `hasattr(obj, "方法名")` 把拼错静默降级成「功能不存在」（`count_repos` 事件）。

## G71 参考实现（可观测性 / 持久化）
- 任何长流程/状态变化：发布 `EventBus` 事件（`app/event_bus.py`），不要只加 UI 信号。
- 任何 JSON 持久化：走 `util/atomic_io.write_text_atomic`（唯一 tmp + fsync + 退避）。
- 错误：用 `app/errors.classify_error` 判 `retryable`，不要在 UI 再写关键字表。
- 验收：功能定向测试 + `tests.test_g71_event_stream`（事件/SSE/原子写/诊断包/状态机）。
