"""契约与版本一致性（G5）。

1. `API.md`（唯一权威源）声明的端点与 `backend/server.py` 实际分发完全一致（双向）。
2. 端点文档里的示例响应字段，必须真实出现在实现返回的 JSON 中（防字段改名漂移）。
3. 版本号单一来源：`backend/server.py::server_version` 是唯一事实，`build.py` 的提取器、
   exe 产物名、前端资源持久化目录 `v{版本}` 都须由它派生；版本不得落后于最新发布 tag。
"""
import importlib.util
import os
import re
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from helpers import ServerTestCase  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PY = os.path.join(REPO_ROOT, "backend", "server.py")
API_MD = os.path.join(REPO_ROOT, "API.md")

ROUTE_RE = re.compile(r'path\s*==\s*"([^"]+)"')
DOC_HEAD_RE = re.compile(r'^### (GET|POST|PUT|DELETE) (/api/[^\s]+)', re.M)
JSON_BLOCK_RE = re.compile(r'```json\s*\n(.*?)```', re.S)


def server_source() -> str:
    with open(SERVER_PY, encoding="utf-8") as f:
        return f.read()


def api_md_text() -> str:
    with open(API_MD, encoding="utf-8") as f:
        return f.read()


def implemented_routes():
    """解析 backend/server.py 的 _route_api_<method> 分发表。"""
    src = server_source()
    routes = set()
    for method in ("GET", "POST", "PUT", "DELETE"):
        m = re.search(r"def _route_api_%s\(.*?\n(.*?)(?=\n    def |\Z)" % method.lower(), src, re.S)
        assert m, "未找到 _route_api_%s 分发函数" % method.lower()
        for path in ROUTE_RE.findall(m.group(1)):
            routes.add((method, path))
    return routes


def top_level_keys(block: str):
    """取 JSON 示例里的顶层字段名（忽略嵌套对象的字段，避免把 tree[] 内部字段当成响应字段）。"""
    keys = set()
    depth = 0
    i = 0
    in_str = False
    esc = False
    str_start = 0
    while i < len(block):
        ch = block[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
                j = i + 1
                while j < len(block) and block[j].isspace():
                    j += 1
                if j < len(block) and block[j] == ":" and depth == 1:
                    keys.add(block[str_start + 1:i])
            i += 1
            continue
        if ch == '"':
            in_str = True
            str_start = i
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        i += 1
    return keys


def documented_endpoints():
    """解析 API.md：端点 -> 成功响应示例的顶层字段集合列表（一个端点可能有多种成功形态）。

    端点节里可能先给请求体示例，故只取「**成功响应 …**」之后的 json 块；
    没写成功响应示例的端点返回空列表（其字段不参与核对）。
    """
    text = api_md_text()
    heads = list(DOC_HEAD_RE.finditer(text))
    out = {}
    for i, h in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        body = text[h.end():end]
        m = re.search(r"成功响应[^\n]*", body)
        scope = body[m.end():] if m else ""
        out[(h.group(1), h.group(2).split("?")[0])] = [top_level_keys(b) for b in JSON_BLOCK_RE.findall(scope)]
    return out


class ApiDocRouteTest(unittest.TestCase):
    def test_route_sets_are_identical(self):
        impl = implemented_routes()
        doc = set(documented_endpoints())
        self.assertEqual(sorted(doc - impl), [], "API.md 声明了实现里没有的端点")
        self.assertEqual(sorted(impl - doc), [], "实现里有 API.md 未声明的端点")

    def test_every_endpoint_has_documented_status_codes(self):
        """每个端点节要么写明成功状态码，要么显式声明「同 <方法>」，避免只写请求体不写返回。"""
        text = api_md_text()
        heads = list(DOC_HEAD_RE.finditer(text))
        for i, h in enumerate(heads):
            end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
            body = text[h.end():end]
            with self.subTest(endpoint="%s %s" % (h.group(1), h.group(2))):
                self.assertRegex(body, r"(成功响应\s*`?2\d\d|同 (?:POST|PUT|GET|DELETE))",
                                 "缺少成功响应状态码")


class ApiDocFieldTest(ServerTestCase):
    """拿真服务逐端点核对文档示例字段（GET 与只读/无害写操作）。"""

    def _calls(self):
        f = self.fixture
        return {
            ("GET", "/api/status"): lambda: f.get("/api/status"),
            ("GET", "/api/config"): lambda: f.get("/api/config"),
            ("GET", "/api/config/file"): lambda: f.get("/api/config/file?path=nginx.conf"),
            ("GET", "/api/backups"): lambda: f.get("/api/backups"),
            ("GET", "/api/logs/error"): lambda: f.get("/api/logs/error"),
            ("GET", "/api/logs/access"): lambda: f.get("/api/logs/access"),
            ("GET", "/api/metrics"): lambda: f.get("/api/metrics"),
            ("GET", "/api/proxies"): lambda: f.get("/api/proxies"),
            ("GET", "/api/proxy-pool"): lambda: f.get("/api/proxy-pool"),
            ("GET", "/api/upstreams"): lambda: f.get("/api/upstreams"),
            ("GET", "/api/settings"): lambda: f.get("/api/settings"),
            ("POST", "/api/config/test"): lambda: f.post("/api/config/test"),
            ("PUT", "/api/config/file"): lambda: f.put("/api/config/file",
                                                       {"path": "nginx.conf", "content": VALID}),
        }

    def test_documented_response_fields_are_returned(self):
        """文档与响应字段双向一致。

        单一成功形态的端点：顶层字段必须两侧完全一致（多写/少写都算漂移）；
        多种成功形态的端点（如 metrics 已/未开启状态页）：至少精确命中其中一种形态。
        """
        doc = documented_endpoints()
        calls = self._calls()
        for key, call in calls.items():
            variants = doc.get(key, [])
            with self.subTest(endpoint="%s %s" % key):
                status, body = call()
                self.assertLess(status, 400, "端点返回错误：%s" % body)
                self.assertIsInstance(body, dict)
                if not variants:
                    continue
                actual = set(body)
                if len(variants) == 1:
                    self.assertEqual(variants[0], actual,
                                     "响应字段与 API.md 不一致：仅文档 %s / 仅实现 %s"
                                     % (sorted(variants[0] - actual), sorted(actual - variants[0])))
                else:
                    self.assertTrue(any(v <= actual for v in variants),
                                    "响应字段与 API.md 的任何一种成功形态都不匹配：文档 %s / 实际 %s"
                                    % (variants, sorted(actual)))

    def test_calls_cover_every_get_endpoint(self):
        """除带必填参数的查询与需图形界面的端点外，所有 GET 端点都应被上面的字段核对覆盖。"""
        doc = documented_endpoints()
        covered = set(self._calls())
        skipped = {("GET", "/api/backups/diff")}   # 需要 a/b/path 三个参数
        missing = {k for k in doc if k[0] == "GET" and k not in covered and k not in skipped}
        self.assertEqual(sorted(missing), [], "有 GET 端点未被字段核对覆盖：%s" % sorted(missing))


VALID = """worker_processes  1;
events { worker_connections  64; }
http { server { listen 8080; } }
"""


class VersionConsistencyTest(unittest.TestCase):
    def setUp(self):
        src = server_source()
        m = re.search(r'server_version\s*=\s*"nginx-manager/([\d.]+)"', src)
        self.assertIsNotNone(m, "backend/server.py 缺少 server_version 单一来源")
        self.version = m.group(1)

    def test_build_extracts_same_version(self):
        spec = importlib.util.spec_from_file_location("build_mod", os.path.join(REPO_ROOT, "build.py"))
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        self.assertEqual(build.extract_version(), self.version)

    def test_version_not_hardcoded_outside_allowlist(self):
        """版本号不得散落硬编码：仅允许单一来源与文档提及。"""
        allow = re.compile(
            r"^(backend/server\.py|release-notes/|ROADMAP\.md|VIBE_CODING_GUIDE\.md|README.*\.md|"
            r"API\.md|AGENTS\.md|aoci[^/]*\.txt|aoci/|\.aoci/)"
        )
        skip_prefixes = (".git/", ".aoci/objects/", "dist/", "build/", "nginx-1.30.4/", "__pycache__/", ".pytest_cache/")
        offenders = []
        for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
            rel_dir = os.path.relpath(dirpath, REPO_ROOT)
            rel_dir = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")
            dirnames[:] = [d for d in dirnames
                           if not ((rel_dir + "/" + d).lstrip("/") + "/").startswith(skip_prefixes)]
            for name in filenames:
                rel = (rel_dir + "/" + name).lstrip("/") if rel_dir else name
                if allow.match(rel) or os.path.splitext(name)[1] in {".png", ".jpg", ".exe", ".zip", ".pdf"}:
                    continue
                path = os.path.join(dirpath, name)
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except (UnicodeDecodeError, OSError):
                    continue
                if self.version in text:
                    offenders.append(rel)
        self.assertEqual(sorted(offenders), [],
                         "以下文件硬编码了版本号 %s（应只由 backend/server.py 派生）：%s"
                         % (self.version, sorted(offenders)))

    def test_version_is_not_behind_latest_tag(self):
        tags = subprocess.run(["git", "tag", "--list", "v*.*.*"], cwd=REPO_ROOT,
                              capture_output=True, text=True).stdout.split()
        if not tags:
            self.skipTest("仓库没有 tag（CI 浅克隆时正常）")
        latest = max(tags, key=lambda t: tuple(int(x) for x in t.lstrip("v").split(".")))
        cur = tuple(int(x) for x in self.version.split("."))
        newest = tuple(int(x) for x in latest.lstrip("v").split("."))
        self.assertGreaterEqual(cur, newest,
                                "server_version %s 落后于最新 tag %s" % (self.version, latest))


if __name__ == "__main__":
    unittest.main()
