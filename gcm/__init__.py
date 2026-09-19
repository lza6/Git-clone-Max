"""Git-clone-Max — GitHub 仓库批量并行下载与增量更新工具。"""
__version__ = "8.0.1"
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
)

