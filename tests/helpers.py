"""测试公共设施：临时数据目录/配置目录、nginx 替身脚本、真实服务子进程与 HTTP 客户端。

设计要点：
  - 服务以真实方式启动（子进程运行 backend/server.py），测试只走 HTTP，不打内部桩，
    因此覆盖的是真实路由、真实路径校验、真实备份/回滚编排与真实 nginx -t 调用链。
  - nginx 用替身脚本：配置里不含 INVALID_DIRECTIVE 时 -t 成功；含则按 nginx 真实
    输出格式给出多行错误（含 `in <文件>:<行号>`），用于验证失败路径与行号解析。
  - 仅用标准库（后端零第三方依赖的约定），unittest 与 pytest 都能直接收集。
  - 替身脚本是 POSIX shell：Windows 上相关用例自动跳过（CI 的测试任务跑 ubuntu）。
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(REPO_ROOT, "backend")
SERVER_PY = os.path.join(BACKEND_DIR, "server.py")

POSIX = os.name != "nt"
requires_posix = unittest.skipUnless(POSIX, "nginx 替身脚本依赖 POSIX shell")

# 探针指令：替身脚本只认这一个，出现即视为配置非法
INVALID_DIRECTIVE = "INVALID_DIRECTIVE"

# nginx -t 失败时的真实输出形状：先 [alert]，中间才是出错位置，末尾还有 test failed 汇总
#
# 参数解析必须按「精确 token」判定，不能用 case "$*" in *-v*) 之类的子串匹配：
# tempfile.mkdtemp 生成的目录名可能含 -v（如 nginx-manager-test-v88pf896），
# 子串匹配会把 -t 调用误判成 -v 版本查询，导致 -t 变成假失败（本仓曾因此抖动）。
NGINX_STUB = """#!/bin/sh
# 测试替身：不依赖真实 nginx。含 {invalid} 的配置视为语法错误。
mode=""
conf=""
want_conf=0
for a in "$@"; do
  case "$a" in
    -v|-V) mode="version" ;;
    -c) want_conf=1 ;;
    *)
      if [ "$want_conf" = "1" ]; then
        conf="$a"
        want_conf=0
      fi
      ;;
  esac
done
if [ "$mode" = "version" ]; then
  echo "nginx version: nginx/1.30.4-test" >&2
  exit 0
fi
if [ "$conf" = "" ]; then
  echo "nginx: no -c given" >&2
  exit 1
fi
line=$(grep -n "{invalid}" "$conf" 2>/dev/null | head -1 | cut -d: -f1)
if [ -n "$line" ]; then
  echo "nginx: [alert] could not open error log file: open() \\"$conf.log\\" failed (2: No such file or directory)" >&2
  echo "nginx: [emerg] unknown directive \\"{invalid}\\" in $conf:$line" >&2
  echo "nginx: configuration file $conf test failed" >&2
  exit 1
fi
echo "nginx: the configuration file $conf syntax is ok" >&2
echo "nginx: configuration file $conf test is successful" >&2
exit 0
""".format(invalid=INVALID_DIRECTIVE)

VALID_CONF = """worker_processes  1;

error_log  logs/error.log;
pid        logs/nginx.pid;

events {
    worker_connections  64;
}

http {
    server {
        listen       8080;
        server_name  localhost;

        location / {
            return 200 "ok";
        }
    }
}
"""


def write_nginx_stub(directory: str) -> str:
    """在 directory 下生成 nginx 替身可执行文件，返回其绝对路径。"""
    path = os.path.join(directory, "nginx")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(NGINX_STUB)
    os.chmod(path, 0o755)
    return path


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerFixture:
    """临时数据目录 + 临时配置目录 + 服务子进程。

    用法：
        with ServerFixture() as srv:
            status, body = srv.get("/api/status")
    """

    def __init__(self):
        self.tmp = tempfile.mkdtemp(prefix="nginx-manager-test-")
        self.data_dir = os.path.join(self.tmp, "data")
        self.conf_dir = os.path.join(self.tmp, "conf")
        self.logs_dir = os.path.join(self.tmp, "logs")
        self.proc = None
        self.base = ""
        self.output = ""

    # ---- 生命周期 ----

    def start(self) -> "ServerFixture":
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.conf_dir, exist_ok=True)
        os.makedirs(self.logs_dir, exist_ok=True)
        self.nginx = write_nginx_stub(self.tmp)
        self.write_conf("nginx.conf", VALID_CONF)
        self.write_conf("sites.conf", "server {\n    listen 8081;\n}\n")
        # 预置 settings：服务启动即进入正常模式（非预览），controller 指向替身
        with open(os.path.join(self.data_dir, "settings.json"), "w", encoding="utf-8") as f:
            json.dump({"nginxPath": self.nginx, "confDir": self.conf_dir, "backupRetention": 5}, f, indent=2)

        env = dict(os.environ)
        env["BROWSER"] = "/usr/bin/true"      # 测试中不要真的弹浏览器
        env["PYTHONIOENCODING"] = "utf-8"
        port = free_port()
        self.proc = subprocess.Popen(
            # -u：管道下 print 默认块缓冲，不加 -u 就读不到启动那行 URL（会一直阻塞）
            [sys.executable, "-u", SERVER_PY, "--data-dir", self.data_dir,
             "--nginx-path", self.nginx, "--conf-dir", self.conf_dir, "--port", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=REPO_ROOT,
        )
        self._wait_ready(port)
        return self

    def _wait_ready(self, port: int) -> None:
        deadline = time.time() + 30
        lines = []
        assert self.proc is not None and self.proc.stdout is not None
        while time.time() < deadline:
            line = self.proc.stdout.readline()
            if not line:
                if self.proc.poll() is not None:
                    raise RuntimeError("服务进程提前退出：\n" + "".join(lines))
                continue
            lines.append(line)
            m = re.search(r"http://127\.0\.0\.1:(\d+)", line)
            if m:
                self.base = "http://127.0.0.1:%s" % m.group(1)
                self.port = int(m.group(1))
                self.output = "".join(lines)
                return
        raise RuntimeError("服务在 30 秒内未就绪：\n" + "".join(lines))

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        if self.proc and self.proc.stdout:
            try:
                self.proc.stdout.close()
            except OSError:
                pass

    def cleanup(self) -> None:
        self.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.cleanup()

    # ---- HTTP ----

    def request(self, method: str, path: str, payload=None, csrf: bool = True, raw_body=None):
        """返回 (status, body_dict)。非 2xx 也返回响应体（后端错误统一 {error, detail?}）。"""
        data = raw_body
        headers = {}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        if csrf:
            headers["X-Requested-With"] = "XMLHttpRequest"
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read().decode("utf-8")
                return resp.status, json.loads(body or "{}")
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            try:
                return e.code, json.loads(body or "{}")
            except json.JSONDecodeError:
                return e.code, {"raw": body}

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, payload=None, **kw):
        return self.request("POST", path, payload=payload, **kw)

    def put(self, path, payload=None, **kw):
        return self.request("PUT", path, payload=payload, **kw)

    def delete(self, path, payload=None, **kw):
        return self.request("DELETE", path, payload=payload, **kw)

    # ---- 配置目录读写 ----

    def conf_path(self, rel: str = "nginx.conf") -> str:
        return os.path.join(self.conf_dir, rel)

    def write_conf(self, rel: str, content: str) -> None:
        with open(self.conf_path(rel), "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

    def read_conf(self, rel: str = "nginx.conf") -> str:
        with open(self.conf_path(rel), "r", encoding="utf-8") as f:
            return f.read()

    def backup_dir(self, backup_id: str) -> str:
        return os.path.join(self.data_dir, "backups", backup_id)

    def backup_ids(self) -> list:
        d = os.path.join(self.data_dir, "backups")
        if not os.path.isdir(d):
            return []
        return sorted(n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n)))


class ServerTestCase(unittest.TestCase):
    """每个测试类共享一个服务进程（启动约 1 秒，逐用例重启太慢）。"""

    fixture: ServerFixture  # 由 setUpClass 装配

    @classmethod
    def setUpClass(cls):
        cls.fixture = ServerFixture().start()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()
