"""G71-3 三段式错误模型：发生了什么 / 为什么 / 该怎么办。

参考项目 SurgeDM/Surge 把错误分成「可重试」与「永久失败」两类，并在 UI 上
同时给出**原因**与**下一步动作**；主项目原先只有一句 git 原始报错，小白用户
看到 `fatal: unable to access ... 403` 仍然不知道该改哪个设置。

本模块是**纯分类器**（无 Qt、无 IO、无网络），三处消费：
- `app/retry_queue.py`：只有 `retryable=True` 才自动重投；
- `ui/failure_panel.py`：按类别聚合 + 展示三段式文案；
- `http_api.py` / SSE：`task_error` 事件携带结构化错误，agent 不必解析文案。

分类优先级（前面的赢）：
1. 用户取消 → `cancelled`（不是错误，不重试）
2. 磁盘/配额 → `local`（不重试，重试只会再撞一次）
3. Windows 平台/路径限制 → `platform`（不重试，换环境才能解决）
4. 认证/权限（401/403）→ `auth`（不自动重试；用户更新凭据后手动重试）
5. 仓库不存在（404 / Repository not found）→ `not_found`（不重试）
6. 限流（429 / rate limit）→ `network`（**可重试**，Surge 同口径）
7. 5xx / 网络类 → `network`（可重试）
8. 其余 → `other`（保守不自动重试）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from ..git.service import _is_networkish_error
from ..util.redact import redact

# 与 ui/failure_panel.py 的历史常量保持一致，避免旧调用方/测试失效。
CAT_NETWORK = "network"
CAT_AUTH = "auth"
CAT_PLATFORM = "platform"
CAT_LOCAL = "local"
CAT_OTHER = "other"
CAT_NOT_FOUND = "not_found"
CAT_CANCELLED = "cancelled"

_CATEGORY_LABELS = {
    CAT_NETWORK: "网络",
    CAT_AUTH: "认证/权限",
    CAT_PLATFORM: "平台限制",
    CAT_LOCAL: "本地环境",
    CAT_NOT_FOUND: "仓库不存在",
    CAT_CANCELLED: "已取消",
    CAT_OTHER: "其他",
}

# 三段式文案：标题（发生了什么）/ 原因（为什么）/ 动作（该怎么办）。
_CATEGORY_TEXT = {
    CAT_NETWORK: (
        "网络连接失败或超时",
        "远端不可达、连接被重置、TLS 握手失败，或触发平台限流。",
        "检查网络与代理；稍后重试。开启「失败自动重试」后网络类错误会自动重投。",
    ),
    CAT_AUTH: (
        "认证失败或权限不足",
        "Token/凭据缺失、过期，或当前账号没有该仓库的读取权限。",
        "到设置页更新对应 host 的 Token/凭据，然后手动重试；自动重试不会解决权限问题。",
    ),
    CAT_PLATFORM: (
        "平台或文件路径限制",
        "仓库包含 Windows 非法文件名（如 : * ?），或路径过长/文件被占用。",
        "改到 WSL/Linux 环境克隆，或跳过该仓库；可先启用长路径支持并清理占用。",
    ),
    CAT_LOCAL: (
        "本地写入失败",
        "磁盘空间不足、目录只读、文件被占用，或没有写入权限。",
        "清理磁盘空间/关闭占用程序/更换下载目录后重试；重试前先确认目标目录可写。",
    ),
    CAT_NOT_FOUND: (
        "仓库不存在或不可访问",
        "仓库已被删除、改名、转为私有，或 URL 拼写错误。",
        "核对仓库地址；私有仓库先确认凭据与权限。这类错误重试无效。",
    ),
    CAT_CANCELLED: (
        "任务已取消",
        "用户主动取消或程序退出时终止。",
        "需要时重新开始即可；已下载的部分会按断点续传规则处理。",
    ),
    CAT_OTHER: (
        "未归类的 git 错误",
        "git 返回了分类器不认识的信息，可能是仓库状态异常或本地环境问题。",
        "展开详情查看原始报错；必要时导出诊断包（含已打码的事件流与日志）。",
    ),
}

_DISK_KEYWORDS = (
    "no space left on device", "disk full", "insufficient disk space",
    "not enough space", "quota exceeded", "cannot write", "write error",
)
_LOCAL_KEYWORDS = (
    "read-only file system", "cannot create directory", "cannot mkdir",
    "cannot create work tree", "permission denied (publickey)",
    "could not create work tree", "filename too long", "file name too long",
)
_PLATFORM_KEYWORDS = (
    "invalid path", "file exists", "already exists",
    "unable to checkout", "unable to create file", "unable to create directory",
    "invalid argument", "symlink", "long path",
)
_AUTH_KEYWORDS = (
    "authentication failed", "invalid username or password",
    "could not read username", "access denied", "permission denied",
    "not authorized", "bad credentials", "invalid token",
    "support for password authentication was removed",
)
_NOT_FOUND_KEYWORDS = (
    "repository not found", "not found", "does not appear to be a git repository",
    "could not read from remote repository", "please make sure you have the correct access rights",
    "remote: not found",
)
_RATE_LIMIT_KEYWORDS = (
    "too many requests", "rate limit", "rate-limit",
    "secondary rate limit", "abuse detection", "retry-after",
)
# 数字状态码只走 `_STATUS_RE`（带单词边界）；不要在关键词里放裸 "401"/"404"/
# "500" —— 端口 4031、提交号 deadbeef404 会被误判（审查 S6）。
_NETWORK_5XX = ("internal server error", "bad gateway",
                "service unavailable", "gateway timeout")
# 中文失败文案（`git/service._classify_failure` 产出）需要同样识别为可重试，
# 否则「网络连接失败（可自动重试）」会被误判成 other → 永不重投。
_NETWORK_CN = ("网络连接失败", "网络异常", "连接超时", "连接被重置",
               "远端不可达", "无法连接", "网络不可达")

_STATUS_RE = re.compile(r"\b(4\d{2}|5\d{2})\b")


@dataclass(frozen=True)
class ErrorInfo:
    """结构化错误（可安全外发：`raw` 由调用方保证已打码）。"""

    category: str
    code: str
    title: str
    cause: str
    action: str
    retryable: bool
    label: str = ""
    http_status: Optional[int] = None
    raw: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        """给 JSON / SSE 用的字典（不含私有字段）。"""
        out: dict[str, Any] = {
            "category": self.category,
            "code": self.code,
            "title": self.title,
            "cause": self.cause,
            "action": self.action,
            "retryable": self.retryable,
            "label": self.label or category_label(self.category),
        }
        if self.http_status is not None:
            out["http_status"] = self.http_status
        if self.raw:
            out["raw"] = self.raw
        if self.extra:
            out["extra"] = dict(self.extra)
        return out

    def display_lines(self) -> list[str]:
        """UI 三段式展示文本（不带图标，便于表格/日志复用）。"""
        return [f"发生了什么：{self.title}",
                f"为什么：{self.cause}",
                f"怎么办：{self.action}"]


def _status_code(text: str) -> Optional[int]:
    m = _STATUS_RE.search(text)
    return int(m.group(1)) if m else None


def _make(category: str, code: str, *, retryable: bool,
          http_status: Optional[int] = None, raw: str = "",
          extra: Optional[dict[str, Any]] = None) -> ErrorInfo:
    title, cause, action = _CATEGORY_TEXT[category]
    return ErrorInfo(
        category=category,
        code=code,
        title=title,
        cause=cause,
        action=action,
        retryable=bool(retryable),
        label=category_label(category),
        http_status=http_status,
        raw=redact(raw)[:2000],
        extra=dict(extra or {}),
    )


def category_label(category: str) -> str:
    return _CATEGORY_LABELS.get(category, _CATEGORY_LABELS[CAT_OTHER])


def is_retryable_message(message: str, detail: str = "",
                         status: Optional[int] = None) -> bool:
    """轻量判断（供旧调用方/热路径使用）：是否值得自动重投。"""
    return classify_error(message, detail, status=status).retryable


def classify_error(message: str, detail: str = "",
                   status: Optional[int] = None,
                   retry: Optional[bool] = None) -> ErrorInfo:
    """把 git 原始报错映射为三段式结构化错误。

    `retry` 显式覆盖分类结果（调用方已知上下文时用，例如「已到重试上限」）。
    """
    text = f"{message or ''}\n{detail or ''}".strip()
    low = text.lower()
    code_status = status if status is not None else _status_code(text)

    def result(cat: str, code: str, retryable: bool,
               extra: Optional[dict[str, Any]] = None) -> ErrorInfo:
        return _make(cat, code, retryable=retryable,
                     http_status=code_status, raw=text[:2000], extra=extra)

    # 1) 取消
    if "已取消" in text or "cancelled" in low or "canceled" in low:
        info = result(CAT_CANCELLED, "cancelled", False)
    # 2) 磁盘/配额
    elif any(k in low for k in _DISK_KEYWORDS):
        info = result(CAT_LOCAL, "disk_full", False)
    # 3) 平台/路径限制
    elif any(k in low for k in _PLATFORM_KEYWORDS):
        info = result(CAT_PLATFORM, "platform_path", False)
    # 3.1) 本地环境（只读/权限/长路径）
    elif any(k in low for k in _LOCAL_KEYWORDS):
        info = result(CAT_LOCAL, "local_io", False)
    # 4) 认证/权限（401/403 优先于泛化 4xx）
    elif code_status in (401, 403) or any(k in low for k in _AUTH_KEYWORDS):
        info = result(CAT_AUTH, "auth_failed", False)
    # 5) 仓库不存在（404）
    elif code_status == 404 or any(k in low for k in _NOT_FOUND_KEYWORDS):
        info = result(CAT_NOT_FOUND, "repo_not_found", False)
    # 6) 限流：429 是临时性的，必须可重试（Surge 同口径）
    elif code_status == 429 or any(k in low for k in _RATE_LIMIT_KEYWORDS):
        info = result(CAT_NETWORK, "rate_limited", True)
    # 7) 5xx / 网络类
    elif (code_status is not None and 500 <= code_status <= 599) or \
            any(k in text for k in _NETWORK_CN) or \
            any(k in low for k in _NETWORK_5XX) or _is_networkish_error(text):
        code = "server_error" if (code_status or 0) >= 500 else "network"
        info = result(CAT_NETWORK, code, True)
    # 8) 兜底
    else:
        info = result(CAT_OTHER, "unknown", False)

    if retry is not None and info.retryable != bool(retry):
        return ErrorInfo(
            category=info.category, code=info.code, title=info.title,
            cause=info.cause, action=info.action, retryable=bool(retry),
            label=info.label, http_status=info.http_status, raw=info.raw,
            extra=info.extra,
        )
    return info
