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

## G71 新增审查项
- [ ] 可重试错误变成永久失败（404/401/403/磁盘满）时，旧重试条目是否被摘除？（否则 15s 无限重投）
- [ ] 批次状态机 key 是否用 `folder_name`（含 @tag），而不是 `owner/repo`？
- [ ] 诊断包是否显式 `redact(userprofile=True)`（本机用户目录）？manifest extra 是否递归打码？
- [ ] SSE/长连接：客户端断开是否被 `select+MSG_PEEK` 或写失败清理？是否会空转？
- [ ] 事件总线发布是否永不阻塞/抛出？进度事件是否与错误事件分开缓冲？
- [ ] GUI 线程是否执行了 `server.shutdown()`/`join()` 等阻塞操作？
- [ ] 自动重试关闭时，重试面板是否还会报“已请求重试”的假成功？

## G72 新增审查项
- [ ] 日志是否经 applog（Formatter 打码）？有没有直接写 app.log？
- [ ] 原子写是否 mkstemp O_EXCL？replace 重试是否只覆盖瞬时 winerror？
- [ ] 诊断包 report 是否有上限、manifest 是否含截断/时区/脱敏版本？
- [ ] SSE 是否处理 Last-Event-ID、并发上限、写超时？
- [ ] 重试 jitter 是否可注入？单 tick 是否有批量上限？
- [ ] 慢订阅者指标是否可按订阅者观测（dropped/qsize）？

## G73 新增审查项
- [ ] dropped 记录是否保留 url/folder/ref/local_path（@tag 不丢）？
- [ ] 重新入队是否重置 attempts 并立即到期？
- [ ] 是否有批量 redrive 造成重试风暴的风险？（本项目刻意不提供）
