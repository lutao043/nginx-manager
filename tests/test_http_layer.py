"""HTTP 传输层回归：keep-alive 与静态资源条件请求（304）。

本仓原本是 HTTP/1.0（``protocol_version`` 未设）：每个请求新建一条 TCP 连接 + 一个新
线程，空闲时约 1.2 req/s，折合每天约 10 万次线程创建。改为 HTTP/1.1 后连接可复用，
但**开启的前提是每个响应都带准确的 Content-Length**——少一个，客户端就会挂在
「等下一条响应」上。本文件就是这条前提的门禁：

  - 同一连接连续多个请求都能拿到正确响应（含 404 / 403 / 304 之后仍可继续用）；
  - 每个带正文的响应都有 Content-Length，304 明确不带正文；
  - 写接口的 CSRF 校验与 409/`saved:true` 语义不因协议变更而改变。
"""
import http.client
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from helpers import ServerFixture  # noqa: E402

requires_posix = unittest.skipUnless(os.name != "nt", "nginx 替身脚本依赖 POSIX shell")


@requires_posix
class HttpTransportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = ServerFixture().start()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.stop()

    def setUp(self):
        self.conn = http.client.HTTPConnection("127.0.0.1", self.fixture.port, timeout=10)
        self.addCleanup(self.conn.close)

    def _get(self, path, headers=None):
        self.conn.request("GET", path, headers=headers or {})
        resp = self.conn.getresponse()
        return resp, resp.read()

    # ---- keep-alive ----

    def test_http_11_and_connection_reuse(self):
        """同一连接连续 4 个请求都必须成功——证明 keep-alive 生效且响应不带错长度。"""
        for i in range(4):
            resp, body = self._get("/api/status")
            self.assertEqual(resp.status, 200, "第 %d 个请求失败" % (i + 1))
            self.assertEqual(resp.version, 11, "响应不是 HTTP/1.1，连接无法复用")
            self.assertIsInstance(json.loads(body.decode("utf-8")), dict)
            self.assertTrue(resp.getheader("Content-Length"), "JSON 响应缺 Content-Length")

    def test_connection_survives_error_responses(self):
        """404（不存在的静态资源）之后连接仍可用：错误响应也必须带 Content-Length。

        若错误响应长度不对，HTTP/1.1 下会把余下字节当成下一个响应的起始，客户端随即
        读到一串垃圾或直接挂住——这是开启 keep-alive 最典型的翻车方式。
        """
        resp, _ = self._get("/no-such-asset.js")
        self.assertEqual(resp.status, 404)
        self.assertTrue(resp.getheader("Content-Length"), "404 响应缺 Content-Length")

        resp, body = self._get("/api/status")
        self.assertEqual(resp.status, 200, "错误响应之后同一连接不可用了")
        self.assertIsInstance(json.loads(body.decode("utf-8")), dict)

    def test_write_guard_still_enforced_over_keepalive(self):
        """写接口的 X-Requested-With 校验照旧：403 之后连接仍可用。"""
        self.conn.request("POST", "/api/config/test", body=b"{}",
                          headers={"Content-Type": "application/json"})
        resp = self.conn.getresponse()
        resp.read()
        self.assertEqual(resp.status, 403, "缺少 X-Requested-With 的写操作必须被拒")

        resp, _ = self._get("/api/status")
        self.assertEqual(resp.status, 200, "403 之后同一连接不可用了")

    def test_keepalive_write_roundtrip(self):
        """带 CSRF 头的写请求在同一连接上正常返回（写路径同样不能破坏连接状态）。"""
        with open(os.path.join(self.fixture.conf_dir, "nginx.conf"), encoding="utf-8") as f:
            current = f.read()
        payload = json.dumps({"path": "nginx.conf", "content": current})
        self.conn.request("PUT", "/api/config/file", body=payload.encode("utf-8"),
                          headers={"Content-Type": "application/json",
                                   "X-Requested-With": "XMLHttpRequest"})
        resp = self.conn.getresponse()
        body = resp.read()
        self.assertLess(resp.status, 400, "写请求失败：%s" % body)
        self.assertIsInstance(json.loads(body.decode("utf-8")), dict)

        resp, _ = self._get("/api/status")
        self.assertEqual(resp.status, 200)

    # ---- 静态资源条件请求 ----

    def test_static_has_validators(self):
        resp, body = self._get("/")
        self.assertEqual(resp.status, 200)
        self.assertTrue(body, "首页应有正文")
        self.assertTrue(resp.getheader("ETag"), "静态资源缺少 ETag")
        self.assertTrue(resp.getheader("Last-Modified"), "静态资源缺少 Last-Modified")
        self.assertEqual(resp.getheader("Cache-Control"), "no-cache",
                         "应每次重校验（revalidate），避免升级后吃到陈旧前端")
        self.assertEqual(resp.getheader("Content-Length"), str(len(body)))

    def test_static_304_on_if_none_match(self):
        """If-None-Match 命中 → 304 且不带正文（前端资源约 200KB，刷新时省掉整份重传）。"""
        resp, _ = self._get("/")
        etag = resp.getheader("ETag")

        resp, body = self._get("/", headers={"If-None-Match": etag})
        self.assertEqual(resp.status, 304, "条件请求未命中 304")
        self.assertEqual(body, b"", "304 不得带正文")
        self.assertIsNone(resp.getheader("Content-Length"),
                          "304 不应带 Content-Length（无正文）")
        self.assertEqual(resp.getheader("ETag"), etag)

        # 304 之后连接必须仍可用
        resp, body = self._get("/api/status")
        self.assertEqual(resp.status, 200)
        self.assertIsInstance(json.loads(body.decode("utf-8")), dict)

    def test_static_200_on_stale_etag(self):
        """ETag 不匹配必须回 200 + 完整正文（不得误判成 304）。"""
        resp, body = self._get("/", headers={"If-None-Match": '"stale-tag"'})
        self.assertEqual(resp.status, 200)
        self.assertTrue(body)

    def test_static_304_on_if_modified_since(self):
        """If-Modified-Since 的回退路径：未来时间 → 304，过去时间 → 200。"""
        resp, _ = self._get("/")
        last_modified = resp.getheader("Last-Modified")

        resp, body = self._get("/", headers={"If-Modified-Since": last_modified})
        self.assertEqual(resp.status, 304, "资源未改动应回 304")
        self.assertEqual(body, b"")

        resp, body = self._get("/", headers={"If-Modified-Since": "Mon, 01 Jan 1990 00:00:00 GMT"})
        self.assertEqual(resp.status, 200, "资源晚于该时间，应回 200")
        self.assertTrue(body)

    def test_if_none_match_takes_precedence(self):
        """If-None-Match 优先于 If-Modified-Since（RFC 9110 §13.1.3）。

        两者同时给出且 Etag 不匹配时，即使 If-Modified-Since 很新也必须回 200。
        """
        resp, _ = self._get("/")
        last_modified = resp.getheader("Last-Modified")
        resp, body = self._get("/", headers={"If-None-Match": '"stale-tag"',
                                            "If-Modified-Since": last_modified})
        self.assertEqual(resp.status, 200, "ETag 不匹配时不应因 IMS 回 304")
        self.assertTrue(body)

    def test_malformed_if_modified_since_is_ignored(self):
        """非法日期头不得 500，按「未命中」回 200。"""
        resp, body = self._get("/", headers={"If-Modified-Since": "not-a-date"})
        self.assertEqual(resp.status, 200)
        self.assertTrue(body)

    def test_api_responses_are_not_cached(self):
        """接口响应仍为 no-store（304 逻辑只作用于静态资源，别把接口也缓存了）。"""
        resp, _ = self._get("/api/status")
        self.assertEqual(resp.getheader("Cache-Control"), "no-store")

    # ---- HEAD ----

    def _head(self, path, headers=None):
        self.conn.request("HEAD", path, headers=headers or {})
        resp = self.conn.getresponse()
        return resp, resp.read()

    def test_head_api_matches_get_headers(self):
        """HEAD 不再 501：头部与 GET 一致、正文为空（`curl -I` 探活用得上）。"""
        resp, body = self._head("/api/status")
        self.assertEqual(resp.status, 200, "HEAD /api/status 应为 200 而不是 501")
        self.assertEqual(body, b"", "HEAD 响应不得带正文")
        self.assertTrue(resp.getheader("Content-Length"), "HEAD 也要给 Content-Length（即 GET 的实际长度）")
        self.assertEqual(resp.getheader("Content-Type"), "application/json; charset=utf-8")

    def test_head_static_no_body(self):
        resp, body = self._head("/")
        self.assertEqual(resp.status, 200)
        self.assertEqual(body, b"")
        self.assertTrue(resp.getheader("ETag"))

    def test_head_404_and_connection_reuse(self):
        """HEAD 的 404 之后连接仍可用：正文被跳过，头部仍须自洽（keep-alive 不串位）。"""
        resp, body = self._head("/no-such-asset.js")
        self.assertEqual(resp.status, 404)
        self.assertEqual(body, b"")
        resp, body = self._get("/api/status")
        self.assertEqual(resp.status, 200, "HEAD 之后同一连接不可用了")
        self.assertIsInstance(json.loads(body.decode("utf-8")), dict)

    def test_head_conditional_304(self):
        resp, _ = self._get("/")
        etag = resp.getheader("ETag")
        resp, body = self._head("/", headers={"If-None-Match": etag})
        self.assertEqual(resp.status, 304)
        self.assertEqual(body, b"")


if __name__ == "__main__":
    unittest.main()
