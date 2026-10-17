# 代码审查清单（自审 + 独立审查）

## 心态
有罪推定：在代码证明没问题之前，假定它有问题。评估成品，不评估意图。

## 必查（本项目高危项）
| 类别 | 检查点 |
|------|--------|
| **Qt 生命周期** | 信号是否用 `lambda` 捕获 self？绑定方法是否被挂成控件属性？模态是否过 `_can_show_modal()`？QTimer 是否挂在被销毁对象上？ |
| **线程** | 工作线程是否只 `emit`、槽回主线程？是否直接碰 QWidget / QAbstractItemModel？ |
| **并发** | 「先查后写」的临界区是否完整？锁内是否做了慢 I/O？失败是否回滚占位？ |
| **异常** | 是否有裸 `except`？`except Exception` 是否静默吞（必须记日志或给可操作提示）？ |
| **编码** | `subprocess` 是否显式 `encoding`？git 用 utf-8、Windows 控制台工具用 `_console_encoding()`；stdio 服务是否 UTF-8？ |
| **数据目录** | 是否走 `get_data_dir()`？ |
| **凭据** | 出站文本过 `redact`？落盘是否加密？审计是否只记 `scheme://host`？ |
| **SQL** | 是否参数化？是否有无 LIMIT 查询？ |
| **虚伪实现** | 新增模块/函数/设置项是否**有生产调用点或 UI 入口**？只写不读 / 只读不写？ |
| **死代码** | 未使用的字段/函数/导入？注释掉的代码？ |
| **测试** | 空断言？绝对日期？依赖执行顺序？自己污染全局状态（env/线程/窗口）？ |

## 输出格式
```
## Summary
## Critical Issues (Blocking)   [file:line + 为什么阻塞 + 复现]
## Required Changes
## Suggestions
## Verdict  Request Changes | Needs Discussion | Approve
```
Approve = 严格审查后无阻塞问题，不是「完美」。不得为逃避 Approve 编造问题。

## 循环
审查 → 修复 → 复验，最多 3 轮；无实质进展则记录阻塞。

## 本项目已确认的失效模式（别重犯）
1. 守卫覆盖「方便测试的路径」而非生产路径（不 `show()` 的窗口）。
2. 静态守卫被自己的 changelog 喂成假绿（文本匹配）。
3. 「改了但没生效」：调色板键名猜错（`dim` vs `text_dim`）。
4. `hasattr` 把拼错的方法名静默降级成功能缺失（`count_repos`）。
