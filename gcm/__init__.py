"""Git-clone-Max — GitHub 仓库批量并行下载与增量更新工具。"""
__version__ = "5.7.0"
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
    "5.1.0: 可靠性加固 G06 - token 加密落盘(DPAPI/Keyring/XOR 降级) · single-instance 进程锁+崩溃残留清理 · "
    "全局异常兜底 error.log · settings .bak 备份+损坏恢复 · 跨进程单实例 E2E · 全量 249 测试全绿 · 覆盖 83%",
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
)
