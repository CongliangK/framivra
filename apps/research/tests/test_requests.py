"""离线 HTTP/CLI 回归：不访问网络，不写 cookie 或采集结果。"""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

VIDEO = {"bvid": "BV1test", "aid": 1, "title": "<em>人工智能</em>",
         "author": "作者", "mid": 2, "pubdate": 0}

ROOT = Path(__file__).resolve().parents[1]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bili = load("research_bili", "bilibili.py")
github = load("research_github", "github_repos.py")
assets = load("research_assets", "fetch-site-assets.py")
SEARCH = "https://api.bilibili.com/x/web-interface/wbi/search/type"
NAV = {"code": -101, "message": "账号未登录", "data": {"wbi_img": {
    "img_url": "https://example.invalid/" + "a" * 32 + ".png",
    "sub_url": "https://example.invalid/" + "b" * 32 + ".png"}}}


class Response:
    def __init__(self, data, status=200):
        self.status = status
        self.body = data.encode() if isinstance(data, str) else json.dumps(data).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body


class BilibiliTests(unittest.TestCase):
    def cli(self, payload, args=None):
        stderr, stdout = io.StringIO(), io.StringIO()
        def respond(req, timeout):
            self.assertEqual(timeout, 15)
            if req.full_url.endswith("/nav"):
                return Response(NAV)
            return Response(payload(req) if callable(payload) else payload)
        with patch.object(bili, "load_or_init_cookie"), \
             patch.object(bili.urllib.request, "urlopen", side_effect=respond) as request, \
             patch.object(bili.time, "sleep"), patch.object(bili, "save") as save, \
             patch.object(sys, "argv", ["bilibili.py"] + (args or ["search", "人工智能", "--retry", "1"])), \
             contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(stdout):
            status = bili.main()
        return status, stderr.getvalue(), stdout.getvalue(), request.call_count, save

    def test_risk_codes_exit_nonzero_with_diagnosis(self):
        for code in (-352, -412):
            with self.subTest(code=code):
                status, error, out, _, save = self.cli({"code": code, "message": "风控"})
                self.assertEqual(status, 1)
                for text in ("人工智能", "第1页", "HTTP 200", str(code), "换网络", "cookie", "稍后重试"):
                    self.assertIn(text, error)
                self.assertNotIn("第1页 0 条", out)
                save.assert_not_called()

    def test_code_zero_voucher_is_blocked_not_empty(self):
        status, error, _, _, save = self.cli({"code": 0, "message": "OK", "data": {"v_voucher": "secret"}})
        self.assertEqual(status, 1)
        self.assertIn("v_voucher", error)
        self.assertIn("并非真无结果", error)
        self.assertIn("B 站错误码 0", error)
        self.assertNotIn("secret", error)
        save.assert_not_called()

    def test_missing_result_is_abnormal(self):
        status, error, _, _, _ = self.cli({"code": 0, "data": {}})
        self.assertEqual(status, 1)
        self.assertIn("缺少 result", error)

    def test_first_page_empty_is_distinct_exit_two_and_bounded_retry(self):
        status, error, out, count, save = self.cli({"code": 0, "data": {"result": []}},
            ["search", "不存在", "--pages", "2", "--retry", "3"])
        self.assertEqual(status, 2)
        self.assertEqual(count, 4)  # 一次 nav + 三次 search（保持原 retry 尝试次数语义）
        self.assertIn("真无结果", error)
        self.assertIn("result=[]", error)
        self.assertNotIn("✓", out)
        save.assert_not_called()

    def test_second_page_empty_saves_first_page_and_exits_zero(self):
        def payload(req):
            page = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)["page"][0]
            return {"code": 0, "data": {"result": [VIDEO] if page == "1" else []}}
        status, error, _, count, save = self.cli(payload,
            ["search", "人工智能", "--pages", "2", "--retry", "3"])
        self.assertEqual(status, 0)
        self.assertEqual(count, 5)  # nav + 首页 + 空第2页三次有限重试
        self.assertIn("第2页 真无结果", error)
        save.assert_called_once()
        self.assertEqual([r["bvid"] for r in save.call_args.args[1]], ["BV1test"])

    def test_empty_page_stops_only_current_keyword(self):
        requested = []
        def payload(req):
            query = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)
            keyword, page = query["keyword"][0], query["page"][0]
            requested.append((keyword, page))
            videos = [dict(VIDEO, bvid="BV" + keyword)] if page == "1" else []
            return {"code": 0, "data": {"result": videos}}
        status, _, _, _, save = self.cli(payload,
            ["search", "甲,乙", "--pages", "3", "--retry", "1"])
        self.assertEqual(status, 0)
        self.assertEqual(requested, [("甲", "1"), ("甲", "2"), ("乙", "1"), ("乙", "2")])
        save.assert_called_once()
        self.assertEqual([r["bvid"] for r in save.call_args.args[1]], ["BV甲", "BV乙"])

    def test_empty_first_keyword_does_not_discard_later_results(self):
        def payload(req):
            keyword = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)["keyword"][0]
            return {"code": 0, "data": {"result": [] if keyword == "空" else [VIDEO]}}
        status, _, _, _, save = self.cli(payload,
            ["search", "空,人工智能", "--retry", "1"])
        self.assertEqual(status, 0)
        save.assert_called_once()
        self.assertEqual([r["bvid"] for r in save.call_args.args[1]], ["BV1test"])

    def test_second_page_risk_discards_first_page_without_save(self):
        for risk in ({"code": -352, "message": "风控"},
                     {"code": -412, "message": "风控"},
                     {"code": 0, "data": {"v_voucher": "secret", "result": []}}):
            with self.subTest(risk=risk):
                def payload(req):
                    page = urllib.parse.parse_qs(urllib.parse.urlsplit(req.full_url).query)["page"][0]
                    return {"code": 0, "data": {"result": [VIDEO]}} if page == "1" else risk
                status, error, _, _, save = self.cli(payload,
                    ["search", "人工智能", "--pages", "2", "--retry", "1"])
                self.assertEqual(status, 1)
                self.assertIn("第2页", error)
                self.assertNotIn("第2页 真无结果", error)
                save.assert_not_called()

    def test_voucher_retry_can_recover_without_cookie_rotation(self):
        video = {"bvid": "BV1test", "aid": 1, "title": "<em>人工智能</em>",
                 "author": "作者", "mid": 2, "pubdate": 0}
        responses = [Response(NAV), Response({"code": 0, "data": {"v_voucher": "x"}}),
                     Response({"code": 0, "data": {"result": [video]}})]
        with patch.object(bili, "load_or_init_cookie"), \
             patch.object(bili.urllib.request, "urlopen", side_effect=responses) as request, \
             patch.object(bili.time, "sleep"), patch.object(bili, "save") as save, \
             patch.object(sys, "argv", ["bilibili.py", "search", "人工智能", "--retry", "2"]), \
             contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(bili.main(), 0)
        self.assertEqual(request.call_count, 3)
        self.assertIn("[人工智能] 第1页 1 条", out.getvalue())
        self.assertEqual(save.call_args.args[1][0]["title"], "人工智能")

    def test_nav_unauthenticated_keys_are_allowed(self):
        with patch.object(bili.urllib.request, "urlopen", return_value=Response(NAV)):
            self.assertEqual(len(bili.mixin_key()), 32)

    def test_non_json_http_200_is_diagnostic(self):
        with patch.object(bili.urllib.request, "urlopen", return_value=Response("<html>出错啦</html>")):
            with self.assertRaisesRegex(bili.ResearchError, "HTTP 200.*非 JSON"):
                bili.http(SEARCH)

    def test_http_error_includes_status_and_bili_code(self):
        error = urllib.error.HTTPError(SEARCH, 412, "blocked", {},
            io.BytesIO(b'{"code": -412, "message": "blocked"}'))
        with patch.object(bili.urllib.request, "urlopen", side_effect=error):
            with self.assertRaisesRegex(bili.ResearchError, "HTTP 412.*-412"):
                bili.http(SEARCH)

    def test_timeout_is_bounded_and_has_context_and_proxy(self):
        for error in (TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out")),
                      urllib.error.URLError("timed out")):
            with self.subTest(error=error), \
                 patch.object(bili.urllib.request, "urlopen", side_effect=error) as request, \
                 patch.object(bili.time, "sleep"), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(bili.ResearchError) as caught:
                    bili.http(SEARCH)
                self.assertEqual(request.call_count, 3)
                for text in (SEARCH, "超时", "15 秒", "HTTP 未收到", "代理", "HTTPS_PROXY"):
                    self.assertIn(text, str(caught.exception))

    def test_unreachable_includes_proxy_advice(self):
        with patch.object(bili.urllib.request, "urlopen", side_effect=urllib.error.URLError("refused")), \
             patch.object(bili.time, "sleep"), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(bili.ResearchError, "网络不可达.*代理"):
                bili.http(SEARCH)

    def test_stats_and_comments_share_risk_detection(self):
        for command in ("stats", "comments"):
            with self.subTest(command=command):
                status, error, _, _, save = self.cli({"code": -352, "message": "风控"}, [command, "BV1test"])
                self.assertEqual(status, 1)
                self.assertIn("-352", error)
                save.assert_not_called()

    def test_reply_risk_is_not_empty_comments(self):
        responses = [Response({"code": 0, "data": {"aid": 1}}),
                     Response({"code": -412, "message": "风控"})]
        with patch.object(bili.urllib.request, "urlopen", side_effect=responses):
            with self.assertRaisesRegex(bili.ResearchError, "-412"):
                bili.comments("BV1test")

    def test_legitimate_empty_comments(self):
        responses = [Response({"code": 0, "data": {"aid": 1}}),
                     Response({"code": 0, "data": {"replies": None}})]
        with patch.object(bili.urllib.request, "urlopen", side_effect=responses):
            self.assertEqual(bili.comments("BV1test"), [])

    def test_cookie_override_never_writes_cache(self):
        with patch.dict(bili.os.environ, {"BILIBILI_COOKIE": "buvid3=user-cookie"}), \
             patch.object(bili.Path, "write_text") as write, patch.object(bili.Path, "mkdir") as mkdir, \
             patch.object(bili, "COOKIE", ""):
            bili.load_or_init_cookie()
            self.assertEqual(bili.COOKIE, "buvid3=user-cookie")
            write.assert_not_called()
            mkdir.assert_not_called()

    def test_invalid_retry_is_argparse_error_not_unbound_result(self):
        with patch.object(sys, "argv", ["bilibili.py", "search", "x", "--retry", "0"]), \
             contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            bili.main()
        self.assertEqual(caught.exception.code, 2)


class OtherRequestTests(unittest.TestCase):
    def test_github_http_error_nonzero_no_success_file(self):
        error = urllib.error.HTTPError("https://api.github.com/search/repositories", 403,
                                      "forbidden", {}, io.BytesIO(b'{"message": "rate limit"}'))
        with patch.object(github.urllib.request, "urlopen", side_effect=error), \
             patch.object(github.time, "sleep"), patch.object(github.Path, "write_text") as write, \
             patch.object(sys, "argv", ["github_repos.py", "test"]), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(github.main(), 1)
        self.assertIn("HTTP 403", stderr.getvalue())
        self.assertIn("GITHUB_TOKEN", stderr.getvalue())
        write.assert_not_called()

    def test_github_missing_items_is_not_empty(self):
        with patch.object(github.urllib.request, "urlopen", return_value=Response({"message": "bad"})):
            with self.assertRaisesRegex(RuntimeError, "缺少 items.*并非真无结果"):
                github.search_repos("test")

    def test_github_true_empty_remains_valid(self):
        with patch.object(github.urllib.request, "urlopen", return_value=Response({"items": []})):
            self.assertEqual(github.search_repos("test"), [])

    def test_other_network_timeouts_are_chinese_and_contextual(self):
        for module, call, limit in ((github, lambda: github.search_repos("test"), 15),
                                    (assets, lambda: assets.fetch("https://example.invalid"), 30)):
            with self.subTest(module=module), \
                 patch.object(module.urllib.request, "urlopen", side_effect=TimeoutError("timed out")):
                with self.assertRaises(RuntimeError) as caught:
                    call()
                for text in ("超时", str(limit), "代理", "HTTP 未收到"):
                    self.assertIn(text, str(caught.exception))

    def test_assets_css_failure_is_nonzero_partial_result(self):
        with patch.object(sys, "argv", ["fetch-site-assets.py", "https://example.invalid", str(ROOT / "unused")]), \
             patch.object(assets, "fetch", return_value=b'<link href="bad.css" rel="stylesheet">'), \
             patch.object(assets, "save", side_effect=RuntimeError("HTTP 403")), \
             patch.object(assets.Path, "mkdir"), patch.object(assets.Path, "write_text"), \
             contextlib.redirect_stderr(io.StringIO()) as stderr, \
             contextlib.redirect_stdout(io.StringIO()) as stdout:
            self.assertEqual(assets.cli(), 1)
        self.assertIn("CSS 失败", stderr.getvalue())
        self.assertIn("部分完成", stdout.getvalue())

    def test_assets_page_failure_is_nonzero(self):
        with patch.object(sys, "argv", ["fetch-site-assets.py", "https://example.invalid", str(ROOT / "unused")]), \
             patch.object(assets.Path, "mkdir"), patch.object(assets, "fetch", side_effect=RuntimeError("HTTP 412")), \
             contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(assets.cli(), 1)
        self.assertIn("网站资产采集失败", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
