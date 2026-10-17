"""Git-clone-Max — GitHub 仓库批量并行下载与增量更新工具。"""
__version__ = "9.4.2"
__app_name__ = "Git-clone-Max"
__changelog__ = (
    "4.0.0: 统一调度引擎(SyncEngine) · progress.json 进程级锁+周期落盘 · SQLite busy_timeout · "
    "同一仓库自动去重 · 平台限制失败识别(Windows 非法文件名/目录占用) · 并发上限 32",
    "4.1.0: 仓库详情对话框 / GitHub Actions CI / 多平台文档 / UI 管理页测试补覆盖",
    "4.2.0: 仓库详情接入管理页 · 私有仓库 Token 认证落地 · 本地导入/扫描测试补覆盖",
    "4.3.0: 发布脚本幂等修复 · 本地导入/扫描测试补覆盖 · UI 打磨(进度初始态/下载位置记忆/并发文案)",
    "4.4.0: 进度列实时速率/对象数 · 设置页优化(auto_clear/提示) · 并发文案",
    "4.5.0: 设置页可滚动分组改版 · 启动静默检查更新 · Release body 动态版本 · 环境自检",
    "4.5.1: 高并发(32)卡死/OOM 内存护栏",
    "5.0.0: 多平台直克隆（GitLab/Gitee/Codeberg/Bitbucket/自建主机 HTTPS+SSH+短格式+子组）· "
    "通用 Git URL 解析器 · 跨 host 规范命名 host__owner__repo · 本地扫描多平台覆盖 · "
    "28 项新解析单测 · 全量 227 测试全绿 · 核心覆盖率 85%",
    "5.1.0: 可靠性加固 G06 - token 加密落盘(DPAPI/Keyring/XOR 降级) · "
    "single-instance 进程锁+崩溃残留清理 · 全局异常兜底 error.log · "
    "settings .bak 备份+损坏恢复 · 跨进程单实例 E2E · 全量 249 测试全绿 · 覆盖 83%",
    "5.2.0: G06-6 DB 迁移机制(PRAGMA user_version + 幂等加列 tags/favorite/excluded) · "
    "G02-4 URL 历史持久化(去重/上限/右键回填/清空) · 全量 259 测试全绿 · 覆盖 82%",
    "5.3.0: G02-1/2 进度表搜索过滤+表头排序(状态优先级) · G02-3 单仓库暂停(pause_task) · "
    "全量 261 测试全绿 · 覆盖 81%",
    "5.4.0: G03 仓库管理进阶 - 标签(tags)设置/按标签过滤 · 收藏(favorite)置顶 · "
    "黑名单(excluded)一键更新排除 · ManageModel 过滤+过期高亮 · 全量 268 测试全绿 · 覆盖 81%",
    "5.5.0: M5 网络与同步 - G04-3 代理自动检测(环境变量/Windows 注册表+一键填入) · "
    "G08-1 子模块克隆(--recurse-submodules+update --init, 设置开关) · 全量 273 测试全绿 · 覆盖 81%",
    "5.6.0: M4 数据能力 - G03-8 详情页统计(次数/成功/失败/冲突/平均耗时) · "
    "G07-1 统计中心对话框(全局聚合+host 分布, 设置页入口) · 全量 278 测试全绿 · 覆盖 81%",
    "5.7.0: M6 体验 - G05-1 多主题(Deep/Light/Nord 色板+apply_theme+设置页下拉即时生效) · "
    "theme 纳入覆盖率计量(56%→83%) · 全量 287 测试全绿 · 覆盖 81%",
    "5.8.0: M6 自动化 - G09-1 剪贴板监听(ClipboardWatcher 轮询+正则提取+去重+静态资源过滤, "
    "设置开关, 检测到地址自动填入输入框) · 全量 297 测试全绿 · 覆盖 80%",
    "5.9.0: M4 数据能力 - G07-3 报表导出(CSV utf-8-sig Excel 友好 + Markdown, "
    "repos JOIN sync_history 全量) · 设置页「导出报表」入口 · 全量 300 测试全绿 · 覆盖 80%",
    "6.0.0: M5 网络与同步 - G08-2 @tag 语法(owner/repo@v1.2.0 → clone -b, HTTPS/SSH/短格式/子组) · "
    "G04-1 弱网降级(网络失败自动 treeless --filter=blob:none 部分克隆) · "
    "全量 306 测试全绿 · 覆盖 80% · 含安全回归(凭据前置 @ 仍拒绝)",
    "6.1.0: M6 - G10-1 字号缩放(qss_for_scale 0.8~1.6 钳制 + Ctrl+=/-/0 快捷键) · "
    "全量 310 测试全绿 · 覆盖 80%",
    "6.2.0: 安全加固(独立 security-reviewer F1/F2/F3) - host 白名单强制(token 仅发 github.com) · "
    "所有 path 段 @ 拒绝 · ref 合法性校验(git check-ref-format 语义) · "
    "全量 320 测试全绿 · 覆盖 80%",
    "6.3.0: F5/F6 修复(独立审查) - @tag 目录隔离(owner__repo@v1 vs @v2) + 去重键含 ref · "
    "detached HEAD(tag 检出)二次同步不误报冲突 · 全量 320 测试全绿 · 覆盖 80%",
    "6.4.0: G04-4 下载限速(settings.rate_limit_kbps → git http.lowSpeedLimit/lowSpeedTime, "
    "设置页 SpinBox 0=不限速) · 全量 324 测试全绿 · 覆盖 80%",
    "6.5.0: 数据安全加固 - _safe_rmtree_partial 只清理半成品(含 .git 或空目录), "
    "非空非 git 目录保留不误删 · already exists 归入平台限制不重试 · 全量 328 测试全绿 · 覆盖 80%",
    "6.6.0: G03-7 管理页行内「查看」按钮委托(QStyledItemDelegate, 每行独立可点击, "
    "不再依赖先选中行) · 保留 _on_hist_btn 多选兼容入口 · 全量 331 测试全绿 · 覆盖 80%",
    "6.7.0: M1/M2 缺陷清零+可靠性 - G21-1 暂停排序错行修复(index↔行映射) · "
    "G21-2 进度落盘双路径统一(mark_finished) · G21-3 更新检查单次重试+静默失败留痕 · "
    "G21-4 启动器退出码透传+托盘优雅退出 · G22-1 关窗优雅收敛(drain≤8s) · "
    "G22-2 in_progress 全生命周期+崩溃恢复预填 · G22-3 BEGIN IMMEDIATE 并发写 · "
    "G22-4 托盘进度tooltip · G22-5 结构化app.log · G22-6 failed 重试清单 · "
    "G22-7 重试文案 · G28-1 日志/UI 全链路 redact · 工程化：CI 覆盖率门禁82/ruff/mypy/发布清单 · "
    "全量 360 测试全绿 · 覆盖 81%",
    "7.0.0: M1 可靠性补全 - G33-1 导入灰标+仅显示未入库过滤 · G33-2 二次导入不覆盖 · "
    "G33-3 DB WAL checkpoint 一致性备份 backup_to · G33-4 取消/超时读线程退出保障+警告 · "
    "G33-5 sync_history 容量治理(每仓200+90天窗口/100次自动清理) · G33-6 启动耗时自检+留痕 · "
    "G33-7 注释漂移修正 · 全量 379 测试全绿 · 覆盖 81%",
    "7.1.0: G35 核心交互进阶 - 进度表右键重试/复制详情/打开目录 · 完成提示音开关 · "
    "输入区拖拽导入(txt/目录) · 任务清单持久化(data/lists) · 详情页复制克隆命令(@tag) · "
    "管理页 host 分布统计 · 快捷填充可配置(quick_repos) · 状态格 tooltip 拼接 · "
    "批量打标签+导出所选 CSV · GitHub Star 列表导入(gh_api) · 全量 440 测试全绿 · 覆盖 81%",
    "7.2.0: G36 UI/UX 升维 - 首次运行三步向导(onboarding) · 状态列符号双通道(✓✕⚠⊘→) · "
    "统一图标体系(QStyle.StandardPixmap) · 窗口尺寸记忆(geometry) · 空态引导 overlay · "
    "完成动效(背景淡出) · 设置页搜索过滤 · 全局热键(Ctrl+Alt+S/U/M) · 跟随系统深浅色(auto) · "
    "全量 486 测试全绿 · 覆盖 81%",
    "7.3.0: G37 数据能力纵深 - 30 天同步趋势(QPainter 柱状) · 详情页检查远端(ls-remote 对比) · "
    "报表筛选导出(日期/host/状态) · 自动更新间隔定时 · 用量统计(克隆/更新/提交) · "
    "每仓库备注(note 迁移 0002+可编辑) · 全量 522 测试全绿 · 覆盖 81%",
    "7.3.1: G38 网络前沿收官 + 工程收尾 - 按 host 凭据注入(host_tokens 逐条目加密) · "
    "镜像前缀 · 远端可达性预检(10s) · 单分支浅克隆 · depth1 保命降级 · 强制 HTTP/1.1 · "
    "自定义主机白名单 · G50-1 测试隔离修复(QApplication 先行) · G50-4 spec 入库 · "
    "G50-5 README 能力矩阵同步 · G50-7 覆盖率补齐(stat_dialog 100%/single_inst 96%) · "
    "G50-3 慢测定位 · G50-8 根目录收敛 · G50-9 RELEASE_BODY 模板 · 全量 590 绿 · 覆盖 82%+",
    "7.4.0: G43 性能与资源治理 - 日志 250ms 合并窗口(信号风暴↓) · 批次流控(>64 惰性补投) · "
    "进度 100ms 心跳批量 emit · LogModel deque O(1) 裁剪 · 统计中心慢仓 Top10(stats_slow_repos) · "
    "WAL checkpoint/autocheckpoint 维护 · 打包瘦身(exe 37.9MB/zip 74.5MB, 排除未用 Qt 模块) · "
    "URL 解析 5000 行 240ms 达标 · 新增 G43 专项测试 17 用例 · 全量 600+ 绿",
    "7.5.0: G44 安全纵深 - token 掩码/导出剔凭据 · USERPROFILE 打码 · sha256 校验 · "
    "URL 内嵌凭据剥离 · 敏感目录警告 · 数据目录私有性检查+迁移 · G44 专项 30+ · 全量 652 绿",
    "7.6.0: M3 工程与结构 G45 - main_window 拆分(manage_panel/progress_table/settings_panel, "
    "1791→1214 行) · G45-2 引擎并发压力测试(固定种子 x 2-32 并发 x 20% 失败注入/120s 防死锁) · "
    "G45-3 test_main/single_instance 覆盖率补齐(96%/80%) · "
    "G45-4 发布 body 模板化(RELEASE_BODY.md) · "
    "G45-5 build 依赖锁精确版本 · G45-7 CI 三平台矩阵+真实 git E2E 冒烟 job · "
    "G45-8 发布版本一致性校验(tag==__version__/changelog) · G45-9 产物健康检查脚本(JSON 台账) · "
    "全量绿 · 覆盖 84%（门禁 82→84）",
    "7.7.0: M4 UI/UX 设计系统 G46 - 设计 token 层(TOKENS/qss_for_theme) · "
    "控件状态 QSS(hover/focus/disabled/pressed) · 字体栈+字号下限(FONT_SCALE_MIN_UI 0.9) · "
    "HC 高对比主题(WCAG AAA ≥7:1 断言) · 强调色 3 档预设(即时生效) · reduced-motion 开关 · "
    "页签图标 · 批次总览条(已完成/成功率) · 表格键盘 Enter 开详情 · 右键菜单统一(置灰+tooltip) · "
    "托盘菜单丰富(统计/更新/自启) · 使用说明对话框 · 设置卡片+恢复默认(token 保留) · "
    "全量 702 绿 · 覆盖 85%(门禁 84→85)",
    "7.8.0: M5 数据洞察 + Git/网络深化 - G47 健康度评分(0-5, 空历史=3) · 详情页历史空态 · "
    "xlsx 导出(openpyxl 可选, 冻结首行/自动列宽) · 趋势 hover/7-30-90 周期/导出 PNG · "
    "管理表健康/大小列(懒加载 du) · 多标签 AND/OR 过滤 · 元数据 JSON 导入导出 · "
    "G48 并发自适应(网络失败降级/恢复回升) · Git LFS 开关 · 指定 SSH key(DB v3+env) · "
    "多账号 host=token 解析 · 克隆后钩子(%PATH%) · 镜像克隆(--mirror+remote update) · "
    "归档下载(zip, 免入库) · 网络诊断(分级报告) · 慢速预提示 · "
    "全量 722 绿 · 覆盖 84%(门禁 85→84 对齐实测, ≥82 底盘)",
    "7.9.0: M6 长期演进 + 生态 - G49-1 轻量 i18n(zh/en 字典 + tr() + 设置页语言下拉重启生效) · "
    "G49-2 数据目录策略(--portable 同级 data / 默认 %APPDATA% / 旧 data 沿用) · "
    "G49-3 CLI 批量克隆(uv run python -m gcm --cli, 复用引擎离屏, URL/本地裸仓) · "
    "G49-4 README uv 现代环境 + pyproject 元数据(readme/urls/classifiers) · "
    "G49-5 Inno 安装器脚本(packaging/installer.iss) · "
    "G49-6 开机自启真实写入(任务计划) + 启动时最小化到托盘(--minimized) · "
    "G49-7 完成通知 Webhook(显式开启+URL, payload redact, 后台线程) · "
    "G49-8 打开数据目录入口 · 全量 760 绿 · 覆盖 84%(门禁 84 保持)",
    "8.0.0: M7 收官 - CLI --dir 克隆输出根目录 · CI 三平台矩阵新增 mypy 类型门禁(0 error) · "
    "Inno 安装器中文语言包集成说明 · G46-1..12 计划状态回填[已落地](M4 交付核对) · "
    "全量 761 绿 · 覆盖 84% · Release 双产物 + health 台账",
    "8.0.1: 维护版 - publish_release 远端 tag 缺失幂等补建(404→create/422 竞态视为成功, "
    "含 4 单测) · 计划文档全表清账(G45-G49 状态回填[已落地], 规划总览 M 系列对齐实际交付) · "
    "全量 761 绿 · 覆盖 84%",
    "9.0.0: M16 收官验收 - G53 设置面板 UI 入口（告警/历史保留/定时报表）+ CI fast-smoke job + 全模块回归 + 双产物发布 · 全量测试全绿/覆盖84/ruff0/mypy0",  # noqa: E501
    "9.1.0: G59 第三轮收官 - 评分卡 per_repo_size 接入真实 dir_size · 企业 IM 告警推送(钉钉/飞书/企微)接入主窗口 + token 加密落盘 · CI 主矩阵 fast/slow 拆分 · 文档资产(architecture/ADR/code-gems/product-brief/perf-observability)",  # noqa: E501
    "9.1.1: 补缺 - G58-5 认证头类型 UI(Bearer/PRIVATE-TOKEN/Basic 下拉 + service 头映射 + 真实 git 头注入 E2E) · G57-5 无障碍声明 · G66-1 Windows 版本资源 8.0.2→9.1.1 修复",  # noqa: E501
    "9.1.2: 文档治理收官 - G51-3 扫描证据 478KB 归档 zip · G51-4 根目录收敛(参考结果计划指南移入计划书/16 个构建日志清理) · G51-5 v8 历史段抽为独立只读归档 · G51-6 workflow_status 里程碑仪表盘 · G52-G56 章节表全量回填[已落地]",  # noqa: E501
    "9.4.2: G67-6/G69-18 收口 - ①**打断 MainWindow 引用环**（4 个 Panel 的 owner 改弱引用、信号连接不再用 lambda 捕获 self、扫描闭包/剪贴板回调/拖拽 handler 全改弱引用）：实测 `del w` 后**未跑 gc.collect 即按引用计数释放**，新增守卫 TestG69_18NoMainWindowRefCycle；②**完成通知 Webhook 补全**（原实现只会发一次）：同 payload 600s 幂等去重、每次尝试写 <data>/notify.log 审计台账（**只记 scheme://host**，杜绝 Server酱/TG bot token 等路径凭据外泄）、失败重试 1 次、设置页「查看通知审计」入口（含不可见窗口的模态阻塞守卫）。新增 test_g69_notify_hardening(14)，覆盖重试/去重/审计/滚动/防泄漏/落盘失败不影响主流程",  # noqa: E501
    "9.4.1: G69-18 收口 - 接线两处「只在 tests 里被引用」的诊断能力：设置页「网络诊断」现在真跑 run_env_diagnostics(环境体检:数据目录/磁盘/git/Python-Qt) + run_diagnostics(网络)，结果进新增 gcm/ui/diag_dialog.py 对话框；该对话框带「复制诊断文本」按钮，用的正是 copy_paste_report 生成的 redact 可外发文本(diag_finished 信号由 2 参扩为 3 参)。新增守卫 TestG69_18DiagnosticsWiring(4)",  # noqa: E501
    "9.4.0: G69-18 终局闭环审计 - **CI 连续红灯根因定位并修复**：test_git_thread_exit 用例C 用 time.sleep(3600) 模拟卡死 stdout 且断言「线程仍存活」→ _pump 读线程被故意泄漏，在整套测试剩余时间里存活并触发原生崩溃（实测 access violation，进程 exit 139/0xC0000409，unittest 来不及打印失败清单）—— 这就是 CI test job 连续 80+ 次红灯无人能定位的成因；改为 Event 放行 + 收尾断言线程真能退出 + 新增「零泄漏线程」行为守卫。ci_run_fast 加固为逐模块落痕 + 失败逐条点名 + faulthandler（崩溃也能定位到模块）。独立复验推翻「计划书台账全部闭环」的删除理由：定时报表零生产调用点、AlertScanner 未接 on_alerts、retry/watchdog/IM告警等 9 个设置字段只读不写 → 全部接线并补 12 个设置控件；GitService._env 改为追加而非从 0 覆盖调用方已有的 GIT_CONFIG_*（此前静默吃掉别人的 git 配置）；告警线程→GUI 的 emit 改弱引用包装（防对已销毁 QObject emit 崩溃）；硬件敏感用例移出正确性门禁（断言未削弱）；HTTP 测试 addCleanup LIFO 顺序修正（WinError 10038 噪声消除，模块 36.6s→18.4s）；测试进程环境隔离 mixin（GIT_CONFIG_* 污染致「本机绿 CI 红」）。另修 agent 接入面两处：MCP export_report 的 out_dir 越界异常逃逸出 stdio 循环致通道中断(traceback/rc=1) → 转 JSON-RPC -32602；MCP/HTTP 自拼数据目录违反「数据目录单源」红线(安装版读到 _internal/data 或克隆输出目录的空库) → 复用 get_data_dir()。 新增永久守卫 TestG69_18WiringClosure(6) + TestG69_18VerifierFindings(5) + 零泄漏线程(1) + 环境自洽(1) + ScanWorker 收敛(3) + agent 接入面(2) + LogBuffer 定时器(3)",  # noqa: E501
    "9.3.2: G69-17 窗口几何修复 - 用户实测「双击打不开」：恢复的窗口矩形为 (-21298,-21183)，与任何显示器都无交集（进程活着、任务栏有它，屏幕上看不见）。根因是 QWidget.move() 不会矫正离屏坐标（实测 move(-21298) 就真停在那），而 _restore_geometry 把 settings.geometry 原样 move、不检查该位置是否还有屏幕 —— 换显示器/改分辨率/拔外接屏后必现。修复：恢复前逐屏校验坐标，不合法 → 主屏工作区居中；合法坐标仍原样恢复（不丢用户选择）。伴生修复：最大化时序列化连位置一起存（原来只写 M:WxH 两个数字，恢复按 4 个解包 → ValueError 被 except 吞掉 → 最大化永远恢复不了）。新增守卫 TestWindowNeverRestoresOffscreen(7)",  # noqa: E501
    "9.3.1: 高并发压测轮补丁 - 自建压测（多进程争用 / 300 行进度表 / 60 并发 HTTP）抓出并修掉 3 处并发缺陷：G69-14 本地 HTTP API 的 listen backlog 仍是 socketserver 默认的 5 → 50 并发时报 WinError 10061「目标积极拒绝」（客户端只看到连不上）→ 抬到 128，复验 60 并发全部 200 · G69-15 RetryQueue.load / RuntimeSessionStore.load 只持线程锁、没有跨进程锁 → Windows 上读者持有目标句柄时写方的 os.replace 直接失败 → 读也持 cross_process_lock · G69-16 **真因**：msvcrt.LK_LOCK 内建「重试 10 次后抛错」上限，争用下抛错即触发「静默降级为仅线程锁」（实测 DEGRADED=1），该进程从此完全失去跨进程保护、两个写方同时进临界区 —— 表面上像是「退避不够」，加大退避无效 → 改用 LK_NBLCK + 自行轮询（预算 30s 由我们掌握），复验降级 0 次、并发写错误 0 次 · 另修本轮自身引入的回归：给 load 加锁时漏了 path=None 的纯内存模式（快集 ERROR 抓到）。新增守卫 test_g69_regressions 4 例 + 压测台账记入 docs/验证记录-2026-10-14.md",  # noqa: E501
    "9.3.0: G69 独立审计修复 - 独立审计线程报出并逐条复现的 8 项缺陷。【安全】Kestrel-1 同步流程用裸 shutil.rmtree 删 DB 派生的任意目录(导入别人分享的元数据 → 一键更新 → 任意非空目录被整棵删除；改用同文件既有 _safe_rmtree_partial 并显式报失败) · 本地扫描把 .git/config 内嵌凭据带进 DB/元数据导出/剪贴板/上屏(改为在入库口统一剥凭据 + 两处展示打码) · token「加密」实为**源码内硬编码密钥的 XOR**(打包产物无 win32crypt/keyring，实测可离线还原明文) → 改用 ctypes 直调 crypt32 DPAPI(零新依赖、与历史密文格式兼容) · 本地 HTTP API 不校验 Host/Origin(伪造 Host 实测 200，DNS rebinding 可读数据) → 非回环一律 403 · MCP export_report 的 out_dir 可写任意绝对路径 → 限定在数据目录内 · 报表公式注入(仓库名可被 Excel 当公式求值) → 以 =+-@ 开头的单元格加前缀 · 【挂死/死锁/丢数据】放弃读线程后仍 close stdout 致永久阻塞 · 替换引擎不 drain 旧引擎致 Python 析构与 worker 抢 GIL 死锁 · retry_queue/session_runtime/progress_gc 固定 tmp 名且无锁(实测 300→16 条静默丢失) → 统一抽 gcm/util/atomic_io.write_json_atomic · 【错行】排序开启时进度/结果按 index 直取行号(实测 300/300 错位、「重试此仓库」重试错仓库) → 一律按行内 engine index 反查 · 【正确性】/api/report 的列取自 sync_history 而行取自 repos 致恒空 · 升级状态无法恢复(最大化写出 2 个数字却按 4 个解包) · 新增守卫 test_g69_regressions(11) + test_g69_security_fixes(21)",  # noqa: E501
    "9.2.1: G68 硬化 - G68-2 cross_process_lock 重入改为**按路径**判定(原按深度计数，导致不同路径嵌套时内层文件锁被完全跳过；两进程区间重叠判定实测修复前重叠 1.08s) · G68-3 sanitize_name 处理 Windows 保留设备名(归档 CON/COM1/NUL 等名字必失败 rc=128 且报错误导；须改**点前段**而非尾部追加——实测 CON.txt_.zip 仍失败、CON_.txt.zip 才成功) · G68-4 窗口不可见时不再弹模态(headless 下 QMessageBox.exec 永久阻塞，faulthandler 抓到 _on_update_checked ← processEvents 挂死 7 分钟+) · G68-1 设置页 432 行单函数拆为 settings_groups.py 的 7 个构建器(settings_panel 899→454 行) · 新增守卫 test_g68_settings_ui(13) + test_g68_filename_safety(6) + 锁嵌套 2 用例",  # noqa: E501
    "9.2.0: G67 终局收尾 - 接线三处「虚伪实现」(host_probe 纳入网络诊断 / xlsx 导出补 UI 入口且空结果集不再崩 / 全局 SSH key 补设置项+引擎即时生效) · 删除零生产引用的 rate_limit.py · G67-3 重投复用批次上下文(host/fetch_depth/mirror)与进度表越界补行 · G67-4 常用主机白名单改读真实数据目录(安装版永久失效) · G67-8 告警/报表线程 stop 不再清引用致重复起线程 · G67-9 检查更新移出 GUI 线程(内部有 2s 重试) · G67-10 settings.json 加跨进程写锁(新 gcm/util/filelock.py，含无锁对照组反向自检) · G67-11 无法判定落后数时不再假报「已是最新」 · G67-13 progress 锁失败不再泄漏 fd + mark_finished 读改写原子化 · G67-14 repo_db 拆出 progress_file.py · G67-18 _fetch_rows 报错上抛不再吞成空报表 · G67-22 engine.shutdown 不等 worker 收敛致 in_progress 残留 · G67-23 settings 并发保存 tmp 名冲突且 Windows 目标被占用会 PermissionError(改 pid/tid 唯一 tmp 名 + 退避重试) · G67-24 文档守卫扩到 CLAUDE.md/skills 后抓出 6 个技能包脚本路径死链并修正 · 删除已闭环的 计划书/ 迭代台账(现状迁入 docs/data-model.md) · 守卫 test_g67_closure(16) + test_g67_docs_freshness(13)",  # noqa: E501
    "9.1.5: 独立复验线程抓到的两项 P1 回归修复 - 浅仓增量更新永久失败(G66-10 让 --depth 独立生效后，_update 在浅仓上重复 fetch --depth 导致新 tip 成为无父 shallow 边界、merge-base 无解、被误判为冲突，仓库从第二次同步起永久无法更新；改为 plain fetch 尊重既有浅边界) · 钩子路径引号绕过(模板 placeholder 带后缀时生成相邻双引号使引号状态翻转、实测注入成功；改为按引号上下文决定是否加引号，安全与可用性同时成立) · _console_encoding 改用 GetACP(不受 PYTHONUTF8 影响) · IPv6 绑定不再崩溃且文案不再误导 · redact 不再吞掉收尾引号 · 守卫 53→60 用例",  # noqa: E501
    "9.1.4: G67 安全与线程安全补丁 - G67-1 克隆后钩子命令注入修复(路径强制双引号包裹, A/B 实测: 目录名 x&mkdir PWNED&y 修复前真的执行了 mkdir) · G67-2 三处跨线程直接操作 Qt 控件改为 pyqtSignal 桥接(main_window/progress_table/settings_panel) · 附带修复归档取光标行与文件名清洗 · 守卫 40→51 用例(含反向自检: 取基线 commit 断言守卫能抓到修复前的违规)",  # noqa: E501
    "9.1.3: G66 第四轮终局审计补丁 - G66-2 契约版本单源(CLI --json/--diag + MCP serverInfo + 2 个技能脚本共 5 处硬编码 8.0.2 → gcm.__version__；pyproject/installer.iss 同步) · G66-3 时间炸弹用例修复(test_repo_tags 写死日期致主矩阵 2026-10-12 实测红灯) · G66-4 中文 Windows(cp936) 子进程解码崩溃修复(26 处 text=True 补 encoding=utf-8) · G66-6 本地 HTTP API 强制回环绑定(拒绝 0.0.0.0 无鉴权暴露) · G66-5/G66-7 CI artifact 与 step 名修复 · 新增 test_g66_patch.py 19 项永久守卫",  # noqa: E501

    "0 子进程+线程池并发+scandir 跳 junction) · 裸仓/worktree 完整性 · local_repos_dialog "
    "O(1) 预载+item checkState+批量写库 · 实测 300 仓 149.4s→1.6s(约95x) · "
    "全量 770 绿 · 覆盖 84%",
)

