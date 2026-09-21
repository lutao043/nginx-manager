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


class ProcessStateTest(ServerTestCase):
    """运行状态类失败必须是 409（API.md 契约），不能报成 500。

    回归背景：reload 在未运行时返回 500，前端只把 409 当「状态问题」提示，
    500 会被当成服务端故障，用户看到的文案和可采取的动作都不一样。
    """

    def test_reload_when_not_running_is_409(self):
        st, body = self.fixture.post("/api/nginx/reload")
        self.assertEqual(st, 409)
        self.assertIn("未在运行", body.get("error", ""))

    def test_stop_when_not_running_is_409(self):
        st, body = self.fixture.post("/api/nginx/stop")
        self.assertEqual(st, 409)
        self.assertTrue(body.get("error"))


CONF_FOR_PROXY = """worker_processes  1;

error_log  logs/error.log;
pid        logs/nginx.pid;

events {
    worker_connections  64;
}

http {
    upstream backend {
        server 10.0.0.1:80;
    }

    server {
        listen       8080;
        server_name  localhost;

        location /api {
            proxy_pass http://backend;
        }
    }
}
"""


class ProxyApiTest(ServerTestCase):
    """代理增删改走真实 HTTP：查重、未建模内容保护、行尾保持。"""

    def setUp(self):
        self.fixture.write_conf("nginx.conf", CONF_FOR_PROXY)

    def _bytes(self) -> bytes:
        with open(self.fixture.conf_path(), "rb") as f:
            return f.read()

    def test_duplicate_add_rejected(self):
        """server.py 每次请求新建 ProxyManager，查重必须在地道的新实例上仍成立。"""
        st1, body1 = self.fixture.post("/api/proxies",
                                       {"path": "/dup", "target": "http://127.0.0.1:8001"})
        self.assertEqual(st1, 200, body1)
        self.assertTrue(body1.get("ok"))
        st2, body2 = self.fixture.post("/api/proxies",
                                       {"path": "/dup", "target": "http://127.0.0.1:8002"})
        self.assertEqual(st2, 409, body2)
        self.assertIn("已存在", body2.get("error", ""))
        self.assertEqual(self.fixture.read_conf().count("location /dup"), 1)
        self.assertNotIn("8002", self.fixture.read_conf())

    def test_update_targets_applies_active(self):
        """PUT /api/proxies/targets 带 active 时必须改激活行（前端单选真正生效）。"""
        self.fixture.write_conf("nginx.conf", CONF_FOR_PROXY.replace(
            "            proxy_pass http://backend;\n",
            "            proxy_pass http://backend;\n            #proxy_pass http://127.0.0.1:9001;\n"))
        st, body = self.fixture.put("/api/proxies/targets", {
            "path": "/api",
            "targets": ["http://backend", "http://127.0.0.1:9001"],
            "active": "http://127.0.0.1:9001",
        })
        self.assertEqual(st, 200, body)
        conf = self.fixture.read_conf()
        self.assertIn("proxy_pass http://127.0.0.1:9001;", conf)
        self.assertIn("#proxy_pass http://backend;", conf)

    def test_upstream_save_refuses_manual_directives(self):
        """含 zone/keepalive 等未建模指令的 upstream：拒绝整块重写，且一个字节都不改。"""
        conf = CONF_FOR_PROXY.replace("    upstream backend {\n        server 10.0.0.1:80;\n    }",
                                      "    upstream backend {\n        zone backend 64k;\n"
                                      "        keepalive 32;\n        server 10.0.0.1:80;\n    }")
        self.fixture.write_conf("nginx.conf", conf)
        before = self._bytes()
        st, body = self.fixture.put("/api/upstreams",
                                    {"name": "backend", "method": "round_robin",
                                     "servers": [{"address": "10.0.0.9:80"}]})
        self.assertIn(st, (400, 409), body)
        self.assertIn("未建模", body.get("error", ""))
        self.assertEqual(self._bytes(), before)

    def test_config_put_keeps_crlf_line_endings(self):
        """磁盘上是 CRLF、编辑器发来 LF 时，保存后必须仍是 CRLF（不整份改行尾）。"""
        self.fixture.write_conf("nginx.conf", CONF_FOR_PROXY.replace("\n", "\r\n"))
        lf = CONF_FOR_PROXY.replace("worker_processes  1;", "worker_processes  2;")
        st, body = self.fixture.put("/api/config/file", {"path": "nginx.conf", "content": lf})
        self.assertEqual(st, 200, body)
        raw = self._bytes()
        self.assertEqual(raw.count(b"\n"), raw.count(b"\r\n"))
        self.assertIn(b"worker_processes  2;", raw)
        self.assertEqual([n for n in os.listdir(self.fixture.conf_dir) if ".tmp-" in n], [])


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


class WorkspaceNginxDetectionTest(unittest.TestCase):
    """开发默认探测（`server.py::find_workspace_nginx`）：Windows 官方版布局（nginx.exe）
    与 Unix 源码构建布局（sbin/nginx）都必须认——本仓库开发机同时存在这两种形态。"""

    def setUp(self):
        import importlib.util
        self.tmp = tempfile.mkdtemp(prefix="nm-workspace-")
        backend = os.path.join(REPO_ROOT, "backend")
        if backend not in sys.path:
            sys.path.insert(0, backend)          # server.py 内部 from nginxctl/proxymgr import
        spec = importlib.util.spec_from_file_location("nm_server_ws_probe", SERVER_PY)
        assert spec is not None and spec.loader is not None
        self.server = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.server)
        self._orig = getattr(self.server, "PROJECT_ROOT")
        setattr(self.server, "PROJECT_ROOT", self.tmp)
        # 配置目录是探测条件之一，先建好；可执行文件由各用例按布局创建
        self.conf_dir = os.path.join(self.tmp, "nginx-1.30.4", "conf")
        os.makedirs(self.conf_dir, exist_ok=True)
        with open(os.path.join(self.conf_dir, "nginx.conf"), "w", encoding="utf-8") as f:
            f.write("events {}\n")

    def tearDown(self):
        setattr(self.server, "PROJECT_ROOT", self._orig)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_exe(self, rel):
        exe = os.path.join(self.tmp, "nginx-1.30.4", rel)
        os.makedirs(os.path.dirname(exe), exist_ok=True)
        with open(exe, "w", encoding="utf-8") as f:
            f.write("#!/bin/sh\n")
        return exe

    def test_windows_layout_detected(self):
        exe = self._make_exe("nginx.exe")
        got = self.server.find_workspace_nginx()
        self.assertEqual(got, {"nginxPath": exe, "confDir": self.conf_dir})

    def test_unix_source_layout_detected(self):
        exe = self._make_exe(os.path.join("sbin", "nginx"))
        got = self.server.find_workspace_nginx()
        self.assertEqual(got, {"nginxPath": exe, "confDir": self.conf_dir})

    def test_no_exe_returns_empty(self):
        self.assertEqual(self.server.find_workspace_nginx(), {})

    def test_missing_conf_returns_empty(self):
        self._make_exe("nginx.exe")
        os.remove(os.path.join(self.conf_dir, "nginx.conf"))
        self.assertEqual(self.server.find_workspace_nginx(), {})


if __name__ == "__main__":
    unittest.main()
