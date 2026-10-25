"""G80-B7-4 守卫：winget 清单必须由**真实产物**生成（不是手写、不是猜）。"""
from __future__ import annotations

import hashlib
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_script():
    path = ROOT / "scripts" / "gen_winget_manifest.py"
    spec = importlib.util.spec_from_file_location("gen_winget_manifest", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


class TestBuildManifests(unittest.TestCase):
    def setUp(self):
        self.mod = _load_script()

    def test_three_files_with_version_and_sha(self):
        sha = "a" * 64
        files = self.mod.build_manifests("9.10.3", "https://x/Git-clone-Max.exe", sha)
        self.assertEqual(len(files), 3)
        for name, content in files.items():
            self.assertIn("PackageIdentifier: lza6.Git-clone-Max", content, name)
            self.assertIn("PackageVersion: 9.10.3", content, name)
        inst = [v for k, v in files.items() if "installer" in k][0]
        self.assertIn("InstallerSha256: " + sha, inst)
        self.assertIn("InstallerType: portable", inst)
        self.assertIn("Architecture: x64", inst)

    def test_version_prefix_is_stripped(self):
        files = self.mod.build_manifests("v1.2.3", "https://x/a.exe", "b" * 64)
        self.assertIn("PackageVersion: 1.2.3", next(iter(files.values())))

    def test_rejects_bad_version(self):
        with self.assertRaises(ValueError):
            self.mod.build_manifests("9", "https://x/a.exe", "b" * 64)

    def test_rejects_bad_sha(self):
        with self.assertRaises(ValueError):
            self.mod.build_manifests("9.10.3", "https://x/a.exe", "not-a-sha")

    def test_check_mode_detects_drift_and_match(self):
        with tempfile.TemporaryDirectory() as td:
            exe = Path(td) / "Git-clone-Max.exe"
            exe.write_bytes(b"fake installer")
            out = Path(td) / "winget"
            # 先生成 → check 应通过
            self.assertEqual(self.mod.main(["--out", str(out), "--exe", str(exe)]), 0)
            self.assertEqual(self.mod.main(["--out", str(out), "--exe", str(exe), "--check"]), 0)
            # 换内容 → check 必须失败（sha256 变了）
            exe.write_bytes(b"tampered installer")
            self.assertEqual(self.mod.main(["--out", str(out), "--exe", str(exe), "--check"]), 1)

    def test_missing_artifact_returns_2(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(self.mod.main(["--out", td, "--exe", str(Path(td) / "nope.exe")]), 2)


class TestCommittedManifestsMatchCurrentArtifact(unittest.TestCase):
    def test_committed_manifest_matches_dist_exe(self):
        """已入库的清单必须与 dist 里的 exe 一致（防止发版时忘了重新生成）。"""
        exe = ROOT / "dist" / "Git-clone-Max.exe"
        manifest = ROOT / "packaging" / "winget" / "lza6.Git-clone-Max.installer.yaml"
        if not exe.is_file() or not manifest.is_file():
            self.skipTest("缺 dist 产物或清单（打包环境才具备）")
        h = hashlib.sha256()
        with open(exe, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        self.assertIn(h.hexdigest(), manifest.read_text(encoding="utf-8"),
                      "winget 清单的 sha256 与 dist 产物不一致：发版前请重跑 gen_winget_manifest.py")


if __name__ == "__main__":
    unittest.main()
