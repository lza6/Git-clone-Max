# 执行前预览 + MCP 异步作业（可取消）
新增 app/batch_plan.py：本地预检输出 将克隆/将更新/目录被占/重复/无效 五档，纯函数零网络零副作用；CLI 加 --dry-run（文本与 --json 两形态），MCP clone_batch 加 dry_run/async 参数。MCP 从同步阻塞（最长 1 小时不响应）改为后台作业：job_status 查询、job_cancel 杀整棵进程树，进度通知仅在客户端给 progressToken 时发。下载页新增「导入清单…」：从 README/awesome 清单提取地址去重后填入（纯本地解析，无效链接回显条数不静默丢弃）。
@verify tests/test_g84_batch_plan（11 例）+ test_g84_mcp_jobs（12 例）+ test_g84_readme_import（10 例）；真机 E2E：CLI --dry-run exit 0 且不写盘；MCP 异步克隆 octocat/Hello-World 6.6s 完成 exit 0、产物落盘、全程通道可响应
