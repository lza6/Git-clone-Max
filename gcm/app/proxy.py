# -*- coding: utf-8 -*-
"""G04-3 代理自动检测：读取系统代理（Windows 注册表 / 环境变量）。

优先级：环境变量 HTTP_PROXY/HTTPS_PROXY > Windows 系统代理（注册表
Internet Settings ProxyServer / ProxyEnable）。返回形如 http://host:port。
"""
from __future__ import annotations

import os


def detect_system_proxy() -> str:
    """探测当前系统可用的代理地址；无则返回空串。

    顺序：环境变量（HTTPS_PROXY > HTTP_PROXY）→ Windows 注册表系统代理。
    """
    # 1) 环境变量优先
    for key in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        v = os.environ.get(key, "").strip()
        if v:
            return v
    # 2) Windows 注册表系统代理
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            ) as k:
                enabled, _ = winreg.QueryValueEx(k, "ProxyEnable")
                if enabled:
                    server, _ = winreg.QueryValueEx(k, "ProxyServer")
                    if server:
                        server = str(server).strip()
                        # 形如 http=127.0.0.1:7890;https=... → 取 https
                        for part in server.split(";"):
                            if part.lower().startswith("https="):
                                return "http://" + part.split("=", 1)[1]
                            if part.lower().startswith("http="):
                                return "http://" + part.split("=", 1)[1]
                        # 纯 host:port
                        if "=" not in server:
                            return "http://" + server
        except Exception:
            pass
    return ""
