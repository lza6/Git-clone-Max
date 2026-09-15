"""G35-10 GitHub Star 列表导入（纯逻辑层）单测：全 mock、绝不触网。

覆盖 parse_starred_payload / next_page_url / fetch_starred（含分页、token
注入、失败返回部分结果、10 页防御上限），以及默认 fetcher 的 Authorization
Bearer 头与 non-200 抛错行为。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gcm.app import gh_api  # noqa: E402 —— 依赖上方 sys.path 注入
from gcm.app.gh_api import (  # noqa: E402
    MAX_STARRED_PAGES,
    fetch_starred,
    next_page_url,
    parse_starred_payload,
)


class TestParseStarredPayload(unittest.TestCase):
    """纯函数：从 Star API JSON 列表提取 full_name。"""

    def test_extracts_all_full_names(self):
        payload = [
            {"full_name": "a/b", "id": 1},
            {"full_name": "c/d", "id": 2},
            {"full_name": "e/f", "id": 3},
        ]
        self.assertEqual(parse_starred_payload(payload), ["a/b", "c/d", "e/f"])

    def test_skips_invalid_entries(self):
        payload = [
            {"full_name": "a/b"},
            {"id": 2},             # 无 full_name
            {"full_name": ""},     # 空串
            {"full_name": "  "},   # 纯空白
            None,                  # 非 dict
            "x/y",                 # 非 dict
        ]
        self.assertEqual(parse_starred_payload(payload), ["a/b"])

    def test_empty_and_none(self):
        self.assertEqual(parse_starred_payload([]), [])
        self.assertEqual(parse_starred_payload(None), [])


class TestNextPageUrl(unittest.TestCase):
    """纯函数：解析 Link header 的 rel=\"next\"。"""

    def test_returns_next_url(self):
        link = (
            '<https://api.github.com/user/starred?page=2>; rel="next", '
            '<https://api.github.com/user/starred?page=3>; rel="last"'
        )
        self.assertEqual(
            next_page_url(link),
            "https://api.github.com/user/starred?page=2",
        )

    def test_unquoted_rel(self):
        self.assertEqual(
            next_page_url('<https://api.github.com/user/starred?page=2>; rel=next'),
            "https://api.github.com/user/starred?page=2",
        )

    def test_no_next_returns_empty(self):
        self.assertEqual(next_page_url(""), "")
        self.assertEqual(next_page_url('<https://a/1>; rel="last"'), "")
        self.assertEqual(next_page_url("no link header here"), "")


class TestFetchStarred(unittest.TestCase):
    """fetch_starred：分页 / token 注入 / 失败回退 / 页数上限。"""

    def test_single_page_no_link(self):
        """单页 2 条 + 无 Link → repo_list 2 条、err 为空。"""
        fetcher = mock.Mock(
            return_value=([{"full_name": "a/b"}, {"full_name": "c/d"}], ""))
        repos, err = fetch_starred("lisa", fetcher=fetcher)
        self.assertEqual(repos, ["a/b", "c/d"])
        self.assertEqual(err, "")
        fetcher.assert_called_once()
        url = fetcher.call_args.args[0]
        self.assertIn("/lisa/starred", url)
        self.assertIn("per_page=100", url)

    def test_two_page_pagination(self):
        """第一页 Link 带 next、第二页无 → 合并 4 条。"""
        fetcher = mock.Mock(side_effect=[
            ([{"full_name": "a/b"}, {"full_name": "c/d"}],
             '<https://api.github.com/user/starred?page=2>; rel="next"'),
            ([{"full_name": "e/f"}, {"full_name": "g/h"}], ""),
        ])
        repos, err = fetch_starred("lisa", fetcher=fetcher)
        self.assertEqual(repos, ["a/b", "c/d", "e/f", "g/h"])
        self.assertEqual(err, "")
        self.assertEqual(fetcher.call_count, 2)

    def test_token_forwarded_to_fetcher(self):
        """token 非空 → fetcher 收到该 token（默认 fetcher 拼 Authorization Bearer）。"""
        fetcher = mock.Mock(return_value=([], ""))
        fetch_starred("lisa", token="ghp_abc", fetcher=fetcher)
        url, token_arg = fetcher.call_args.args
        self.assertIn("/lisa/starred", url)
        self.assertEqual(token_arg, "ghp_abc")

    def test_token_empty_forwarded_as_empty(self):
        """token 为空 → fetcher 收到空串。"""
        fetcher = mock.Mock(return_value=([], ""))
        fetch_starred("lisa", fetcher=fetcher)
        self.assertEqual(fetcher.call_args.args[1], "")

    def test_fetcher_raises_returns_partial_and_error(self):
        """中途 fetcher 抛异常 → 返回已拉部分 + 非空 err。"""
        fetcher = mock.Mock(side_effect=[
            ([{"full_name": "a/b"}],
             '<https://api.github.com/user/starred?page=2>; rel="next"'),
            RuntimeError("boom"),
        ])
        repos, err = fetch_starred("lisa", fetcher=fetcher)
        self.assertEqual(repos, ["a/b"])
        self.assertIn("boom", err)

    def test_page_cap_stops_at_ten(self):
        """每页都有 next 的假 Link 链 → 恰好 MAX_STARRED_PAGES 页停止（不无限）。"""
        calls: list[str] = []

        def fetcher(url: str, token: str):
            calls.append(url)
            next_link = (
                f'<https://api.github.com/user/starred?page={len(calls) + 1}>; '
                'rel="next"'
            )
            return ([{"full_name": "x/y"}], next_link)

        repos, err = fetch_starred("lisa", fetcher=fetcher)
        self.assertEqual(len(calls), MAX_STARRED_PAGES)
        self.assertEqual(len(repos), MAX_STARRED_PAGES)
        self.assertEqual(err, "")

    def test_empty_username_error(self):
        """用户名为空 → 直接返回空列表 + 非空 err，不调 fetcher。"""
        fetcher = mock.Mock()
        repos, err = fetch_starred("   ", fetcher=fetcher)
        self.assertEqual(repos, [])
        self.assertTrue(err)
        fetcher.assert_not_called()


class TestDefaultFetcher(unittest.TestCase):
    """默认 urllib 抓取器：Authorization 头 / non-200 抛错（全 mock 不触网）。"""

    def _mock_open(self, body=b"[]", status=200):
        # 默认 fetcher 用 `with urlopen(...) as resp`：__enter__ 须返回自身
        resp = mock.MagicMock()
        resp.status = status
        resp.headers.get.return_value = ""
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        resp.__exit__.return_value = False
        return mock.patch("urllib.request.urlopen", return_value=resp)

    def test_bearer_header_sent(self):
        with self._mock_open(body=b'[{"full_name": "a/b"}]') as m_open:
            data, link = gh_api._default_fetcher(
                "https://api.github.com/users/lisa/starred", token="ghp_xyz")
        req = m_open.call_args.args[0]
        self.assertEqual(req.get_header("Authorization"), "Bearer ghp_xyz")
        self.assertEqual(data, [{"full_name": "a/b"}])
        self.assertEqual(link, "")

    def test_no_auth_header_without_token(self):
        with self._mock_open() as m_open:
            gh_api._default_fetcher("https://api.github.com/users/lisa/starred")
        req = m_open.call_args.args[0]
        self.assertIsNone(req.get_header("Authorization"))

    def test_non_200_raises(self):
        with self._mock_open(status=403), self.assertRaises(RuntimeError):
            gh_api._default_fetcher("https://api.github.com/users/lisa/starred")


if __name__ == "__main__":
    unittest.main(verbosity=2)
