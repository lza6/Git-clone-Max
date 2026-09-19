"""G46-11 使用说明对话框：小白语法速查（@tag / host=token / 镜像 / 快捷键 / 拖拽 / 清单 / Star 导入）。"""
from __future__ import annotations

from PyQt6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout

# 说明正文（每条独立段落，便于单测断言关键字）
HELP_SECTIONS = [
    ("仓库地址语法", [
        "每行一个仓库地址，支持：https://host/o/r(.git) / git@host:o/r.git / 短格式 o/r",
        "分支/标签：https://github.com/o/r@v1.2.0 （@ 后指定分支或标签，只克隆该引用）",
        "跨平台：GitHub / GitLab / Gitee / Codeberg / Bitbucket / 自建主机均可",
    ]),
    ("按 host 凭据（host=token）", [
        "设置页「按平台凭据」每行一个：host=token，例如 gitlab.com=glpat-xxx",
        "私有仓库访问走该凭据；留空不发送；仅对登记过的 host 生效",
    ]),
    ("镜像前缀", [
        "设置页「镜像前缀」每行一个：host=前缀，例如 github.com=https://ghproxy.com",
        "克隆时自动拼接镜像地址（仅 https 生效；SSH 不拼）",
    ]),
    ("快捷键", [
        "Ctrl+= 放大 / Ctrl+- 缩小 / Ctrl+0 复位字号",
        "Ctrl+Alt+S 开始/取消全部 · Ctrl+Alt+U 一键更新 · Ctrl+Alt+M 显示主窗口",
    ]),
    ("拖拽与清单", [
        "把 .txt / .csv 拖进输入区自动按行填充地址；目录拖入会触发本地仓库导入扫描",
        "「保存清单」把当前输入区存为命名清单（data/lists/*.json），可分享给他人载入",
    ]),
    ("Star 列表导入", [
        "设置页可一键导入 GitHub Star 列表，自动加入下载队列",
    ]),
    ("本地已有仓库", [
        "「导入本地已有仓库」扫描任意目录下已存在的 git 仓库，纳入统一管理后增量更新",
    ]),
]


def help_html() -> str:
    """生成只读富文本（QTextBrowser 可直接 setHtml）。"""
    parts = ["<h2>Git-clone-Max 使用说明</h2>"]
    for title, lines in HELP_SECTIONS:
        parts.append(f"<h3>{title}</h3>")
        parts.append("<ul>" + "".join(f"<li>{line}</li>" for line in lines) + "</ul>")
    return "".join(parts)


class HelpDialog(QDialog):
    """使用说明对话框：只读展示语法速查。不依赖 MainWindow 具体实现。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("使用说明")
        self.resize(560, 460)
        v = QVBoxLayout(self)
        self.browser = QTextBrowser()
        self.browser.setOpenExternalLinks(False)
        self.browser.setHtml(help_html())
        v.addWidget(self.browser)
