"""后端 HTTP 接口回归：状态、配置树、配置文件读写与校验失败路径。

服务以真实子进程运行（见 helpers.ServerFixture），断言的是真实响应与真实磁盘状态。
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import (INVALID_DIRECTIVE, REPO_ROOT, SERVER_PY, VALID_CONF,  # noqa: E402
                     ServerTestCase, requires_posix)


class StatusTest(ServerTestCase):
    def test_status_fields(self):
        st, body = self.fixture.get("/api/status")
        self.assertEqual(st, 200)
        for key in ("running", "version", "pid", "confPath", "confDir", "nginxPath",
                    "confFileExists", "frontendOk"):
            self.assertIn(key, body)
        self.assertTrue(body["confPath"].endswith("nginx.conf"))
        self.assertTrue(body["confFileExists"])
        self.assertTrue(body["frontendOk"])
        self.assertEqual(body["version"], "1.30.4")          # 取自 nginx -v
        self.assertIsInstance(body["running"], bool)

    def test_static_page_served(self):
        with urllib.request.urlopen(self.fixture.base + "/", timeout=30) as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode("utf-8")
            self.assertIn("nginx manager", html)
            self.assertIn('id="editor"', html)

    def test_config_tree_lists_conf_files(self):
        st, body = self.fixture.get("/api/config")
        self.assertEqual(st, 200)
        names = _tree_names(body.get("tree") or [])
        self.assertIn("nginx.conf", names)


def _tree_names(nodes):
    out = []
    for n in nodes or []:
        if n.get("isDir"):
            out.extend(_tree_names(n.get("children")))
        else:
            out.append(n.get("name"))
    return out


class ConfigFileTest(ServerTestCase):
    def setUp(self):
        self.fixture.write_conf("nginx.conf", VALID_CONF)

    def test_read_file(self):
        st, body = self.fixture.get("/api/config/file?path=nginx.conf")
        self.assertEqual(st, 200)
        self.assertEqual(body.get("path"), "nginx.conf")
        self.assertIn("worker_processes", body.get("content", ""))

    def test_put_valid_content(self):
        new = VALID_CONF.replace("worker_processes  1;", "worker_processes  2;")
        st, body = self.fixture.put("/api/config/file", {"path": "nginx.conf", "content": new})
        self.assertEqual(st, 200)
        self.assertTrue(body.get("ok"))
        self.assertFalse(body.get("backedUp"))
        self.assertTrue(body["test"]["ok"])
        self.assertEqual(self.fixture.read_conf(), new)

    def test_put_requires_csrf_header(self):
        st, body = self.fixture.put("/api/config/file",
                                    {"path": "nginx.conf", "content": VALID_CONF}, csrf=False)
        self.assertEqual(st, 403)
        self.assertIn("X-Requested-With", body.get("error", ""))

    def test_put_rejects_path_traversal(self):
        st, body = self.fixture.put("/api/config/file", {"path": "../outside.conf", "content": "x"})
        self.assertEqual(st, 400)

    def test_put_rejects_absolute_path(self):
        st, _ = self.fixture.put("/api/config/file", {"path": "/etc/passwd", "content": "x"})
        self.assertEqual(st, 400)

    def test_put_rejects_unknown_file(self):
        st, _ = self.fixture.put("/api/config/file", {"path": "brand-new.conf", "content": VALID_CONF})
        self.assertEqual(st, 404)

    def test_read_rejects_path_traversal(self):
        st, _ = self.fixture.get("/api/config/file?path=../../etc/passwd")
        self.assertEqual(st, 400)

    def test_content_must_be_string(self):
        st, _ = self.fixture.put("/api/config/file", {"path": "nginx.conf", "content": 42})
        self.assertEqual(st, 400)

    @requires_posix
    def test_put_invalid_content_reports_409_with_line(self):
        """校验失败：配置已落盘但未应用（409 + saved:true + 可定位行号）。"""
        broken = VALID_CONF.replace("worker_processes  1;", "worker_processes  1;\n%s;" % INVALID_DIRECTIVE)
        st, body = self.fixture.put("/api/config/file", {"path": "nginx.conf", "content": broken})
        self.assertEqual(st, 409)
        self.assertTrue(body.get("saved"))
        self.assertFalse(body["test"]["ok"])
        self.assertEqual(body["test"]["errLine"], 2)          # 插入在 worker_processes 之后的第 2 行
        self.assertTrue(body["test"]["errFile"].endswith("nginx.conf"))
        self.assertEqual(self.fixture.read_conf(), broken)    # 语义：已保存、未生效

    @requires_posix
    def test_config_test_endpoint_reports_failure_and_line(self):
        broken = VALID_CONF + "\n%s;\n" % INVALID_DIRECTIVE
        self.fixture.write_conf("nginx.conf", broken)
        st, body = self.fixture.post("/api/config/test")
        self.assertEqual(st, 200)
        self.assertFalse(body.get("ok"))
        self.assertEqual(body.get("errLine"), broken.count("\n"))   # 末行
        self.assertIn("test failed", body.get("output", ""))

    @requires_posix
    def test_config_test_endpoint_ok(self):
        st, body = self.fixture.post("/api/config/test")
        self.assertEqual(st, 200)
        self.assertTrue(body.get("ok"))
        self.assertIn("successful", body.get("output", ""))


class StartupArgValidationTest(unittest.TestCase):
    """启动参数边界（SECURITY_AUDIT P3）：越界/非数字端口必须在入口被拒，不能启动到 bind 才抛栈。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nm-startup-")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, *args):
        proc = subprocess.run(
            [sys.executable, "-u", SERVER_PY, "--data-dir", self.tmp, *args],
            capture_output=True, text=True, timeout=60, cwd=REPO_ROOT,
            env={**os.environ, "BROWSER": "/usr/bin/true"},
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")

    def test_rejects_out_of_range_port(self):
        code, out = self._run("--port", "70000")
        self.assertEqual(code, 2)
        self.assertIn("1~65535", out)

    def test_rejects_zero_port(self):
        code, out = self._run("--port", "0")
        self.assertEqual(code, 2)
        self.assertIn("1~65535", out)

    def test_rejects_non_numeric_port(self):
        code, out = self._run("--port", "abc")
        self.assertEqual(code, 2)
        self.assertIn("整数", out)


if __name__ == "__main__":
    unittest.main()
