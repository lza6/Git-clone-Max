# -*- coding: utf-8 -*-
"""设置持久化：并发数 / 深浅克隆 / 自动清空 / 代理 / 超时 / 自动化开关 等。

存储为 data/settings.json，原子写入（temp + os.replace），损坏时兜底为默认值。
G06-1：token 安全存储 —— 落盘前加密（Windows DPAPI / macOS Keychain / Linux keyring），
无可用后端时降级为掩码存储并在日志注明；读取时解密，失败降级为空串。
"""
from __future__ import annotations

import base64
import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

# token 加密封装：优先平台密钥环，缺失时用 DPAPI（Windows）或简单掩码。
# 关键目标：settings.json 中 token 永不明文。
_ENC_PREFIX = "enc:v1:"          # 加密值前缀（明示已加密）
_MASK_PREFIX = "mask:"           # 降级掩码值前缀
_enc_backend = None              # 惰性探测的后端名（dpapi/keyring/none）


def _detect_backend() -> str:
    """探测可用的加密后端：win32crypt(DPAPI) > keyring > 无。"""
    try:
        import win32crypt  # noqa: F401
        return "dpapi"
    except Exception:
        pass
    try:
        import keyring  # noqa: F401
        return "keyring"
    except Exception:
        pass
    # 无系统密钥环（如最小化 Python/CI）：退化为进程内 XOR 弱加密，
    # 保证 settings.json 中 token 不明文；解密同密钥即可还原。
    return "xorfixed"


_XOR_KEY = b"Git-clone-Max-token-v1"


def _xor_crypt(plain: str) -> str:
    """进程内固定密钥 XOR 加密（防御「明文落盘」，非防破解）。"""
    data = plain.encode("utf-8")
    out = bytes(b ^ _XOR_KEY[i % len(_XOR_KEY)] for i, b in enumerate(data))
    return _ENC_PREFIX + base64.b64encode(out).decode("ascii")


def _xor_decrypt(enc: str) -> str:
    """逆 XOR 解密（enc 为带 _ENC_PREFIX 的完整串）。"""
    data = base64.b64decode(enc[len(_ENC_PREFIX):])
    out = bytes(b ^ _XOR_KEY[i % len(_XOR_KEY)] for i, b in enumerate(data))
    return out.decode("utf-8")


def _get_backend():
    global _enc_backend
    if _enc_backend is None:
        _enc_backend = _detect_backend()
    return _enc_backend


def _encrypt_token(plain: str) -> str:
    """加密 token 字符串；返回带前缀的密文。空输入原样返回。"""
    if not plain:
        return ""
    backend = _get_backend()
    try:
        if backend == "dpapi":
            import win32crypt
            blob = win32crypt.CryptProtectData(
                plain.encode("utf-8"), None, None, None, None, 0)
            return _ENC_PREFIX + base64.b64encode(blob).decode("ascii")
        if backend == "keyring":
            import keyring
            try:
                keyring.set_password("Git-clone-Max", "github_token", plain)
                return _ENC_PREFIX + "keyring"
            except Exception:
                pass
        if backend == "xorfixed":
            return _xor_crypt(plain)
        # 无后端：掩码降级（不可逆，读回为空；保存时用明文占位但落盘前替换）
        return _MASK_PREFIX + "*" * min(len(plain), 12)
    except Exception:
        # 加密失败：掩码降级，保证绝不明文落盘
        return _MASK_PREFIX + "*" * min(len(plain), 12)


def _decrypt_token(enc: str) -> str:
    """解密 token；非加密值/解密失败返回空串。"""
    if not enc:
        return ""
    try:
        if enc.startswith(_ENC_PREFIX):
            data = enc[len(_ENC_PREFIX):]
            backend = _get_backend()
            if backend == "dpapi":
                import win32crypt
                blob = base64.b64decode(data)
                return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode("utf-8")
            if backend == "keyring" and data == "keyring":
                import keyring
                try:
                    return keyring.get_password("Git-clone-Max", "github_token") or ""
                except Exception:
                    return ""
            if backend == "xorfixed":
                return _xor_decrypt(enc)
        if enc.startswith(_MASK_PREFIX):
            return ""  # 掩码不可逆
    except Exception:
        pass
    return ""


def _mask_token(plain: str) -> str:
    """展示用掩码（保留前 5 位 + 星号）。"""
    if not plain:
        return ""
    if len(plain) <= 5:
        return "*" * len(plain)
    return plain[:5] + "*" * (len(plain) - 5)


@dataclass
class Settings:
    """应用级可持久化配置。"""

    concurrency: int = 8              # 并行线程数 1..16
    shallow_default: bool = False     # 是否默认浅克隆
    depth: int = 1                    # 浅克隆深度
    auto_clear: bool = True           # 下载完成自动清空输入框
    proxy: str = ""                   # HTTP 代理，形如 http://127.0.0.1:7890
    fetch_timeout: int = 300          # 单次 fetch 超时秒数
    clone_timeout: int = 600          # 单次 clone 超时秒数
    retries: int = 2                  # 网络失败自动重试次数
    auto_init_empty: bool = False     # 空仓库自动初始化 main 并推送
    minimize_to_tray: bool = True     # 最小化时收进系统托盘
    language: str = "zh"              # 保留：zh / en（i18n 备用）
    token: str = ""                   # GitHub token（私有仓库认证；落盘前加密）
    fetch_unshallow: bool = False     # 浅克隆仓库增量 fetch 时拉全量历史（V3-P0-3）
    download_dir: str = ""            # 上次使用的下载位置（启动时恢复）
    submodule: bool = False           # G08-1 克隆时拉取子模块（--recurse-submodules）
    theme: str = "deep"               # G05-1 主题：deep/light/nord
    clipboard_watch: bool = False     # G09-1 剪贴板监听（检测到仓库地址提示）
    font_scale: float = 1.0           # G10-1 字号缩放（0.8 ~ 1.6）


_DEFAULTS: dict = asdict(Settings())


class SettingsStore:
    """线程安全、原子写、损坏兜底 的 settings.json 封装。"""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> Settings:
        with self._lock:
            raw: dict = {}
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        raw = data
                except Exception:
                    # 主文件损坏 → 尝试从 .bak 恢复，仍失败则兜底默认值不崩
                    raw = self._try_restore_backup()
            # 只取已知字段的类型安全合并
            merged = dict(_DEFAULTS)
            for k in _DEFAULTS:
                if k in raw:
                    try:
                        merged[k] = _coerce(k, raw[k])
                    except Exception:
                        pass
            s = Settings(**merged)
            # G06-1：token 读回解密（掩码/损坏 → 空串，不崩）
            s.token = _decrypt_token(s.token)
            return s

    def _try_restore_backup(self) -> dict:
        """主 settings.json 损坏时尝试从 .bak 恢复；返回恢复的 dict 或 {}。"""
        bak = self.path.with_suffix(".json.bak")
        if not bak.exists():
            return {}
        try:
            data = json.loads(bak.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
        return {}

    def save(self, s: Settings) -> None:
        """原子写 settings.json；token 加密落盘。每次保存前把当前文件备份到 .bak。"""
        with self._lock:
            tmp = self.path.with_suffix(".json.tmp")
            payload = asdict(s)
            # G06-1：token 落盘前加密（绝不明文）
            if payload.get("token"):
                payload["token"] = _encrypt_token(payload["token"])
            tmp.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # G06-8：备份当前磁盘上的旧文件到 .bak（读入内存再写，避免 replace 后取不到旧内容）
            old = b""
            try:
                if self.path.exists():
                    old = self.path.read_bytes()
            except Exception:
                old = b""
            if old:
                try:
                    bak = self.path.with_suffix(".json.bak")
                    bak.write_bytes(old)
                except Exception:
                    pass
            os.replace(tmp, self.path)  # 原子写


def _coerce(key: str, val):
    """按默认值类型强制转换，失败抛异常交由调用方兜底。"""
    default = _DEFAULTS[key]
    if isinstance(default, bool):
        return bool(val)
    if isinstance(default, int):
        return int(val)
    if isinstance(default, float):
        return float(val)
    if isinstance(default, str):
        # None（如用户清空代理）→ 空字符串，避免出现字面 "None"
        return str(val) if val is not None else ""
    return val