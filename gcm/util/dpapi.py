"""Windows DPAPI 封装（ctypes 直调，**不依赖 pywin32**）。

## 为什么需要它（G69-12，独立安全审计报出的 HIGH）
`gcm/db/settings.py` 声称「settings.json 中 token 永不明文」，后端探测顺序是
`win32crypt` → `keyring` → **固定密钥 XOR**。但项目的依赖只有 `PyQt6`
（`requirements.txt`、`Git-clone-Max.spec` 的 `hiddenimports=[]`），
打包产物里既没有 win32crypt 也没有 keyring —— 于是**所有实际安装都走 XOR**，
而 XOR 的密钥写在仓库源码里（`_XOR_KEY = b"Git-clone-Max-token-v1"`）：

    落盘 : enc:v1:IAEEcjA5Pys3fggiKmggMD8qJWg4bnZbRxlWWlhWXB0=
    用源码密钥异或 → token = ghp_SUPERSECRET_TOKEN_1234567890

拿到 `settings.json` 的任何人都能还原全部 token（共享目录/备份/误上传/同机他人）。

用 ctypes 直调 `CryptProtectData` / `CryptUnprotectData` 即可拿到**真正的
OS 级加密**（密钥由用户登录凭据派生，跨用户/跨机器不可解），且不引入新依赖。

**格式兼容**：DPAPI 密文与 pywin32 版本产生的密文是同一种 blob，
所以本模块可以透明解密历史文件（`_ENC_PREFIX` + base64 完全相同）。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

_available: bool | None = None


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_byte))]


if os.name == "nt":  # pragma: no cover - 平台分支由 available() 统一判定
    try:
        _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        _crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(_DataBlob)]
        _crypt32.CryptProtectData.restype = wintypes.BOOL
        _crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.POINTER(_DataBlob),
            ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(_DataBlob)]
        _crypt32.CryptUnprotectData.restype = wintypes.BOOL
        _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        _LINKED = True
    except Exception:
        _LINKED = False
else:
    _LINKED = False

# 不弹 UI（本工具是后台/无交互场景，且防止在服务里阻塞）
_UI_FORBIDDEN = 0x1
_DESCR = "Git-clone-Max"


def _blob(data: bytes) -> _DataBlob:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))


def available() -> bool:
    """DPAPI 是否可用（Windows 且 crypt32 可加载）。结果缓存。"""
    global _available
    if _available is None:
        _available = False
        if _LINKED:
            try:  # 真跑一次，别只信「库能加载」
                _available = unprotect(protect(b"probe")) == b"probe"
            except Exception:
                _available = False
    return _available


def protect(plain: bytes) -> bytes:
    """加密（当前用户 + 当前机器可解）。失败抛 OSError。"""
    src, out = _blob(plain), _DataBlob()
    if not _crypt32.CryptProtectData(ctypes.byref(src), _DESCR, None, None, None,
                                     _UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), "CryptProtectData 失败")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(out.pbData)


def unprotect(enc: bytes) -> bytes:
    """解密。失败抛 OSError（凭据归属其他用户/机器时也会失败，由调用方兜底）。"""
    src, out = _blob(enc), _DataBlob()
    if not _crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None,
                                       _UI_FORBIDDEN, ctypes.byref(out)):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData 失败")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        _kernel32.LocalFree(out.pbData)
