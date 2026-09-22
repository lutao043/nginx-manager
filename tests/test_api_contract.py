"""契约与版本一致性（G5）。

1. `API.md`（唯一权威源）声明的端点与 `backend/server.py` 实际分发完全一致（双向）。
2. 端点文档里的示例响应字段，必须真实出现在实现返回的 JSON 中（防字段改名漂移）。
3. 版本号单一来源：`backend/server.py::server_version` 是唯一事实，`build.py` 的提取器、
   exe 产物名、前端资源持久化目录 `v{版本}` 都须由它派生；版本不得落后于最新发布 tag。
4. 派生的三处落地逐项断言：打包 spec 的版本提取与 exe 名、`build.py` 生成的 exe 文件名与
   版本属性、`stage_frontend` 的 `frontend/v{版本}/` 持久化目录（含旧版本与 .tmp 残留清理）。
   断言用「换一组假版本再求值一次」，硬编码字面量会导致第二次求值不变而被抓住。
"""
import fnmatch
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
from helpers import VALID_CONF, ServerTestCase  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PY = os.path.join(REPO_ROOT, "backend", "server.py")
API_MD = os.path.join(REPO_ROOT, "API.md")
BUILD_PY = os.path.join(REPO_ROOT, "build.py")
SPEC = os.path.join(REPO_ROOT, "nginx-manager.spec")
GH_BUILD_YML = os.path.join(REPO_ROOT, ".github", "workflows", "build.yml")
APP_NAME = "nginx-manager"


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def fstring_expr(text: str, anchor: str) -> str:
    """取 `anchor=f"..."` 里的表达式源码（用于换版本再求值，验证派生而非硬编码）。"""
    m = re.search(r"%s\s*=\s*(f\"[^\"]+\")" % anchor, text)
    assert m, "未找到 %s 的 f-string 赋值" % anchor
    return m.group(1)

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
        # 写端点探针要求配置里有一个多行 http server 块（POST /api/proxies 等只往多行块里追加），
        # 故先复位到 VALID_CONF：
        f.write_conf("nginx.conf", VALID_CONF)
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
                                                       {"path": "nginx.conf", "content": VALID_CONF}),
            # 写端点（曾经只有 GET 参与字段核对，写端点的响应字段长期与文档漂移）
            ("POST", "/api/proxies"): lambda: f.post("/api/proxies",
                                                     {"path": "/probe", "target": PROBE_TARGET}),
            ("PUT", "/api/proxies/switch"): lambda: f.put("/api/proxies/switch",
                                                          {"path": "/probe", "target": PROBE_TARGET}),
            ("PUT", "/api/proxies/targets"): lambda: f.put("/api/proxies/targets",
                                                           {"path": "/probe",
                                                            "targets": [PROBE_TARGET, PROBE_TARGET2],
                                                            "active": PROBE_TARGET2}),
            ("POST", "/api/proxy-pool"): lambda: f.post("/api/proxy-pool",
                                                        {"target": PROBE_TARGET3, "alias": "探针"}),
            ("PUT", "/api/proxy-pool"): lambda: f.put("/api/proxy-pool",
                                                      {"target": PROBE_TARGET3, "alias": "探针改"}),
            ("DELETE", "/api/proxy-pool"): lambda: f.delete("/api/proxy-pool",
                                                            {"target": PROBE_TARGET3}),
            ("DELETE", "/api/proxies"): lambda: f.delete("/api/proxies", {"path": "/probe"}),
            ("POST", "/api/upstreams"): lambda: f.post("/api/upstreams",
                                                       {"name": "probe_up", "method": "round_robin",
                                                        "servers": [{"address": "10.9.9.9:80"}]}),
            ("PUT", "/api/upstreams"): lambda: f.put("/api/upstreams",
                                                     {"name": "probe_up", "method": "least_conn",
                                                      "servers": [{"address": "10.9.9.9:80"}]}),
            ("DELETE", "/api/upstreams"): lambda: f.delete("/api/upstreams", {"name": "probe_up"}),
            ("POST", "/api/metrics/enable"): lambda: f.post("/api/metrics/enable",
                                                            {"path": "/probe_status"}),
            ("GET", "/api/backups/diff"): self._diff_call,
            ("POST", "/api/backups/restore"): self._restore_call,
            ("DELETE", "/api/backups"): lambda: f.delete("/api/backups", {"id": self._backup_id()}),
        }

    def _backup_id(self) -> str:
        """造一份真实备份，返回其 id（restore / delete / diff 需要已存在的备份）。"""
        f = self.fixture
        st, body = f.put("/api/config/file",
                         {"path": "nginx.conf", "content": VALID_CONF, "doBackup": True})
        self.assertLess(st, 400, "造备份失败：%s" % body)
        st, body = f.get("/api/backups")
        self.assertLess(st, 400, "读备份列表失败：%s" % body)
        backups: list = list((body or {}).get("backups") or [])
        self.assertTrue(backups, "备份列表为空，无法继续 restore/delete/diff 核对")
        return str(backups[0]["id"])

    def _restore_call(self):
        return self.fixture.post("/api/backups/restore", {"id": self._backup_id()})

    def _diff_call(self):
        bid = self._backup_id()
        return self.fixture.get("/api/backups/diff?a=%s&b=%s&path=nginx.conf" % (bid, bid))

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
        """所有 GET 端点都应被上面的字段核对覆盖（无例外清单，防漏检）。"""
        doc = documented_endpoints()
        covered = set(self._calls())
        missing = {k for k in doc if k[0] == "GET" and k not in covered}
        self.assertEqual(sorted(missing), [], "有 GET 端点未被字段核对覆盖：%s" % sorted(missing))


# 字段核对用的探针地址（127.0.0.1 上不存在的端口，仅作为配置文本，不发起连接）
PROBE_TARGET = "http://127.0.0.1:9009"
PROBE_TARGET2 = "http://127.0.0.1:9010"
PROBE_TARGET3 = "http://127.0.0.1:9011"


def version_sort_key(tag: str) -> tuple:
    """版本排序键：同号下正式版 > 预发布版；预发布之间按后缀逐段比较。

    不能用 `tuple(int(x) for x in tag.split("."))`：预发布 tag 里会出现 "0-rc"
    这种非整数段，直接抛 ValueError，会把整个版本门禁带崩。
    """
    core = tag.lstrip("v")
    pre = ""
    if "-" in core:
        core, pre = core.split("-", 1)
    nums = tuple(int(x) for x in core.split("."))
    if not pre:
        return (nums, 1, ())
    ordinal = tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[._-]", pre))
    return (nums, 0, ordinal)


class VersionConsistencyTest(unittest.TestCase):
    def setUp(self):
        src = server_source()
        m = re.search(r'server_version\s*=\s*"nginx-manager/([\d.]+(?:-[0-9A-Za-z.]+)?)"', src)
        self.assertIsNotNone(m, "backend/server.py 缺少 server_version 单一来源")
        self.version = m.group(1)

    def test_build_extracts_same_version(self):
        spec = importlib.util.spec_from_file_location("build_mod", os.path.join(REPO_ROOT, "build.py"))
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)
        self.assertEqual(build.extract_version(), self.version)

    def test_build_artifact_names_follow_version(self):
        """exe 文件名与版本属性都由 server_version 派生（换假版本再求值一次，硬编码会露）。"""
        spec = importlib.util.spec_from_file_location("build_mod", BUILD_PY)
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)

        # 产物名：dist/nginx-manager-v{version}
        expr = fstring_expr(read(BUILD_PY), "expected")
        for v in (self.version, "9.9.9"):
            self.assertEqual(eval(expr, {"version": v}), "%s-v%s" % (APP_NAME, v),
                             "build.py 的产物名未由 version 派生：%s" % expr)

        # version_info.py 里的 exe 文件名与文件属性
        tmp = tempfile.mkdtemp(prefix="nm-build-info-")
        orig_root = build.ROOT
        try:
            build.ROOT = tmp
            path = build.generate_version_info(self.version)
            self.assertTrue(os.path.isfile(path), "未生成 version_info.py")
            text = read(path)
        finally:
            build.ROOT = orig_root
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIn("u'OriginalFilename', u'%s-v%s.exe'" % (APP_NAME, self.version), text,
                      "exe 文件名未与版本一致")
        for field in ("FileVersion", "ProductVersion"):
            self.assertIn("u'%s', u'%s'" % (field, self.version), text,
                          "%s 未与 server_version 一致" % field)
        parts = (self.version.split("-", 1)[0].split(".") + ["0", "0", "0"])[:4]
        self.assertIn("filevers=(%s)" % ", ".join(parts), text, "文件版本段与版本号不一致")

    def test_spec_derives_version_and_exe_name(self):
        """打包 spec 必须与单一来源同源，且 exe 名由 APP_NAME/APP_VERSION 拼出。"""
        spec = read(SPEC)
        m = re.search(r"APP_NAME\s*=\s*\"([^\"]+)\"", spec)
        self.assertIsNotNone(m, "nginx-manager.spec 缺少 APP_NAME")
        self.assertEqual(m.group(1), APP_NAME)

        # spec 自带一份 server_version 提取正则：必须能从当前 server.py 提出同一个版本
        raws = re.findall(r"r'((?:[^'\\]|\\.)*)'", spec)
        pats = [s for s in raws if "server_version" in s]
        self.assertTrue(pats, "nginx-manager.spec 未从 server.py 提取版本号")
        hit = re.search(pats[0], server_source())
        self.assertIsNotNone(hit, "spec 的版本正则匹配不到 server_version：%s" % pats[0])
        self.assertEqual(hit.group(1), self.version, "spec 提取到的版本与单一来源不一致")

        # exe 名表达式：换两组版本求值，硬编码会两次相同
        expr = fstring_expr(spec, "name")
        ns = {"APP_NAME": APP_NAME, "APP_VERSION": "0.0.0", "_ext": ""}
        got = []
        for v in (self.version, "9.9.9"):
            ns["APP_VERSION"] = v
            got.append(eval(expr, dict(ns)))
        self.assertEqual(got, ["%s-v%s" % (APP_NAME, self.version), "%s-v9.9.9" % APP_NAME],
                         "spec 的 exe 名未由 APP_VERSION 派生：%s" % expr)

        # 与 GitHub 流水线的上传通配符一致（Gitea 侧用不带版本号的固定名）
        globs = re.findall(r"dist/nginx-manager[\w.*-]*", read(GH_BUILD_YML))
        self.assertTrue(globs, ".github/workflows/build.yml 未上传 dist/nginx-manager*")
        names = ["%s-v%s.exe" % (APP_NAME, self.version)]
        self.assertTrue(any(fnmatch.fnmatch(n, os.path.basename(g)) for n in names for g in globs),
                        "流水线通配符 %s 匹配不到 exe 名 %s" % (globs, names))

    def test_exe_frontend_persist_dir_follows_version(self):
        """exe 运行时前端资源按版本持久化到 frontend/v{版本}/，并清理旧版本与 .tmp 残留。"""
        server = importlib.import_module("server")
        tmp = tempfile.mkdtemp(prefix="nm-stage-frontend-")
        src = os.path.join(tmp, "src", "frontend")
        os.makedirs(src)
        with open(os.path.join(src, "index.html"), "w", encoding="utf-8") as f:
            f.write("<!-- probe -->")
        data_root = os.path.join(tmp, "data")
        persist = os.path.join(data_root, "frontend")
        for name in ("v0.0.1", "v0.0.2.tmp", "keep"):
            os.makedirs(os.path.join(persist, name))
        old_frozen, had_meipass = server.IS_FROZEN, hasattr(sys, "_MEIPASS")
        old_meipass = getattr(sys, "_MEIPASS", None)
        server.IS_FROZEN = True
        sys._MEIPASS = os.path.join(tmp, "src")
        try:
            dst = server.stage_frontend(data_root)
            self.assertEqual(dst, os.path.join(persist, "v%s" % self.version),
                             "持久化目录未由 server_version 派生")
            self.assertTrue(os.path.isfile(os.path.join(dst, "index.html")), "未复制到持久化目录")
            self.assertFalse(os.path.isdir(os.path.join(persist, "v0.0.1")), "旧版本目录未清理")
            self.assertFalse(os.path.isdir(os.path.join(persist, "v0.0.2.tmp")), "残留临时目录未清理")
            self.assertTrue(os.path.isdir(os.path.join(persist, "keep")), "清理误伤了非 v*/非 .tmp 目录")
            # 副本已存在时直接复用，不再重新复制
            self.assertEqual(server.stage_frontend(data_root), dst)
        finally:
            server.IS_FROZEN = old_frozen
            if had_meipass:
                sys._MEIPASS = old_meipass
            else:
                del sys._MEIPASS
            shutil.rmtree(tmp, ignore_errors=True)

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
        latest = max(tags, key=version_sort_key)
        self.assertGreaterEqual(version_sort_key("v" + self.version), version_sort_key(latest),
                                "server_version %s 落后于最新 tag %s" % (self.version, latest))

    def test_prerelease_version_supported(self):
        """预发布号（-rc.N）必须走通提取、打包命名、资源版本与 tag 排序。

        用合成版本号，不写真实预发布字面量——本文件不在版本号硬编码白名单里。
        """
        fake = "9.9.9-rc.1"
        spec = importlib.util.spec_from_file_location("build_mod", BUILD_PY)
        build = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(build)

        tmp = tempfile.mkdtemp(prefix="nm-build-info-pre-")
        orig_root = build.ROOT
        try:
            build.ROOT = tmp
            text = read(build.generate_version_info(fake))
        finally:
            build.ROOT = orig_root
            shutil.rmtree(tmp, ignore_errors=True)
        self.assertIn("u'FileVersion', u'%s'" % fake, text, "预发布后缀未写进文本版本属性")
        self.assertIn("u'OriginalFilename', u'%s-v%s.exe'" % (APP_NAME, fake), text,
                      "预发布产物名不正确")
        self.assertIn("filevers=(9, 9, 9, 0)", text, "预发布后缀不该进入资源版本数字段")

        # tag 排序：rc 小于同号正式版，rc.2 大于 rc.1，且都不越级
        self.assertLess(version_sort_key("v9.9.9-rc.1"), version_sort_key("v9.9.9"))
        self.assertLess(version_sort_key("v9.9.9-rc.1"), version_sort_key("v9.9.9-rc.2"))
        self.assertLess(version_sort_key("v9.9.9-rc.2"), version_sort_key("v9.9.10"))


if __name__ == "__main__":
    unittest.main()
