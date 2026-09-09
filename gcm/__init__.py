"""Git-clone-Max — GitHub 仓库批量并行下载与增量更新工具。"""
__version__ = "5.0.0"
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
)
