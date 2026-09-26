"""G59-6 评分卡 per_repo_size 接入测试：真实临时目录字节数 → size_score 填充。"""
import tempfile
import unittest
from pathlib import Path

from gcm.app.alerts import scorecard_for_repo, scorecards
from gcm.db.repo_db import Database


def _repo(owner="a", repo="b", rid=1, path=None, tags=""):
    return {"id": rid, "owner": owner, "repo": repo, "tags": tags,
            "local_path": path, "last_sync_at": None}


def _mkfile(d: Path, name: str, size: int) -> Path:
    p = d / name
    p.write_bytes(b"x" * size)
    return p


class TestPerRepoSizeScorecard(unittest.TestCase):
    def test_scorecard_size_uses_real_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _mkfile(d, "a.bin", 4096)
            repo = _repo(path=str(d))
            card = scorecard_for_repo(repo, [], size_bytes=Database.dir_size(d))
            self.assertGreaterEqual(card["size_score"], 1)

    def test_scorecards_per_repo_size_callback(self):
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            _mkfile(d, "a.bin", 4096)
            repos = [_repo(rid=1, path=str(d)), _repo(rid=2, path=None)]
            cards = scorecards(repos, lambda rid: [],
                               per_repo_size=lambda p: Database.dir_size(p) if p else None)
            by_id = {c["repo_id"]: c for c in cards}
            self.assertGreaterEqual(by_id[1]["size_score"], 1)
            self.assertEqual(by_id[2]["size_score"], 0)

    def test_per_repo_size_exception_falls_back_zero(self):
        repos = [_repo(rid=1, path="/nonexistent/path/xyz")]
        def _boom(p):
            raise RuntimeError("boom")
        cards = scorecards(repos, lambda rid: [], per_repo_size=_boom)
        self.assertEqual(cards[0]["size_score"], 0)


if __name__ == "__main__":
    unittest.main()
