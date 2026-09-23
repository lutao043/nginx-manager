"""日志实时跟随的增量读取语义（API.md：GET /api/logs/error|access 的 since / offset / size / reset / hasMore）。

服务真实子进程 + 真实 HTTP，日志用真实文件写入，覆盖：
尾部读取即重置、增量只取新增、半行不下发、日志轮转重置、多字节字符边界、行数上限、
非法 since 回落、单次字节上限与 hasMore 续取、访问日志路径越界仍被拒。
"""
import os
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import ServerTestCase, VALID_CONF  # noqa: E402

# 单次返回的字节上限（与 backend/nginxctl.py::LOG_CHUNK_MAX_BYTES 对应，测试里作为前提断言）
CHUNK_MAX_BYTES = 256 * 1024


def _lines(n: int, prefix: str = "line") -> str:
    return "".join("%s-%03d\n" % (prefix, i) for i in range(1, n + 1))


class LogFollowTest(ServerTestCase):
    """两个日志端点的尾部/增量读取（同一服务进程，逐用例各自重置日志内容）。"""

    def setUp(self):
        self.error_log = os.path.join(self.fixture.logs_dir, "error.log")
        self.access_log = os.path.join(self.fixture.logs_dir, "access.log")
        self._write(self.error_log, "")
        self._write(self.access_log, "")

    # ---- 小工具 ----

    def _write(self, path: str, text: str, mode: str = "w") -> None:
        with open(path, mode, encoding="utf-8", newline="") as f:
            f.write(text)

    def _append(self, path: str, text: str) -> None:
        self._write(path, text, "a")

    def _size(self, path: str) -> int:
        return os.path.getsize(path)

    def _get(self, endpoint: str, **params):
        """GET 并断言成功（成功形态字段由 test_api_contract 双向核对）。"""
        query = ("?" + urllib.parse.urlencode(params)) if params else ""
        status, body = self.fixture.get(endpoint + query)
        self.assertLess(status, 400, "%s 返回错误：%s" % (endpoint, body))
        return body

    def err(self, **params):
        return self._get("/api/logs/error", **params)

    # ---- 尾部读取 ----

    def test_tail_read_is_a_reset_aligned_to_file_end(self):
        self._write(self.error_log, _lines(10))
        body = self.err(lines=3)
        self.assertTrue(body["reset"], "首次读取（无 since）应标记为全量重置")
        self.assertFalse(body["hasMore"])
        self.assertEqual(body["content"].count("\n"), 3)
        self.assertTrue(body["content"].endswith("line-010\n"))
        self.assertEqual(body["offset"], self._size(self.error_log))
        self.assertEqual(body["size"], body["offset"], "size 与尾部读取后的 offset 都应对齐文件末尾")

    def test_file_missing_returns_empty_without_error(self):
        os.remove(self.error_log)
        body = self.err()
        self.assertEqual(body["content"], "")
        self.assertEqual(body["offset"], 0)
        # 文件不存在时的增量请求同样不该报错
        body2 = self.err(since=1234)
        self.assertEqual(body2["content"], "")

    # ---- 增量读取 ----

    def test_incremental_returns_only_new_lines(self):
        self._write(self.error_log, _lines(5))
        first = self.err()
        self._append(self.error_log, "new-1\nnew-2\n")
        second = self.err(since=first["offset"])
        self.assertFalse(second["reset"], "偏移有效时不得重置（否则前端会整段重画）")
        self.assertEqual(second["content"], "new-1\nnew-2\n")
        self.assertEqual(second["offset"], self._size(self.error_log))
        # 游标语义：拿新 offset 再拉一次必须为空（不重复、不漏行）
        third = self.err(since=second["offset"])
        self.assertEqual(third["content"], "")
        self.assertEqual(third["offset"], second["offset"])

    def test_partial_line_is_withheld_until_complete(self):
        self._write(self.error_log, "done\n")
        first = self.err()
        self._append(self.error_log, "half-")          # 写入中：行还没有换行符
        mid = self.err(since=first["offset"])
        self.assertEqual(mid["content"], "", "未写完的半行不应下发")
        self.assertEqual(mid["offset"], first["offset"], "半行时偏移不得前进（否则这行会丢）")
        self._append(self.error_log, "line\n")
        last = self.err(since=first["offset"])
        self.assertEqual(last["content"], "half-line\n", "补齐换行后应一次性取到整行")
        self.assertEqual(last["offset"], self._size(self.error_log))

    def test_rotation_resets_and_realigns(self):
        self._write(self.error_log, _lines(6))
        first = self.err()
        self._write(self.error_log, "rotated-1\nrotated-2\n")   # 清空重写：旧偏移失效
        again = self.err(since=first["offset"])
        self.assertTrue(again["reset"], "偏移大于文件大小时必须重置")
        self.assertEqual(again["content"], "rotated-1\nrotated-2\n")
        self.assertEqual(again["offset"], self._size(self.error_log))

    def test_invalid_since_falls_back_to_tail(self):
        self._write(self.error_log, _lines(4))
        for bad in ("abc", "-5", ""):
            with self.subTest(since=bad):
                body = self.err(since=bad)
                self.assertTrue(body["reset"])
                self.assertEqual(body["content"], _lines(4))

    def test_multibyte_lines_survive_the_boundary(self):
        self._write(self.error_log, "启动日志：①\n")
        first = self.err()
        tail = "".join("中文日志行-%d：多字节边界\n" % i for i in range(1, 201))
        self._append(self.error_log, tail)
        body = self.err(since=first["offset"])
        self.assertEqual(body["content"], tail)
        self.assertNotIn("\ufffd", body["content"], "多字节字符不得被截出半个（出现替换字符即失败）")
        self.assertEqual(body["offset"], self._size(self.error_log))

    def test_lines_cap_limits_tail(self):
        self._write(self.error_log, _lines(50))
        body = self.err(lines=5)
        self.assertEqual(body["content"].count("\n"), 5)

    def test_incremental_lines_cap_keeps_line_breaks(self):
        """增量读取触发行数上限时，裁剪后仍必须是**逐行**内容。

        回归：上限裁剪分支用的是 "".join，把保留的行全部粘成了一行（0 个换行），
        表现为前端「日志突然变成一整条」；顺带让前端的按换行计数的新行统计失真。
        旧用例只覆盖了尾部读取（read_log_file）的上限，没覆盖增量分支。
        """
        self._write(self.error_log, _lines(2))
        first = self.err()
        burst = "".join("burst-%03d\n" % i for i in range(1, 251))
        self._append(self.error_log, burst)

        body = self.err(since=first["offset"], lines=200)
        self.assertEqual(body["content"].count("\n"), 200, "裁剪后应恰好保留 200 行")
        self.assertEqual(body["content"].split("\n")[0], "burst-051", "保留的应是最新的 200 行")
        self.assertTrue(body["content"].endswith("burst-250\n"), "末行必须是最后写入的那行")
        self.assertEqual(body["offset"], self._size(self.error_log),
                         "裁剪头部不应影响 offset（它仍指向读取位置）")

    # ---- 单次字节上限与续取 ----

    def test_chunk_cap_marks_has_more_and_progresses(self):
        big = "".join("big-%06d 填充填充填充填充填充填充填充填充\n" % i for i in range(20000))
        self._write(self.error_log, big)
        total = self._size(self.error_log)
        self.assertGreater(total, CHUNK_MAX_BYTES, "前提：本用例的文件要大于单次返回上限")

        collected, offset, rounds = "", 0, 0
        while True:
            body = self.err(since=offset, lines=5000)
            collected += body["content"]
            self.assertLessEqual(len(body["content"].encode("utf-8")), CHUNK_MAX_BYTES,
                                 "单次返回不得突破字节上限")
            rounds += 1
            self.assertLess(rounds, 40, "hasMore 一直为真：偏移没有前进，客户端会死循环")
            if not body["hasMore"]:
                break
            self.assertGreater(body["offset"], offset, "hasMore 为真时偏移必须前进")
            offset = body["offset"]
        self.assertGreaterEqual(rounds, 2, "超过上限的文件应分多次读完（否则 hasMore 路径没被覆盖）")
        self.assertEqual(len(collected.encode("utf-8")), total, "分多次读取不得丢字节")

    # ---- 访问日志 ----

    def test_access_log_incremental_and_candidates(self):
        self._write(self.access_log, '127.0.0.1 - - "GET / HTTP/1.1" 200 1\n')
        first = self._get("/api/logs/access")
        self.assertIn(self.access_log, first["paths"])
        self.assertEqual(first["logPath"], self.access_log, "缺省应取第一个存在的候选路径")
        self.assertTrue(first["reset"])

        self._append(self.access_log, '127.0.0.1 - - "GET /a HTTP/1.1" 404 2\n')
        second = self._get("/api/logs/access", path=self.access_log, since=first["offset"])
        self.assertFalse(second["reset"])
        self.assertEqual(second["content"], '127.0.0.1 - - "GET /a HTTP/1.1" 404 2\n')
        self.assertEqual(second["offset"], self._size(self.access_log))

    def test_declared_log_path_outside_prefix_is_readable(self):
        """配置里声明的日志路径（可能在 prefix 之外）显式传入时必须可读。

        回归：缺省分支会读这个文件，但把同一个路径显式作为 `path` 传回曾被判 403
        （「下拉里选中已展示的文件反而报错」），发行版把日志写到 /var/log 时必现。
        """
        outside_dir = tempfile.mkdtemp(prefix="nm-log-declared-")
        self.addCleanup(lambda: __import__("shutil").rmtree(outside_dir, ignore_errors=True))
        declared = os.path.join(outside_dir, "site-access.log")
        line = '127.0.0.1 - - "GET /declared HTTP/1.1" 200 3\n'
        self._write(declared, line)
        conf = "http {\n    access_log %s;\n    server { listen 8080; }\n}\n" % declared
        self.fixture.write_conf("nginx.conf", conf)
        self.addCleanup(self.fixture.write_conf, "nginx.conf", VALID_CONF)

        first = self._get("/api/logs/access")
        self.assertEqual(first["logPath"], declared, "应优先取配置声明的路径")
        self.assertIn(declared, first["paths"])

        again = self._get("/api/logs/access", path=declared)          # 显式传回同一路径
        self.assertEqual(again["logPath"], declared)
        self.assertEqual(again["content"], line)

        status, body = self.fixture.get(
            "/api/logs/access?path=%s" % urllib.parse.quote(os.path.join(outside_dir, "secret.log")))
        self.assertEqual(status, 403, "同目录下未被配置声明的文件仍须拒绝：%s" % body)

    def test_access_log_path_escape_is_still_rejected(self):
        """新增 since 参数不得成为绕过路径校验的口子。"""
        outside_dir = tempfile.mkdtemp(prefix="nm-log-outside-")
        outside = os.path.join(outside_dir, "access.log")
        with open(outside, "w", encoding="utf-8") as f:
            f.write("secret\n")
        self.addCleanup(lambda: __import__("shutil").rmtree(outside_dir, ignore_errors=True))
        status, body = self.fixture.get(
            "/api/logs/access?path=%s&since=0" % urllib.parse.quote(outside))
        self.assertEqual(status, 403)
        self.assertIn("error", body)


if __name__ == "__main__":
    unittest.main()
