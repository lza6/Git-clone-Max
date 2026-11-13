"""设置持久化：并发数 / 深浅克隆 / 自动清空 / 代理 / 超时 / 自动化开关 等。

存储为 data/settings.json，原子写入（temp + os.replace），损坏时兜底为默认值。
G06-1：token 安全存储 —— 落盘前加密（Windows DPAPI / macOS Keychain / Linux keyring），
无可用后端时降级为掩码存储并在日志注明；读取时解密，失败降级为空串。
"""
from __future__ import annotations

import base64
import copy
import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from ..util.filelock import cross_process_lock
from .validators import normalize_settings

# 原子替换的退避重试（Windows 上目标被任何人持有句柄时会 PermissionError；
# 持有者可能是杀软/索引器，不只是本进程的读者）—— 理由与预算见 util/atomic_io.py
_REPLACE_RETRIES = 12
_REPLACE_BACKOFF_SEC = 0.05

# token 加密封装：优先平台密钥环，缺失时用 DPAPI（Windows）或简单掩码。
# 关键目标：settings.json 中 token 永不明文。
_ENC_PREFIX = "enc:v1:"          # 加密值前缀（明示已加密）
_MASK_PREFIX = "mask:"           # 降级掩码值前缀


_FSYNC_WARNED = False


def _fsync_fd(fh) -> None:
    """尽力 fsync；不支持的文件系统（部分网络盘/FUSE）跳过并**只告警一次**。"""
    global _FSYNC_WARNED
    try:
        fh.flush()
        os.fsync(fh.fileno())
    except OSError as e:
        if not _FSYNC_WARNED:      # 每次保存都告警会刷屏，但完全沉默等于不知道
            _FSYNC_WARNED = True
            try:
                from ..app.applog import warn as _warn
                _warn(f"文件系统不支持 fsync（本次起不再重复提示）：{e}")
            except Exception:
                return


def _write_bytes_synced(path: str | Path, data: bytes) -> None:
    """写字节并 fsync（G84-9：`.bak` 也要能扛掉电）。"""
    with open(path, "wb") as fh:
        fh.write(data)
        _fsync_fd(fh)


def _write_text_synced(path: str | Path, text: str) -> None:
    """写文本并 fsync（G84-9：settings.json 的 tmp 必须内容落盘后才能 replace）。"""
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        _fsync_fd(fh)
_enc_backend = None              # 惰性探测的后端名（dpapi/keyring/none）


def _detect_backend() -> str:
    """探测可用的加密后端（G69-12 起优先 ctypes DPAPI）。

    顺序：`dpapi_ctypes`（无依赖，打包产物也能用）> `win32crypt` > `keyring` > `xorfixed`。

    **为什么把 ctypes 放第一**：项目依赖只有 PyQt6、spec 的 hiddenimports 为空 ——
    打包产物里既无 win32crypt 也无 keyring，于是**所有实际安装都落到 `xorfixed`**，
    即「用仓库源码里写死的 `_XOR_KEY` 做异或」。那不是加密：任何拿到 settings.json
    的人都能用公开密钥还原全部 token（实测可离线还原出明文）。
    ctypes 直调 crypt32 拿到了真正的 OS 级加密（密钥由登录凭据派生），且零新依赖。

    格式兼容：ctypes 与 win32crypt 产出的都是同一种 DPAPI blob，
    所以换后端后仍可透明解密历史文件。
    """
    try:
        from ..util import dpapi
        if dpapi.available():
            return "dpapi_ctypes"
    except Exception:
        pass
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
    # 兜底（非 Windows，且无 keyring）：固定密钥异或 —— 只防「明文直读」，
    # **不是加密**。此时必须让用户知情（见 `crypto_warning()`）。
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


def is_data_dir_private(data_dir, home: str | None = None) -> bool:
    """G44-6：数据目录是否位于用户私有目录（%USERPROFILE% / %APPDATA% 下）。

    加密凭据（token/host_tokens）默认落盘于 data/，若目录位于系统公共区
    （如 exe 同级 Program Files / C:\\），同机其它用户可读 → 建议迁移。
    返回 True=私有（安全）；False=公开（需提示）。
    """
    import os
    from pathlib import Path
    d = Path(data_dir).resolve()
    h = Path(home or os.environ.get("USERPROFILE") or os.path.expanduser("~")).resolve()
    if d == h or h in d.parents:
        return True
    ap = os.environ.get("APPDATA")
    if ap:
        ap_p = Path(ap).resolve()
        if d == ap_p or ap_p in d.parents:
            return True
    return False


def migrate_data_dir(src, dst) -> tuple[bool, str]:
    """G44-6：把数据目录内容迁移到 dst（幂等，不删除 src 目录本身）。

    返回 (是否成功, 错误信息/空串)。已存在的目标文件不覆盖（跳过），
    目录结构保留；失败不抛异常。
    """
    import shutil
    from pathlib import Path
    src_p, dst_p = Path(src), Path(dst)
    try:
        dst_p.mkdir(parents=True, exist_ok=True)
        moved = 0
        for item in src_p.iterdir():
            target = dst_p / item.name
            if target.exists():
                continue  # 目标已存在：不覆盖，避免数据竞争
            shutil.move(str(item), str(target))
            moved += 1
        return True, f"已迁移 {moved} 项"
    except Exception as e:
        return False, str(e)


def _encrypt_token(plain: str) -> str:
    """加密 token 字符串；返回带前缀的密文。空输入原样返回。"""
    if not plain:
        return ""
    backend = _get_backend()
    try:
        if backend == "dpapi_ctypes":
            from ..util import dpapi
            blob = dpapi.protect(plain.encode("utf-8"))
            return _ENC_PREFIX + base64.b64encode(blob).decode("ascii")
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
            if backend in ("dpapi_ctypes", "dpapi"):
                blob = base64.b64decode(data)
                # 统一走 ctypes 解（与 win32crypt 同格式）；
                # 极端情况下 ctypes 不可用再回退到 win32crypt。
                try:
                    from ..util import dpapi
                    return dpapi.unprotect(blob).decode("utf-8")
                except Exception:
                    import win32crypt
                    return win32crypt.CryptUnprotectData(
                        blob, None, None, None, 0)[1].decode("utf-8")
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


def _decrypt_or_keep(value: str) -> str:
    """解密「可能已加密」的值；**未加密的历史明文原样保留**。

    G58-3：`_decrypt_token` 对任何非 `enc:` 前缀的值一律返回空串 —— 对 token 是
    有意为之（避免把损坏密文当成可用凭据），但对**后来才加密的字段**（如 proxy）
    会造成**静默数据丢失**：老用户 settings.json 里的明文代理串在升级后会被读成
    空，代理设置凭空消失且无任何提示。

    这里区分三种情况：
      - `enc:` 前缀 → 真解密（失败返回空，与 token 一致）；
      - `mask:` 前缀 → 掩码不可逆 → 空；
      - 其它      → 视为历史明文，**原样保留**（升级不丢配置）。
    """
    if not value:
        return ""
    if value.startswith(_ENC_PREFIX) or value.startswith(_MASK_PREFIX):
        return _decrypt_token(value)
    return value


def crypto_warning() -> str:
    """当前环境的凭据存储是否达到「加密」标准；不达标时返回给用户看的说明。

    `xorfixed` 用的是写在源码里的固定密钥异或 —— 它只防「明文直读」，
    拿到 settings.json 的任何人都能还原。必须显式告知，不能让用户误以为已加密
    （审计实测：文档写「token 永不明文」，而打包产物 100% 走这条兜底路径）。
    """
    if _get_backend() == "xorfixed":
        return ("当前环境没有可用的系统密钥环（Windows DPAPI / keyring 均不可用），"
                "token 仅做了可逆混淆存储，**不等于加密**；"
                "请勿在不受信任的机器或共享目录中保存 token。")
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

    concurrency: int = 8              # 并行线程数 1..32
    host_concurrency: int = 4         # A5-5/R3：同 host 在跑上限（0=不限流）
    auto_clear: bool = True           # 下载完成自动清空输入框
    proxy: str = ""                   # HTTP 代理，形如 http://127.0.0.1:7890
    fetch_timeout: int = 300          # 单次 fetch 超时秒数
    clone_timeout: int = 600          # 单次 clone 超时秒数
    time_budget_min: int = 0          # A5-3/C-10：单仓库总时间预算（分钟，0=不限）
    retries: int = 2                  # 网络失败自动重试次数
    minimize_to_tray: bool = True     # 最小化时收进系统托盘
    language: str = "zh"              # 保留：zh / en（i18n 备用）
    token: str = ""                   # GitHub token（私有仓库认证；落盘前加密）
    fetch_unshallow: bool = False     # 浅克隆仓库增量 fetch 时拉全量历史（V3-P0-3）
    download_dir: str = ""            # 上次使用的下载位置（启动时恢复）
    submodule: bool = False           # G08-1 克隆时拉取子模块（--recurse-submodules）
    theme: str = "deep"               # G05-1 主题：deep/light/nord
    clipboard_watch: bool = False     # G09-1 剪贴板监听（检测到仓库地址提示）
    finish_sound: bool = True         # G35-2 全部任务完成时提示音（QApplication.beep）
    host_tokens: dict = field(default_factory=dict)  # G38-1 按 host 映射凭据（如 {"gitlab.com": "glpat-xxx"}；落盘逐条目加密）
    mirror_prefix: dict = field(default_factory=dict)  # G38-2/G58-2 镜像前缀（值可为 str 或 [str,...]）
    # G58-2：镜像多备援。预设 = 内置国内 GitHub 镜像；失败自动回退源站。
    # **已失效字段（v9.12.1 起）**：内置国内镜像列表已永久清空（实测比直连慢 8 倍）。
    # 保留该字段只为旧 settings.json 能无损加载（不迁移、不报错），代码不再消费它。
    mirror_use_presets: bool = False
    mirror_fallback: bool = True      # 镜像不可用/克隆失败 → 回退源站重试
    mirror_probe_timeout: int = 8     # 镜像探测超时秒数（3..60）
    precheck_remote: bool = False     # G38-3 远端可达性预检（clone/fetch 前 10s ls-remote）
    single_branch: bool = False       # G38-4 浅克隆时只拉目标分支（--single-branch）
    force_ipv4: bool = False          # G38-6 强制 HTTP/1.1（IPv6 兼容修复）
    custom_hosts: tuple = ()          # G38-7 常用主机白名单（自建 GitLab 等，短格式可直接解析）
    host_auth_types: dict = field(default_factory=dict)  # G58-5 按 host 认证头类型（bearer/private_token/basic；空=自动）
    quick_repos: tuple = (            # G35-7 下载中心「快捷填充」按钮仓库列表（可编辑持久化）
        "vercel-labs/skills", "anthropics/skills", "microsoft/azure-skills",
        "remotion-dev/skills", "slidevjs/slidev", "openmeterio/openmeter",
    )
    geometry: str = ""                # G36-4 主窗口几何（"WxH+X+Y"，记忆上次窗口尺寸/位置）
    last_tab: int = 0                 # G70 上次使用的页签（0 下载 / 1 管理 / 2 设置）
    # Rev14 R14-06：界面微状态（不是用户设置）。当前只有管理页定宽列：
    #   {"manage_columns": {"3": 150, "4": 78, ...}}
    # 放在这里而不是另开文件：它必须与其它界面状态一起原子写、一起跨进程锁。
    ui_state: dict = field(default_factory=dict)
    animations: bool = True           # G36-6 完成动效（成功绿/失败红背景色 1.2s 消隐；低配/offscreen 自动关）
    prefers_reduced_motion: bool = False  # G46-8 减少动态效果（关闭完成动效与 hover 过渡）
    accent_preset: str = "teal"           # G75：默认强调色 = Ink 的示波器青（blue/violet/teal）
    lfs_enabled: bool = False          # G48-2 Git LFS：clone/fetch 追加 -c filter.lfs.required=false
    post_clone_hook: str = ""           # G48-5 克隆后钩子命令模板（%PATH% 替换为仓库路径）
    ssh_key_path: str = ""              # G48-3/G67-16 全局 SSH 私钥路径（经 GIT_SSH_COMMAND 注入）
    first_run_done: bool = False      # G36-1 首次运行向导已完成（跳过也置 True）
    # G54-4 引导清单：只记录**推导不出来**的两项（network/help）；
    # download_dir/token/import 由现有设置与数据库实时推导，不重复存状态。
    checklist_done: dict = field(default_factory=dict)
    # G54-4：引导清单是否已自动提示过（只在首次启动弹一次，之后走设置页入口）
    checklist_prompted: bool = False
    auto_update_minutes: int = 0      # G37-4 自动更新间隔（分钟，0=关；非 busy 时触发一键更新）
    font_scale: float = 1.0           # G10-1 字号缩放（0.8 ~ 1.6）
    rate_limit_kbps: int = 0          # G04-4 下载限速 KiB/s（0=不限）
    start_minimized: bool = False      # G49-6 启动时最小化到托盘（需托盘可用；自启命令追加 --minimized）
    notify_webhook_enabled: bool = False  # G49-7 完成通知 Webhook 显式开关（与 URL 非空共同决定是否发送）
    notify_webhook_url: str = ""       # G49-7 完成通知 Webhook URL（仅显式开启且非空才发送）
    notify_webhook_token: str = ""     # G49-7 Webhook Token（可选；落盘前加密）
    retry_enabled: bool = False        # G52-1 失败自动重试队列开关（仅网络类错误）
    retry_max: int = 2                 # G52-1 最多重试次数 0-3（0 = 不自动重试）
    retry_backoff_base_sec: int = 30   # G52-1 退避基数（30/60/120 秒；第 n 次延迟 = base*n）
    watchdog_enabled: bool = False     # G52-3 看门狗开关（续跑清单非空且未正常完成 → 自动续跑）
    watchdog_interval_min: int = 5     # G52-3 看门狗间隔（1/5/15/30 分钟）
    watchdog_silent: bool = False      # G52-3 看门狗静默续跑（不弹提示窗，仅日志）
    # ---- G53 数据洞察（评分卡 / 告警 / 历史保留 / 定时报表）
    g53_alerts_enabled: bool = False          # G53-2 阈值告警开关
    g53_alerts_interval_min: int = 60        # G53-2 告警扫描间隔（分钟）
    g53_alerts_stale_days: int = 30          # G53-2 陈旧阈值（天）
    g53_alerts_disk_min_gb: int = 5          # G53-2 磁盘水位阈值（GB）
    history_retention_days: int = 90         # G53-4 历史保留天数（默认 90）
    sched_report_enabled: bool = False       # G53-5 定时报表开关
    sched_report_interval_min: int = 1440    # G53-5 定时报表间隔（分钟）
    sched_report_dir: str = ""               # G53-5 报表输出目录
    sched_report_format: str = "csv"         # G53-5 报表格式 csv/md
    alert_im_channel: str = ""               # G59-7 企业 IM 告警渠道（dingtalk/feishu/wecom；空=关）
    alert_im_url: str = ""                   # G59-7 企业 IM webhook URL（空=关）
    alert_im_token: str = ""                 # G59-7 企业 IM webhook Token（可选；落盘前加密）
    # G71-9：GUI 内置本地只读 API（含 SSE 事件流）。默认关；仅回环绑定。
    # 独立 `--serve` 进程看不到 GUI 引擎的事件，因此事件流需要由 GUI 自己托管。
    local_api_enabled: bool = False
    local_api_port: int = 8765


_DEFAULTS: dict = asdict(Settings())


class SettingsStore:
    """线程安全、原子写、损坏兜底、**跨进程互斥** 的 settings.json 封装。

    G67-10：原实现只有 `threading.RLock` —— 只保护同进程。多开实例（或看门狗/
    CLI 与服务进程并存）时两个进程的「读-改-写」会交错，后写者拿旧快照整份覆盖，
    表现为「刚改的设置没生效」「token 偶发变回旧值」。现读写都在
    `cross_process_lock` 临界区内完成（进程锁失败会降级并写入 app.log 留痕）。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # G84-8：解密失败的字段 → 磁盘上的原始密文。
        # 为什么要记住它：`_decrypt_token` 对损坏密文返回空串（避免把垃圾当凭据用，
        # 是有意的）；但如果 load()→save() 走一趟（用户只是改了个并发数），
        # 空串会被**当成新值加密落盘**，用户原本还能恢复的密文就此永久消失。
        # 记下来，save() 时对「明文为空且磁盘上是有效密文」的字段保留原密文。
        self._undecryptable: dict[str, str] = {}

    def forget_undecryptable(self, field: str | None = None) -> None:
        """丢弃"密文解不开"的记账，让该字段下次保存时**可以真的写成空**。

        为什么需要这个口子（R9 / 审查发现）：记账保护的本意是"别把用户还能救的
        密文覆盖成空"，但它同时意味着**解不开的凭据永远删不掉**（用户清空 token
        后又被原密文写回）。上层在收到用户**显式清除**的动作时调用本方法即可。
        `field=None` 表示清空全部记账。
        """
        if field is None:
            self._undecryptable.clear()
        else:
            self._undecryptable.pop(field, None)

    def undecryptable_fields(self) -> tuple[str, ...]:
        """当前有哪些字段"密文解不开"（供 UI/诊断如实提示，而不是假装没这回事）。"""
        return tuple(sorted(self._undecryptable))

    def _decrypt_tracked(self, field: str, raw: str) -> str:
        """解密并**记账**：解不出来但磁盘上是有效密文时，记住原值以便 save() 保回。

        与 `_decrypt_token` 的差别只在记账，不改语义（掩码/损坏仍返回空串）。
        """
        out = _decrypt_token(raw)
        if out or not raw:
            # B6（复审 7.1 P2-1）：解密成功 = 该字段已被用户修复或本来就好 ——
            # 陈旧记账必须清掉，否则「解不开过→修好→再清空」时旧密文被复活。
            if out:
                self._undecryptable.pop(field, None)
            return out
        if raw.startswith(_ENC_PREFIX):        # 有效密文却解不开（换机器/后端缺失）
            self._undecryptable[field] = raw
            try:
                from ..app.applog import warn as _warn
                _warn(f"设置项 {field} 的密文无法解密（可能换了机器或系统密钥变更）——"
                      f"本次沿用磁盘原值，不会覆盖；如需重填请在设置页重新输入")
            except Exception:
                pass
        return out

    def load(self) -> Settings:
        with self._lock, cross_process_lock(self.path, label="settings.json"):
            raw: dict = {}
            if self.path.exists():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        raw = data
                except Exception:
                    # 主文件损坏 → 尝试从 .bak 恢复，仍失败则兜底默认值不崩
                    raw = self._try_restore_backup()
            # 只取已知字段的类型安全合并。必须**深拷贝**：`dict(_DEFAULTS)` 浅拷贝
            # 让嵌套 dict（ui_state / host_tokens）与模块级默认值共享同一对象，
            # 第一个实例就地改写 `ui_state` 后，之后所有新实例的「默认值」都被污染。
            merged = copy.deepcopy(_DEFAULTS)
            for k in _DEFAULTS:
                if k in raw:
                    try:
                        merged[k] = _coerce(k, raw[k])
                    except Exception:
                        pass
            s = Settings(**merged)
            # G06-1：token 读回解密（掩码/损坏 → 空串，不崩）
            s.token = self._decrypt_tracked("token", s.token)
            # G38-1：host_tokens 逐条目解密（损坏条目→空，不崩）
            ht = getattr(s, "host_tokens", None) or {}
            if isinstance(ht, dict):
                s.host_tokens = {str(k): self._decrypt_tracked(f"host_tokens.{k}", str(v))
                             for k, v in ht.items()}
            # G49-7：Webhook token 读回解密（掩码/损坏 → 空串）
            nwt = getattr(s, "notify_webhook_token", "") or ""
            s.notify_webhook_token = self._decrypt_tracked("notify_webhook_token", str(nwt))
            # G59-7：企业 IM webhook token 读回解密
            ait = getattr(s, "alert_im_token", "") or ""
            s.alert_im_token = self._decrypt_tracked("alert_im_token", str(ait))
            # G58-3：HTTP 代理串可能内嵌 `user:pass@` —— 读回解密；
            # 历史明文**原样保留**（否则升级会把用户代理设置静默清空）。
            # B6（复审 7.1 P2-1）：改走 `_decrypt_tracked` 记账链路 —— 原来的
            # `_decrypt_or_keep` 对解不开的密文静默返回空且不记账，换机器场景
            # 一次 save() 就把 proxy 密文覆盖成空（G84-8 要防的事，唯独 proxy 漏了）。
            _raw_proxy = str(getattr(s, "proxy", "") or "")
            _dec_proxy = self._decrypt_tracked("proxy", _raw_proxy)
            if _dec_proxy or not _raw_proxy:
                s.proxy = _dec_proxy
            elif not _raw_proxy.startswith(_ENC_PREFIX):
                s.proxy = _raw_proxy      # 历史明文原样保留
            else:
                s.proxy = ""              # 密文解不开：空值 + 已记账，save 保回
            # G80-B5：声明期取值校验（越界纠正，不抛异常，兼容旧配置脏值）。
            # 放在**读回的最后一步**：无论来自 UI、手改 JSON 还是旧版本，都过同一道闸。
            corrected = normalize_settings(s)
            if corrected:
                from ..app.applog import warn as _warn
                _warn(f"设置项越界已纠正：{', '.join(corrected)}")
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

    def update(self, **patch) -> None:
        """锁内读-改-写：只改 `patch` 里的字段，其余字段保留磁盘现值。

        Rev16 C1（changelog 9.18.9 假闭环的补齐）：此前 UI 各回调走
        `save(旧快照)` —— 两个实例同时开着时，「随手拖一下列宽」会把另一个
        实例刚改的并发数/凭据回滚成旧值。`update()` 在同一个跨进程临界区内
        重读磁盘，只覆盖显式给出的字段，多写者不再互相踩。
        加密字段（token/host_tokens/proxy/webhook/IM）传**明文**进来即可，
        走与 `save()` 同一套落盘加密；未提及的字段密文原样保留。
        """
        if not patch:
            return
        with self._lock, cross_process_lock(self.path, label="settings.json"):
            s = self.load()
            for k, v in patch.items():
                if not hasattr(s, k):
                    raise ValueError(f"unknown settings field: {k}")
                # B6（复审 7.1 P2-2）：dict 字段做**浅合并** —— `ui_state` 内含
                # manage_columns / settings_split 等独立 key，多开时整值替换会让
                # 另一实例刚写的 key 静默丢失（列宽保存是最频繁的写路径）。
                # 例外：`host_tokens` 保持整值替换 —— 删掉某个 host 行 = 明确要删，
                # 合并会把用户删掉的 key 又塞回去（R9 语义）。
                cur = getattr(s, k)
                if (k != "host_tokens" and isinstance(v, dict)
                        and isinstance(cur, dict)):
                    merged = dict(cur)
                    merged.update(v)
                    v = merged
                setattr(s, k, v)
            normalize_settings(s)
            self._save_locked(s)

    def save(self, s: Settings) -> None:
        """原子写 settings.json；token 加密落盘。每次保存前把当前文件备份到 .bak。

        G67-10：整个「备份 → 写 tmp → os.replace」都在跨进程临界区内完成 ——
        否则两个实例并发保存时，各自基于旧内容写 .bak / 覆盖主文件，仍会丢更新。

        语义约定（Rev16 复审后确立）：`save()` 的调用契约是「调用方刚改过
        这个快照并要求整份生效」—— 多字段联动保存（设置页 40+ 回调）与
        凭据显式清除（`forget_undecryptable` 后清空）都依赖它原样落盘，
        **不做**磁盘合并（启发式会拦住合法的清空操作）。只想改个别字段、
        不确定快照新旧的多写者场景（列宽/分割比例等 UI 工作区写入点）
        用 `update()`。
        """
        with self._lock, cross_process_lock(self.path, label="settings.json"):
            self._save_locked(s)

    def _save_locked(self, s: Settings) -> None:
        """写盘主体（调用方必须已持有 `_lock` + 文件锁）。"""
        payload = asdict(s)
        # G06-1：token 落盘前加密（绝不明文）
        if payload.get("token"):
            payload["token"] = _encrypt_token(payload["token"])
        # G38-1：host_tokens 逐条目加密（杜绝任一 host 凭据明文落盘）
        ht = payload.get("host_tokens") or {}
        if isinstance(ht, dict):
            ht = {str(k): _encrypt_token(str(v)) for k, v in ht.items() if str(k)}
            payload["host_tokens"] = ht
        # G49-7：Webhook token 与主 token 同策略加密落盘
        if payload.get("notify_webhook_token"):
            payload["notify_webhook_token"] = _encrypt_token(payload["notify_webhook_token"])
        # G59-7：企业 IM webhook token 同策略加密落盘
        if payload.get("alert_im_token"):
            payload["alert_im_token"] = _encrypt_token(payload["alert_im_token"])
        # G58-3：HTTP 代理串可能内嵌 `user:pass@` —— 与 token 同策略加密落盘。
        # 原实现直接 `asdict` 落盘 → 代理口令**明文**留在 settings.json，
        # 与「凭据绝不明文落盘」的项目红线冲突（token/host_tokens/webhook/IM
        # 都已加密，唯独 proxy 漏了）。
        if payload.get("proxy"):
            payload["proxy"] = _encrypt_token(payload["proxy"])
        # G84-8：**解密失败的密文必须原样保回**（本函数末尾之前的最后一步，
        # 因为上面各步会对值做 `_encrypt_token`，而它不幂等 —— 对已是密文的值
        # 再加密一次就彻底废了）。触发场景：换机器/系统密钥变更/DPAPI 后端缺失
        # → `load()` 把该字段读成空串 → 用户只是改了个并发数 → save() 把空串
        # 加密落盘 → 用户原本还能手工救回的密文永久消失。
        _stale: list[str] = []
        for _field, _cipher in self._undecryptable.items():
            if _field.startswith("host_tokens."):
                _key = _field.split(".", 1)[1]
                _ht = payload.get("host_tokens") or {}
                # R9（审查发现）：只有**该 host 仍存在且值为空**才保留原密文。
                # 用户在「按平台凭据」里整行删掉某个 host = 明确要删除它，
                # 原实现会把这个 key 又塞回去（等于删不掉）。
                if isinstance(_ht, dict) and _key in _ht and not str(_ht.get(_key) or ""):
                    _ht[_key] = _cipher
                    payload["host_tokens"] = _ht
                else:
                    # B6（复审 P2-1）：host 被删除或重填了有效值 → 记账使命完成
                    _stale.append(_field)
            elif not str(payload.get(_field) or ""):
                payload[_field] = _cipher
            else:
                # B6（复审 P2-1）：字段已有**有效新值**落盘 —— 旧密文不再需要
                # 保回，清掉记账，否则之后 update(field="") 显式清空时旧密文
                # 会被「保回逻辑」复活。
                _stale.append(_field)
        for _f in _stale:
            self._undecryptable.pop(_f, None)
        self._atomic_write_payload(payload)

    def _atomic_write_payload(self, payload: dict) -> None:
        """落盘尾段（B6 从 `_save_locked` 抽出以守 100 行函数预算）。

        备份 → 唯一 tmp + flush/fsync → os.replace（含 PermissionError 退避）。
        调用方必须已持有 `_lock` + 文件锁。
        """
        # G67-23：tmp 文件名**必须含 pid+线程 id**。
        # 原实现固定用 `settings.json.tmp`：同进程多线程并发保存时，两个线程
        # 会同时 open 同一个 tmp 路径 —— 后开者截断前者的内容，先完成者
        # `os.replace` 走了 tmp，后完成者就撞上 `PermissionError [WinError 5]`
        # （Windows 不允许替换/删除仍被占用的文件）。
        # 实测：8 线程 x 全速 churn 时 3 次失败 —— 不丢已有数据（原子替换本身安全），
        # 但那一次保存被**静默丢弃**（save 只抛给调用方，UI 层没兜住），
        # 用户表现为「刚改的设置没保存」。
        tmp = self.path.with_name(
            f"{self.path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        # G84-9：写完必须 flush+fsync **再** replace。
        # 原来只有 `write_text` → `os.replace`：`replace` 只保证「目录项原子」，
        # 不保证「内容已经落盘」。掉电/蓝屏时可能出现目录项已指向新 inode、
        # 但文件内容仍是零长度的情形 —— 而 settings.json 里装着全部凭据，
        # 这一次丢失等于全丢。这里补上 `atomic_io` 早就有的那半个语义。
        _write_text_synced(
            tmp, json.dumps(payload, ensure_ascii=False, indent=2))
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
                _write_bytes_synced(bak, old)
            except Exception:
                pass
        # G67-23：`os.replace` 在 Windows 上会因**目标文件被他人打开读取**而报
        # `PermissionError [WinError 5]`（读写共享冲突）—— 即使 tmp 名唯一、
        # 且我们持有跨进程写锁，同进程的读者仍可能恰好持有目标句柄。
        # 因此做有限次退避重试；仍失败才上抛（不静默吞掉，那是上一版的另一个坑）。
        last_err: Exception | None = None
        for attempt in range(_REPLACE_RETRIES):
            try:
                os.replace(tmp, self.path)  # 原子写
                last_err = None
                break
            except PermissionError as e:
                last_err = e
                time.sleep(_REPLACE_BACKOFF_SEC * (attempt + 1))
        if last_err is not None:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise last_err


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
    if isinstance(default, dict):
        # 字典字段（G38-1 host_tokens）：非 dict → 空 dict
        if not isinstance(val, dict):
            return {}
        return {str(k): v for k, v in val.items()}
    if isinstance(default, (tuple, list)):
        # 序列字段（G35-7 quick_repos）：list/tuple 互通，逐项转 str
        if not isinstance(val, (tuple, list)):
            return default
        return tuple(str(v) for v in val)
    return val
