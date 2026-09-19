"""正式 Release 发布脚本（PyGithub）。

用法：
    python scripts/publish_release.py --dry-run      # 校验本地产物 + 远端状态，不上传
    python scripts/publish_release.py --tag v2.0.0   # 打 tag 并创建 Release 上传 exe + portable zip

依赖 requirements-build.txt（PyGithub）。token 走 git credential manager。
幂等：同名 tag 的 Release 存在时更新其附件；同名附件先删除再上传。
幂等短路（2026-09）：远端同名附件与本地 size 一致时直接 SKIP 返回，绝不动远端。
网络健壮性（2026-09）：每个网络阶段逐步进度输出 + flush；网络调用失败重试 2 次（间隔 3s）；
整体运行 300s 兜底，超时 [FAIL] 运行超时 返回 1，不无限挂。
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from github import Github as Github  # noqa: F401  PyGithub（供 main 内引用，勿删）
# G50-3 控制台编码兑底：窄编码终端（cp1252/GBK）下中文输出不再抛 UnicodeEncodeError
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

REPO = "lza6/Git-clone-Max"
DIST = ROOT / "dist" / "Git-clone-Max.exe"
ARTIFACT_NAME = "Git-clone-Max.exe"
# G23-7 onedir 便携版 zip（第二产物）
PORTABLE_ZIP = ROOT / "dist" / "Git-clone-Max-portable.zip"
PORTABLE_NAME = "Git-clone-Max-portable.zip"


def _build_body() -> str:
    """G45-4：读取 docs/RELEASE_BODY.md 模板并替换占位符生成 Release body。

    占位符：{version}、{sha256_exe}、{sha256_zip}（缺失/读取失败置空，不阻断发布）。
    BODY 常量保留并指向同一模板结果，兼容旧测试对 pr.BODY 的引用。
    """
    from gcm import __version__  # 脚本入口已把 ROOT 加入 sys.path

    def _sum(path: Path) -> str:
        try:
            if path.is_file():
                return hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception:
            pass
        return ""

    template_path = ROOT / "docs" / "RELEASE_BODY.md"
    try:
        template = template_path.read_text(encoding="utf-8")
    except Exception as e:
        raise RuntimeError(
            f"无法读取 Release body 模板 {template_path}：{e}") from e
    return (
        template
        .replace("{version}", str(__version__))
        .replace("{sha256_exe}", _sum(DIST))
        .replace("{sha256_zip}", _sum(PORTABLE_ZIP))
    )


# 兼容旧引用：动态模板（含当前版本号），等价于 _build_body()。
BODY = _build_body()

# 整体运行时长兜底（秒）：超过则 [FAIL] 运行超时 返回 1，绝不无限挂。
DEADLINE = 300.0
# 每个网络操作的重试次数（首次 + NET_RETRIES 次重试 = 3 次尝试），间隔 3 秒。
NET_RETRIES = 2
NET_BACKOFF = 3.0


class ReleaseNotFound(Exception):
    """Release 不存在（404）——不重试，直接走"创建"路径。"""


def _git(*args: str) -> str:
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def _get_token() -> str:
    r = subprocess.run(["git", "credential", "fill"],
                       input="protocol=https\nhost=github.com\n\n",
                       capture_output=True, text=True)
    for line in (r.stdout or "").splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    raise RuntimeError("无法从 git credential manager 获取 GitHub token")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _call(label: str, started: float, fn):
    """以 label 为进度提示，带整体超时兜底 + 重试地调用一个网络操作。

    - 每次尝试前检查整体运行时长，超过 DEADLINE 直接抛运行超时（不重试）；
    - ReleaseNotFound 不重试，立即向上抛（走"创建"路径）；
    - 其余异常重试 NET_RETRIES 次（间隔 NET_BACKOFF 秒）；
    - 重试耗尽后按"是否已超时"打印 [FAIL] 并抛出。
    """
    have = [False]

    def pre() -> None:
        if time.monotonic() - started >= DEADLINE:
            raise RuntimeError("运行超时")
        if not have[0]:
            print(f"[NET] {label} …", flush=True)
            have[0] = True
        else:
            print(f"[NET] {label} 重试…", flush=True)

    last = None
    for _attempt in range(NET_RETRIES + 1):  # 首次 + NET_RETRIES 次重试
        try:
            pre()
            result = fn()
            print(f"[OK] {label} 完成", flush=True)
            return result
        except ReleaseNotFound:
            raise
        except RuntimeError as e:
            if str(e) == "运行超时":
                print(f"[FAIL] 运行超时（>= {int(DEADLINE)}s）", flush=True)
                raise
            last = e
        except Exception as e:
            last = e
        if _attempt < NET_RETRIES:
            time.sleep(NET_BACKOFF)
    # 重试耗尽
    if time.monotonic() - started >= DEADLINE:
        print(f"[FAIL] 运行超时（>= {int(DEADLINE)}s）", flush=True)
        raise RuntimeError("运行超时")
    print(f"[FAIL] {label} 失败：{last}", flush=True)
    raise last


def _upload_portable(rel, started: float) -> None:
    """上传便携版 zip（G23-7 第二产物）；同名已存在则先删再传。"""
    for a in rel.get_assets():
        if a.name == PORTABLE_NAME:
            try:
                a.delete_asset()
                print(f"[INFO] 已删除旧便携包 {PORTABLE_NAME}")
            except Exception:
                pass
    psize = PORTABLE_ZIP.stat().st_size
    def _upz(**kw):
        return rel.upload_asset(
            str(PORTABLE_ZIP),
            content_type="application/zip",
            **kw)
    kwargs = {}
    try:
        if "timeout" in inspect.signature(rel.upload_asset).parameters:
            kwargs["timeout"] = 120
    except (TypeError, ValueError):
        pass
    asset = _call("上传 " + PORTABLE_NAME + "（" + str(psize) + " bytes）",
                  started, lambda: _upz(**kwargs))
    print(f"[OK] 便携包上传成功 id={asset.id} size={asset.size}")


def _validate_version(tag: str, version: str, changelog) -> str | None:
    """G45-8：tag 与 gcm.__version__/__changelog__ 一致性校验（不触网）。

    - tag（未传时默认 v+version）必须等于 v+version.lstrip("v")，否则 [FAIL]；
    - __changelog__（gcm/__init__.py 元组）必须存在一个以 "version:" 前缀的条目
      （所有条目均按 "X.Y.Z: 描述" 书写；find 前缀匹配同时兼容元组 旧→新 / 新→旧
      两种排列，避免"包含但非本版条目"误判）；
    - 失败返回 None（调用方 return 1），成功返回规范化 tag。
    """
    expected_tag = "v" + str(version).lstrip("v")
    if not tag:
        tag = expected_tag
    if tag != expected_tag:
        print(f"[FAIL] tag 不一致：传入 {tag!r}，期望 {expected_tag!r}（gcm.__version__={version!r}）")
        return None
    matched = [e for e in changelog if e.startswith(str(version) + ":")]
    if not matched:
        latest = changelog[-1] if changelog else ""
        print(f"[FAIL] __changelog__ 缺少版本 {version} 条目（最新：{latest[:60]!r}...）")
        return None
    return expected_tag


def _ensure_remote_tag(repo, tag: str, commit_sha: str | None = None) -> None:
    """幂等补建远端 git tag：Release 存在但 refs/tags/<tag> 缺失时补建（实测 v8.0.0 出现该情况）。

    - 查询 ref：存在 → 不动；404 → 创建；其它异常 → [WARN] 跳过不阻断发布。
    - 创建时 sha 优先用传入的 commit_sha（Release target_commitish），
      缺失则回退默认分支 HEAD；422（竞态已存在）→ 视为成功。
    """
    ref = "refs/tags/" + tag.split("/")[-1]  # GitHub tag 名不含斜杠
    from github import GithubException
    try:
        repo.get_git_ref(ref)
        print(f"[INFO] 远端 tag {ref} 已存在，无需补建")
        return
    except GithubException as e:
        if e.status != 404:
            print(f"[WARN] 查询远端 tag {ref} 失败（status={e.status}），跳过补建")
            return
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 查询远端 tag {ref} 失败：{e}，跳过补建")
        return
    sha = commit_sha
    if not sha:
        try:
            sha = repo.get_branch(repo.default_branch).commit.sha
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] 无法解析默认分支 HEAD 补建 tag {ref}：{e}，跳过")
            return
    try:
        repo.create_git_ref(ref=ref, sha=sha)
        print(f"[OK] 已补建远端 tag {ref} -> {sha[:7]}")
    except GithubException as e:
        if e.status == 422:
            print(f"[OK] 远端 tag {ref} 恰已存在（竞态），视为成功")
        else:
            print(f"[WARN] 补建远端 tag {ref} 失败（status={e.status}），跳过")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 补建远端 tag {ref} 失败：{e}")


def main() -> int:
    ap = argparse.ArgumentParser(description="发布 Git-clone-Max Release")
    ap.add_argument("--dry-run", action="store_true", help="仅校验，不上传")
    ap.add_argument("--tag", default="", help="要发布的 tag，如 v2.0.0")
    args = ap.parse_args()

    started = time.monotonic()

    if not DIST.exists():
        print(f"[FAIL] 产物不存在：{DIST}")
        return 1
    sha = _sha256(DIST)
    size = DIST.stat().st_size
    print(f"[OK] 本地产物 {ARTIFACT_NAME} {size} bytes  sha256={sha[:16]}…")

    # G45-8 版本一致性校验（不触网）：dry-run 与实发都生效，失败直接返回。
    from gcm import __version__, __changelog__  # noqa
    tag = _validate_version(args.tag, str(__version__), __changelog__)
    if tag is None:
        return 1
    print(f"[INFO] tag = {tag}")

    try:
        from github import Auth, Github  # PyGithub
    except ImportError:
        print("[FAIL] 缺少 PyGithub，请先：python -m pip install -r requirements-build.txt")
        return 1
    _auth_cls = Auth
    _github_cls = Github
    try:
        token = _call("解析 token（git credential fill）", started, _get_token)
        print(f"[OK] token 已获取（{len(token)} 字符）")

        # 给整个 PyGithub 客户端一个 120s socket 超时（若版本支持），防单次网络调用无限挂
        gh_kwargs = dict(auth=_auth_cls.Token(token))
        try:
            if "timeout" in inspect.signature(_github_cls.__init__).parameters:
                gh_kwargs["timeout"] = 120
        except (TypeError, ValueError):
            pass
        gh = _github_cls(**gh_kwargs)

        repo = _call("查询仓库 " + REPO, started, lambda: gh.get_repo(REPO))
    except Exception as e:
        print(f"[FAIL] 初始化失败：{e}")
        return 1

    cur = [None]

    def _probe():
        try:
            r = repo.get_release(tag)
            cur[0] = r
            return r
        except ReleaseNotFound:
            raise
        except Exception as e:
            # 判定"不存在"（404）：不重试，走创建路径
            if "404" in str(e) or "Not Found" in str(e) or "not found" in str(e):
                raise ReleaseNotFound(tag) from e
            raise

    try:
        _call("探测 Release " + tag, started, _probe)
        rel = cur[0]
        print(f"[INFO] Release {tag} 已存在 (id={rel.id})，将更新附件")
    except ReleaseNotFound:
        rel = None
    except Exception as e:
        print(f"[FAIL] 探测 Release {tag} 失败：{e}")
        return 1

    # 创建或取已有 Release（非 dry-run 且不存在时）
    if rel is None and not args.dry_run:
        def _mkrel():
            r = repo.create_git_release(
                tag=tag, name=f"Git-clone-Max {tag}", message=_build_body(),
                draft=False, prerelease=False)
            cur[0] = r
            return r

        try:
            _call("创建 Release " + tag, started, _mkrel)
            rel = cur[0]
            print(f"[OK] Release 创建成功 id={rel.id}")
        except Exception as e:
            print(f"[FAIL] 创建 Release 失败：{e}")
            return 1

    # 读取资产列表（存在时；dry-run 与上传路径共用，逐个网络阶段有进度）
    assets = []

    def _read_assets():
        assets[:] = list(rel.get_assets())
        return assets[:]

    if rel is not None:
        try:
            _call("读取 Release " + str(rel.id) + " 的资产列表", started, _read_assets)
        except Exception as e:
            print(f"[FAIL] 读取资产列表失败：{e}")
            return 1

    if args.dry_run:
        pz = PORTABLE_ZIP
        print(f"[DRY] 产物：onefile exe {size} bytes"
              + (f" + portable zip {pz.stat().st_size} bytes" if pz.exists()
                 else "（portable zip 未生成）"))
        if rel is not None:
            print(f"[DRY] 已存在 Release {tag}，assets={[a.name for a in assets]}")
            for a in assets:
                if a.name == ARTIFACT_NAME:
                    print(f"[DRY] 远端 exe size={a.size} 与本地 {size} 对比："
                          f"{'一致' if a.size == size else '不一致'}")
        else:
            print(f"[DRY] Release {tag} 不存在，将新建并上传 {size} bytes")
        _preview = _build_body()
        _plines = _preview.splitlines()
        print("[DRY] Release body 预览（前 8 行）：")
        for _line in _plines[:8]:
            print(f"    | {_line}")
        if len(_plines) > 8:
            print(f"    | ...（共 {len(_plines)} 行，其余省略）")
        print("[DRY] 校验通过，未做任何修改。")
        return 0

    # G-补：保证远端 git tag 存在（Release 已存在但 tag 缺失时幂等补建）
    try:
        _commit = (getattr(rel, "target_commitish", None) or "").strip()
        _call("校验远端 tag " + tag, started,
              lambda: _ensure_remote_tag(repo, tag, commit_sha=_commit or None))
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 远端 tag 校验/补建跳过：{e}")

    # ------------------------- 幂等短路（核心） -------------------------
    # Release 已存在且同名附件也存在、且 asset.size == 本地产物 size → 跳过上传。
    # 此短路必须出现在任何 delete_asset / upload_asset 之前，绝不动远端。
    for a in assets:
        if a.name == ARTIFACT_NAME and a.size == size:
            print(f"[SKIP] 远端已存在一致附件（{a.size} bytes），跳过上传")
            if PORTABLE_ZIP.exists() and not any(x.name == PORTABLE_NAME for x in assets):
                _upload_portable(rel, started)   # 主产物一致仅缺便携包 → 补传便携包
            print(f"[INFO] Release 页面：{rel.html_url}")
            return 0

    # size 不一致或同名附件不存在 → 走"删旧 → 传新"
    for a in assets:
        if a.name != ARTIFACT_NAME:
            continue

        def _del(a=a):
            a.delete_asset()

        try:
            _call("删除旧附件 " + ARTIFACT_NAME, started, _del)
            print(f"[INFO] 已删除旧附件 {ARTIFACT_NAME}")
        except Exception as e:
            print(f"[FAIL] 删除旧附件失败：{e}")
            return 1

    print(f"[INFO] 上传 {ARTIFACT_NAME}（{size} bytes）…")

    def _up(**kw):
        return rel.upload_asset(
            str(DIST),
            content_type="application/vnd.microsoft.portable-executable",
            **kw)

    try:
        kwargs = {}
        try:  # upload_asset 若支持 timeout 参数则传入 120，否则不传
            if "timeout" in inspect.signature(rel.upload_asset).parameters:
                kwargs["timeout"] = 120
        except (TypeError, ValueError):
            pass
        asset = _call("上传 " + ARTIFACT_NAME + "（" + str(size) + " bytes）",
                      started, lambda: _up(**kwargs))
        print(f"[OK] 附件上传成功 id={asset.id} size={asset.size}")
    except Exception as e:
        print(f"[FAIL] 上传失败：{e}")
        return 1

    # G23-7 便携版 zip（第二产物；缺失/未生成不阻塞主产物发布）
    if PORTABLE_ZIP.exists():
        try:
            _upload_portable(rel, started)
        except Exception as e:
            print(f"[WARN] 便携包上传失败（不影响主产物）：{e}")

    # 服务端下载校验（timeout=120）；校验失败仅 [WARN]，不阻塞发布闭环
    # （sha256 一致性最终以人工「独立下载复验」为准，见 docs/RELEASE_CHECKLIST.md）
    server_sha = ""
    try:
        def _download():
            req = urllib.request.Request(
                asset.browser_download_url,
                headers={"Accept": "application/octet-stream"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()

        data = _call("下载校验（" + str(size) + " bytes）", started, _download)
        server_sha = hashlib.sha256(data).hexdigest()
    except Exception as e:
        print(f"[WARN] 服务端下载校验跳过：{e}")
        print(f"[INFO] Release 页面：{rel.html_url}")
        return 0

    ok = server_sha == sha
    print(f"[{'OK' if ok else 'FAIL'}] 服务端 sha256={server_sha[:16]}… "
          f"{'一致' if ok else '不一致!'}")
    print(f"[INFO] Release 页面：{rel.html_url}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
