"""G68-3 Windows 保留设备名守卫：`sanitize_name` 必须避开 CON/PRN/AUX/NUL/COM1-9/LPT1-9。

## 为什么需要这个文件（实测，不是推测）
`_INVALID_DIR_CHARS` 只过滤了 `\\ / : * ? " < > |` 与控制字符，**没管 Windows 保留设备名**。
保留名不能作为文件或目录名，`open()` / `mkdir()` / `git archive --output` 全部失败，
而且报错**毫无指向性**。

复现（仓库名字段直接来自 URL 末段，用户可控）：
```
git archive --format=zip --output archives/CON.zip HEAD
→ rc=128  fatal: could not open '.../CON.zip' for writing: No such file or directory
```
真实路径：用户粘贴 `https://github.com/foo/CON` → 点「归档」→
`main_window` 用 `sanitize_name(url 末段)` 拼 `archives/CON.zip` → 必然失败。
**克隆不受影响**（目录名是 `owner__repo`，永不等于设备名），所以这个 bug 只在归档时暴露，
而错误提示会让人以为是网络/权限问题。

修复：`sanitize_name` 对保留名追加 `_`（`CON` → `CON_`）。
判定与 Windows 一致 —— 比较**第一个点之前**的整段，所以 `CON.txt` 同样要处理。
"""
from __future__ import annotations

import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app.url_lib import sanitize_name  # noqa: E402

RESERVED = ["CON", "PRN", "AUX", "NUL",
            *(f"COM{i}" for i in range(1, 10)),
            *(f"LPT{i}" for i in range(1, 10))]
# 不应被误伤的对照（这些不是保留名）
NOT_RESERVED = ["COM10", "LPT0", "CONSOLE", "CON_", "NULS", "com0", "aux2"]


class TestReservedDeviceNamesAreNeutralized(unittest.TestCase):
    def test_reserved_names_get_suffix(self):
        bad = []
        for n in RESERVED:
            for casing in (n, n.lower(), n.title()):
                got = sanitize_name(casing)
                if got.split(".")[0].upper() in {x.upper() for x in RESERVED}:
                    bad.append(f"{casing!r} → {got!r}")
        self.assertEqual(bad, [], "这些名字仍是保留设备名（Windows 上无法建文件）：\n  "
                                  + "\n  ".join(bad))

    def test_reserved_with_extension_also_neutralized(self):
        """`CON.txt` 的第一个点之前仍是 `CON` —— Windows 同样拒收。

        **这条断言的价值在于我第一版修复是错的**：当时把 `_` 追加到**末尾**，
        `CON.txt` → `CON.txt_`，点前段还是 `CON`。用真实 git archive 实测：
        `CON.txt_.zip` **仍然 rc=128 失败**，而 `CON_.txt.zip` 才是 rc=0。
        所以必须在**第一个点之前的那一段**上加后缀。
        """
        for n in ("CON.txt", "nul.log", "COM1.tar.gz"):
            got = sanitize_name(n)
            head = got.split(".")[0]
            self.assertNotIn(head.upper(), {x.upper() for x in RESERVED},
                             f"{n!r} → {got!r}：点前段 {head!r} 仍是保留名")
            self.assertEqual(got.split(".")[-1], n.split(".")[-1],
                             f"{n!r} → {got!r}：扩展名不该被改动")
            # 后缀必须**插在点前段之后**，其余原样保留（CON.txt → CON_.txt）
            original_head = n.split(".")[0]
            self.assertEqual(got, f"{original_head}_{n[len(original_head):]}",
                             f"{n!r} → {got!r}：下划线位置不对（应紧跟点前段）")

    def test_normal_names_are_not_touched(self):
        """防误伤：不是保留名的名字必须**原样**返回（这是「不制造问题」的底线）。"""
        bad = []
        for n in NOT_RESERVED:
            got = sanitize_name(n)
            if got != n:
                bad.append(f"{n!r} → {got!r}（不该改）")
        self.assertEqual(bad, [], "误伤了合法名字：\n  " + "\n  ".join(bad))

    def test_existing_sanitize_contract_unchanged(self):
        """原有契约不能被破坏（既有测试也在守，这里给出集中断言）。"""
        self.assertEqual(sanitize_name("a/b:c*?"), "a_b_c__")
        self.assertEqual(sanitize_name("..."), "repo")
        self.assertEqual(sanitize_name("   "), "repo")
        self.assertEqual(sanitize_name("a\x00b"), "a_b")


class TestArchivePathIsActuallyWritable(unittest.TestCase):
    """不只看字符串 —— 用**真实 git archive** 验证归档目标路径可写。

    这是把「修好了」钉在真实文件系统行为上，而不是钉在「返回值看起来对」。
    """

    def _make_repo(self, base: pathlib.Path) -> pathlib.Path:
        work = base / "seed"
        work.mkdir()

        def sh(*a, check=True):
            return subprocess.run(a, cwd=work, check=check, capture_output=True,
                                  text=True, encoding="utf-8", errors="replace")

        sh("git", "init", "-q")
        sh("git", "config", "user.email", "a@b.c")
        sh("git", "config", "user.name", "t")
        (work / "f.txt").write_text("x", encoding="utf-8")
        sh("git", "add", ".")
        sh("git", "commit", "-qm", "i")
        return work

    @unittest.skipUnless(os.name == "nt", "保留设备名是 Windows 特有的")
    def test_reserved_repo_name_can_be_archived(self):
        base = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g683_"))
        work = self._make_repo(base)
        arch = base / "archives"
        arch.mkdir()
        failures = []
        for seg in ("CON", "NUL", "COM1", "LPT1", "PRN", "AUX", "normal-repo"):
            dest = arch / f"{sanitize_name(seg)}.zip"
            r = subprocess.run(
                ["git", "archive", "--format=zip", "--output", str(dest), "HEAD"],
                cwd=work, capture_output=True, text=True,
                encoding="utf-8", errors="replace")
            if r.returncode != 0 or not dest.exists() or dest.stat().st_size == 0:
                failures.append(f"{seg} → {dest.name}: rc={r.returncode} {(r.stderr or '').strip()[-100:]}")
        self.assertEqual(failures, [], "归档目标不可写：\n  " + "\n  ".join(failures))

    def test_simulated_archive_name_for_reserved_repo_is_writable(self):
        """跨平台兜底：至少在文件系统层面能创建这些名字。

        ⚠️ **这个断言在本机是弱证据，不要把它当主力**：Python 3.14 在 Windows 上
        对多数路径使用 `\\\\?\\` 前缀，**绕过**了 DOS 设备名解析 ——
        实测 `open("CON.txt_")` 在本机成功。真正会失败的是 `git archive`、`mkdir()`
        这类走普通 Win32 路径的调用，因此主力守卫是上面那条 `git archive` 测试。
        保留本条是为了在别的平台/Python 版本上仍有一层网。
        """
        d = pathlib.Path(tempfile.mkdtemp(prefix="gcm_g683_w_"))
        bad = []
        for seg in ("CON", "NUL", "COM1", "LPT1"):
            p = d / f"{sanitize_name(seg)}.zip"
            try:
                p.write_bytes(b"x")
            except Exception as e:  # noqa: BLE001
                bad.append(f"{seg} → {p.name}: {type(e).__name__}: {e}")
        self.assertEqual(bad, [], "这个平台上仍不可写：\n  " + "\n  ".join(bad))
        # 目录名同样要能建（克隆落盘走的是 mkdir）
        for seg in ("CON", "NUL", "COM1"):
            try:
                (d / sanitize_name(seg)).mkdir()
            except Exception as e:  # noqa: BLE001
                bad.append(f"mkdir {seg} → {sanitize_name(seg)}: {type(e).__name__}: {e}")
        self.assertEqual(bad, [], "目录名不可写：\n  " + "\n  ".join(bad))


if __name__ == "__main__":
    unittest.main()
