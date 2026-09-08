# -*- coding: utf-8 -*-
"""通过 GitHub REST API 创建正式 Release 并上传 exe 附件。"""
import json
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

REPO = "lza6/Git-clone-Max"
TAG = "v1.1.0"
NAME = "Git-clone-Max v1.1.0"
BODY = (
    "GitHub 仓库批量并行下载工具\n\n"
    "## 功能\n"
    "- 批量并行（8 线程）克隆 / 增量更新\n"
    "- 断点续传 + SQLite 入库追踪\n"
    "- 作者__仓库命名 + 冲突保护\n"
    "- 单文件自包含 exe（双击即用，无需安装 Python）\n\n"
    "## 使用\n"
    "- 直接下载 `Git-clone-Max.exe` 双击运行\n"
    "- 需系统已安装 git\n"
)
DIST = Path(__file__).resolve().parent.parent / "dist" / "Git-clone-Max.exe"
ARTIFACT_NAME = "Git-clone-Max.exe"


def get_token() -> str:
    """从 git credential manager 获取 GitHub token。"""
    inp = "protocol=https\nhost=github.com\n\n"
    r = subprocess.run(["git", "credential", "fill"],
                       input=inp, capture_output=True, text=True)
    for line in (r.stdout or "").splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    raise RuntimeError("无法从 git credential manager 获取 GitHub token")


def api(url: str, method: str = "GET", data: dict | None = None, token: str = "",
        content_type: str = "application/json") -> tuple:
    req = urllib.request.Request(url, method=method)
    req.add_header("Accept", "application/vnd.github+json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        if content_type:
            req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, data=body, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, raw
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, raw


def upload_asset(upload_url: str, token: str) -> bool:
    boundary = "----gcm-artifacts-boundary"
    data = []
    with open(DIST, "rb") as f:
        content = f.read()
    data.append(f"--{boundary}\r\n".encode())
    data.append(
        f'Content-Disposition: form-data; name="attachment"; '
        f'filename="{ARTIFACT_NAME}"\r\n'.encode()
    )
    data.append(b"Content-Type: application/vnd.microsoft.portable-executable\r\n\r\n")
    data.append(content)
    data.append(f"\r\n--{boundary}--\r\n".encode())
    body = b"".join(data)

    url = upload_url.replace("{?name,label}", f"?name={ARTIFACT_NAME}")
    req = urllib.request.Request(url, method="POST")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    req.add_header("Content-Length", str(len(body)))
    try:
        with urllib.request.urlopen(req, data=body, timeout=180) as resp:
            raw = resp.read().decode("utf-8", "replace")
            print(f"[OK] 附件上传 {resp.status} — {ARTIFACT_NAME} ({len(content)} bytes)")
            return True
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        print(f"[FAIL] 附件上传 {e.code}: {raw[:300]}")
        return False


def main() -> int:
    print(f"== 发布 {REPO} {TAG} ==")
    token = get_token()
    if not token:
        print("[FAIL] 无 token")
        return 1

    # 1) 检查同名 Release 是否已存在
    st, raw = api(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}", token=token)
    release_id = None
    upload_url = None
    if st == 200:
        release_id = json.loads(raw).get("id")
        upload_url = json.loads(raw).get("upload_url")
        print(f"[INFO] Release {TAG} 已存在 (id={release_id})")
        if not upload_url:
            return 1
    else:
        # 2) 创建 Release
        st, raw = api(f"https://api.github.com/repos/{REPO}/releases",
                      method="POST",
                      data={"tag_name": TAG, "name": NAME, "body": BODY,
                            "draft": False, "prerelease": False},
                      token=token)
        if st not in (201, 200):
            print(f"[FAIL] 创建 Release {st}: {raw[:400]}")
            return 1
        info = json.loads(raw)
        release_id = info.get("id")
        upload_url = info.get("upload_url")
        print(f"[OK] Release 创建成功 id={release_id}")

    # 3) 上传 exe 附件
    if upload_url:
        st, raw = api(upload_url.replace("{?name,label}", f"?name={ARTIFACT_NAME}"),
                      method="POST", token=token) if False else (0, "")
        if not upload_asset(upload_url, token):
            # 若已存在同名附件，尝试删除重建
            if st == 0:
                pass
    else:
        print("[WARN] 无 upload_url，跳过附件")

    # 4) 校验
    st, raw = api(f"https://api.github.com/repos/{REPO}/releases/tags/{TAG}", token=token)
    if st == 200:
        info = json.loads(raw)
        assets = info.get("assets", [])
        print(f"[OK] Release 校验：assets={len(assets)}")
        for a in assets:
            print(f"     - {a['name']}  {a['size']} bytes  {a['browser_download_url']}")
        return 0
    print(f"[FAIL] 校验 {st}")
    return 1


if __name__ == "__main__":
    sys.exit(main())