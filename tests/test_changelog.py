"""更新历史与版本号展示（G6）：release-notes 解析 → GET /api/changelog → 界面入口。

1. 解析：版本号取自文件名（旧说明正文里没有版本号）；`## ` 标题去掉开头的版本号前缀；
   `### ` 小节收纳 `-` 列表项与散文段落（围栏代码块/引用/表格/分隔线不进历史）；行内 markdown
   原样保留（转义是前端的事）；文末链接抽取。
2. 排序：按版本号倒序（同号正式版 > 预发布版），与本文件内独立实现比对——两边一起错才不会被抓住。
3. 打包：spec 把 release-notes 作为资源打入；exe 侧持久化目录 `release-notes/v{版本}/`
   随 server_version 派生，并清理旧版本与 .tmp 残留（与 frontend 同一套机制）。
4. 版本号单一来源：接口返回的版本必须与 `backend/server.py::server_version` 一致。
5. 界面入口：顶栏版本号 + 更新历史弹窗必须真实落在 index.html / app.js / api.js 上，
   并确认前端没有任何外部网络请求（本产品离线可用、不做自动更新）。

版本号一律从单一来源派生——本文件不在版本号硬编码白名单里，不得出现版本字面量。
"""
import importlib
import os
import re
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
from helpers import ServerTestCase  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER_PY = os.path.join(REPO_ROOT, "backend", "server.py")
SPEC = os.path.join(REPO_ROOT, "nginx-manager.spec")
NOTES_DIR = os.path.join(REPO_ROOT, "release-notes")
FRONTEND = os.path.join(REPO_ROOT, "frontend")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def manager_version() -> str:
    """从单一来源取当前版本号。"""
    m = re.search(r'server_version\s*=\s*"nginx-manager/([^"]+)"', read(SERVER_PY))
    assert m is not None, "backend/server.py 未找到 server_version"
    return m.group(1)


def note_versions() -> list:
    """release-notes 下的 v*.md → 版本号（文件名即版本）。"""
    return sorted(n[1:-3] for n in os.listdir(NOTES_DIR)
                  if n.startswith("v") and n.endswith(".md"))


def independent_sort_key(version: str) -> tuple:
    """独立实现的版本排序键：正式版 > 同号预发布版，预发布之间逐段比较。"""
    core, _, pre = version.partition("-")
    nums = tuple(int(x) if x.isdigit() else 0 for x in core.split("."))
    tail = tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[._-]", pre)) if pre else ()
    return (nums, 0 if pre else 1, tail)


class ReleaseNoteParseTest(unittest.TestCase):
    """解析规则（不依赖服务进程）。"""

    def setUp(self):
        self.server = importlib.import_module("server")

    def test_version_comes_from_file_name(self):
        note = self.server.parse_release_note("没有任何标题\n", "9.9.9-rc.1")
        self.assertEqual(note["version"], "9.9.9-rc.1")

    def test_title_strips_version_prefix(self):
        note = self.server.parse_release_note("## 9.9.9-rc.1：标题正文\n", "9.9.9-rc.1")
        self.assertEqual(note["title"], "标题正文", "标题里的版本号前缀未去掉")

    def test_sections_and_items(self):
        text = "## 标题\n\n引子散文\n\n### 新增\n- 甲\n- 乙\n\n### 修复\n- 丙\n"
        note = self.server.parse_release_note(text, "9.9.9")
        self.assertEqual([s["title"] for s in note["sections"]], ["", "新增", "修复"])
        self.assertEqual(note["sections"][0]["items"], ["引子散文"], "首段散文应归入标题为空的小节")
        self.assertEqual(note["sections"][1]["items"], ["甲", "乙"])
        self.assertEqual(note["sections"][2]["items"], ["丙"])
        self.assertEqual(note["title"], "标题")

    def test_bullets_before_any_section_are_kept(self):
        """小节之前就写的列表项不能静默丢弃（归入标题为空的小节）。"""
        note = self.server.parse_release_note("## 标题\n- 甲\n- 乙\n", "9.9.9")
        self.assertEqual(len(note["sections"]), 1)
        self.assertEqual(note["sections"][0]["title"], "")
        self.assertEqual(note["sections"][0]["items"], ["甲", "乙"])

    def test_prose_paragraph_kept(self):
        """散文段落要进历史：v0.6.3 的「根因」整节只有散文，只收列表项会得到空小节。"""
        note = self.server.parse_release_note(
            "## 标题\n首段散文。\n\n### 根因\n资源解压到临时目录后可能被清理。\n", "9.9.9")
        self.assertEqual(note["sections"][0]["title"], "")
        self.assertEqual(note["sections"][0]["items"], ["首段散文。"])
        self.assertEqual(note["sections"][1]["items"], ["资源解压到临时目录后可能被清理。"])

    def test_code_fence_quote_table_skipped(self):
        """围栏代码块（含块内每行）、引用、表格、分隔线不进历史。"""
        note = self.server.parse_release_note(
            "### 小节\n- 甲\n\n```\nprobe --x\n```\n\n> 引用\n\n| a | b |\n\n---\n", "9.9.9")
        self.assertEqual(note["sections"][0]["items"], ["甲"])

    def test_inline_markdown_kept_raw(self):
        """行内标记原样返回（前端负责先转义再套白名单，不得直接 innerHTML）。"""
        note = self.server.parse_release_note("### 小节\n- **粗体** 与 `代码`\n", "9.9.9")
        self.assertEqual(note["sections"][0]["items"][0], "**粗体** 与 `代码`")

    def test_link_extracted_when_present(self):
        note = self.server.parse_release_note(
            "### 小节\n- 甲\n\n**Full Changelog**: https://example.invalid/compare/a...b\n", "9.9.9")
        self.assertEqual(note["link"], "https://example.invalid/compare/a...b")
        self.assertEqual(self.server.parse_release_note("### 小节\n- 甲\n", "9.9.9")["link"], "")

    def test_old_style_note_without_h2(self):
        """v0.3.x/v0.5.x 的旧说明：没有 `## ` 标题，正文直接以 `### 更新内容` 开头。"""
        note = self.server.parse_release_note("### 更新内容\n- 甲\n", "9.9.9")
        self.assertEqual(note["title"], "", "旧说明没有标题时 title 应为空串")
        self.assertEqual(note["sections"][0]["title"], "更新内容")

    def test_load_releases_tolerates_missing_dir(self):
        self.assertEqual(self.server.load_releases("/nonexistent/notes-dir"), [])

    def test_version_sort_key_relations(self):
        key = self.server.version_sort_key
        self.assertLess(key("9.9.9-rc.1"), key("9.9.9"))
        self.assertLess(key("9.9.9-rc.1"), key("9.9.9-rc.2"))
        self.assertLess(key("9.9.9-rc.2"), key("9.9.10"))
        self.assertEqual(key("v9.9.9"), key("9.9.9"), "带 v 前缀应等价")


class ChangelogApiTest(ServerTestCase):
    """端点语义（真实服务进程 + 真实 release-notes）。"""

    def test_fields_and_single_source_version(self):
        status, body = self.fixture.get("/api/changelog")
        self.assertEqual(status, 200, body)
        self.assertEqual(sorted(body.keys()), ["notesAvailable", "releases", "version"])
        self.assertEqual(body["version"], manager_version(),
                         "接口版本号与 server_version 不一致（必须单一来源）")
        self.assertIsInstance(body["notesAvailable"], bool)

    def test_status_exposes_manager_version(self):
        """顶栏版本号取 /api/status 的 managerVersion，必须与 changelog 同源。"""
        status, body = self.fixture.get("/api/status")
        self.assertEqual(status, 200, body)
        self.assertIn("managerVersion", body)
        self.assertEqual(body["managerVersion"], manager_version())
        self.assertNotEqual(body.get("version"), body.get("managerVersion"),
                            "「nginx 版本」与「manager 版本」是两个字段，不能混用")

    def test_releases_cover_every_note_file(self):
        status, body = self.fixture.get("/api/changelog")
        self.assertEqual(status, 200, body)
        self.assertTrue(body["notesAvailable"], "仓库自带 release-notes，notesAvailable 应为 true")
        got = sorted(r["version"] for r in body["releases"])
        self.assertEqual(got, note_versions(), "有发布说明被漏读或多读")

    def test_releases_sorted_desc_by_independent_key(self):
        status, body = self.fixture.get("/api/changelog")
        self.assertEqual(status, 200, body)
        versions = [r["version"] for r in body["releases"]]
        self.assertEqual(versions, sorted(versions, key=independent_sort_key, reverse=True),
                         "更新历史未按版本号倒序（预发布号排序易错）")
        current = manager_version()
        if current in versions:
            # 界面默认展开「当前版本」，它必须能被界面找到并置顶
            self.assertEqual(versions.index(current), 0,
                             "当前版本 %s 不在更新历史最前面" % current)

    def test_entries_are_renderable(self):
        status, body = self.fixture.get("/api/changelog")
        self.assertEqual(status, 200, body)
        for rel in body["releases"]:
            self.assertEqual(sorted(rel.keys()), ["link", "sections", "title", "version"])
            self.assertIsInstance(rel["title"], str)
            self.assertIsInstance(rel["link"], str)
            self.assertTrue(rel["sections"], "%s 没有任何小节（说明被解析空了）" % rel["version"])
            for sec in rel["sections"]:
                self.assertEqual(sorted(sec.keys()), ["items", "title"])
                self.assertTrue(sec["items"], "%s 的小节 %r 没有列表项" % (rel["version"], sec["title"]))
                for item in sec["items"]:
                    self.assertIsInstance(item, str)
                    self.assertTrue(item.strip(), "列表项不应为空串")


class ChangelogPackagingTest(unittest.TestCase):
    """打包与 exe 侧持久化。"""

    def test_spec_bundles_release_notes(self):
        spec = read(SPEC)
        self.assertIn('"release-notes"', spec, "spec 未把 release-notes 作为资源打入 exe")

    def test_stage_release_notes_follows_version(self):
        server = importlib.import_module("server")
        tmp = tempfile.mkdtemp(prefix="nm-stage-notes-")
        src = os.path.join(tmp, "src", "release-notes")
        os.makedirs(src)
        with open(os.path.join(src, "v9.9.9.md"), "w", encoding="utf-8") as f:
            f.write("### 更新内容\n- 探针\n")
        data_root = os.path.join(tmp, "data")
        persist = os.path.join(data_root, "release-notes")
        for name in ("v0.0.1", "v0.0.2.tmp", "keep"):
            os.makedirs(os.path.join(persist, name))
        old_frozen, old_notes = server.IS_FROZEN, server.NOTES_DIR
        had_meipass = hasattr(sys, "_MEIPASS")
        old_meipass = getattr(sys, "_MEIPASS", None)
        server.IS_FROZEN = True
        sys._MEIPASS = os.path.join(tmp, "src")
        server.NOTES_DIR = src
        try:
            dst = server.stage_release_notes(data_root)
            self.assertEqual(dst, os.path.join(persist, "v%s" % manager_version()),
                             "发布说明持久化目录未由 server_version 派生")
            self.assertTrue(os.path.isfile(os.path.join(dst, "v9.9.9.md")), "未复制发布说明")
            self.assertFalse(os.path.isdir(os.path.join(persist, "v0.0.1")), "旧版本目录未清理")
            self.assertFalse(os.path.isdir(os.path.join(persist, "v0.0.2.tmp")), "残留临时目录未清理")
            self.assertTrue(os.path.isdir(os.path.join(persist, "keep")), "清理误伤了非 v*/非 .tmp 目录")
            self.assertEqual(server.stage_release_notes(data_root), dst, "副本已存在时应直接复用")
        finally:
            server.IS_FROZEN, server.NOTES_DIR = old_frozen, old_notes
            if had_meipass:
                sys._MEIPASS = old_meipass
            else:
                del sys._MEIPASS
            shutil.rmtree(tmp, ignore_errors=True)


class ChangelogFrontendWiringTest(unittest.TestCase):
    """界面入口与「无自动更新」边界（静态检查，防「接口有了但页面没入口」）。"""

    def test_topbar_version_chip_and_modal_exist(self):
        html = read(os.path.join(FRONTEND, "index.html"))
        for anchor in ('id="btnVersion"', 'id="changelogModal"', 'id="changelogList"',
                       'id="changelogCurrent"'):
            self.assertIn(anchor, html, "index.html 缺少 %s" % anchor)
        app_js = read(os.path.join(FRONTEND, "js", "app.js"))
        self.assertIn('$("#btnVersion").addEventListener', app_js, "版本号未绑定点击（更新历史入口）")
        self.assertIn("api.changelog()", app_js, "更新历史未走 GET /api/changelog")
        self.assertIn("/api/changelog", read(os.path.join(FRONTEND, "js", "api.js")),
                      "api.js 未封装 changelog()")

    def test_no_update_check_endpoint(self):
        """不搞自动更新：后端不得出现升级检查端点。"""
        self.assertNotIn("/api/update", read(SERVER_PY), "本产品不做自动更新，不应存在升级检查端点")

    def test_frontend_makes_no_external_requests(self):
        """离线可用：前端脚本不得对外部主机发起请求（版本历史只读本地接口）。"""
        pattern = re.compile(r"""(?:fetch|XMLHttpRequest)\s*\(\s*[\"'`]https?://""")
        for name in ("api.js", "app.js", "ui.js"):
            js = read(os.path.join(FRONTEND, "js", name))
            self.assertIsNone(pattern.search(js), "%s 出现外部网络请求" % name)


if __name__ == "__main__":
    unittest.main()
