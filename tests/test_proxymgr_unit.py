"""proxymgr 单元回归：锁定发布前审计复现出的解析与写入缺陷。

每条用例对应一个真实可复现的坏行为（旧实现下会静默写坏/写丢用户配置）：
  - _count_braces 把引号字符串里的 } 当成块结束；
  - `location ~ ^/a{2}$` 这类正则路径被截断成 ^/a；
  - `location /x` 与 `{` 分行时整块漏判；
  - 单行块（location/proxy_pass/} 同行）漏判、且不可行级改写；
  - 文件里 http 之后还有 stream{} 时，新块被写进 stream 的 server（语法非法）；
  - 新实例上 add() 查重失效 → 写出两个同名 location；
  - remove() 跨空行删掉上一个块的注释；
  - upstream_save 静默丢弃 zone/keepalive/注释等未建模内容；
  - 非法 extra 参数被静默丢弃；
  - 写文件非原子、且把 CRLF 配置静默改成 LF。

调用链约定（与 server.py 一致）：ProxyManager 的修改只落在内存 self.content，
调用方在 nginx -t 校验通过后调 commit() 才写盘，因此这里显式 commit。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from proxymgr import ProxyManager, parse_proxies, parse_upstreams, _count_braces  # noqa: E402

# http 块之后还有 stream 块：旧实现在全文件里找「最后一个 server {」，会命中 stream 里的 server
CONF_WITH_STREAM_LAST = """worker_processes 1;

http {
    server {
        listen 8080;
        location /api {
            proxy_pass http://127.0.0.1:8001;
        }
    }
}

stream {
    server {
        listen 9000;
        proxy_pass 127.0.0.1:9001;
    }
}
"""

CONF_QUOTED_BRACE = """http {
    server {
        listen 8080;
        location /api {
            add_header X-End "}";
            proxy_pass http://127.0.0.1:8001;
        }
        location /other {
            proxy_pass http://127.0.0.1:8002;
        }
    }
}
"""

CONF_BRACE_NEXT_LINE = """http {
    server {
        listen 8080;
        location /nl
        {
            proxy_pass http://127.0.0.1:8003;
        }
    }
}
"""

CONF_REGEX_QUANTIFIER = """http {
    server {
        listen 8080;
        location ~ ^/a{2}$ {
            proxy_pass http://127.0.0.1:8004;
        }
    }
}
"""

CONF_SINGLE_LINE = """http {
    server {
        listen 8080;
        location /one { proxy_pass http://127.0.0.1:8005; }
    }
}
"""

CONF_SINGLE_LINE_UPSTREAM = """http {
    upstream be { server 10.0.0.1:80 weight=2; }

    server {
        listen 8080;
        location /api {
            proxy_pass http://be;
        }
    }
}
"""

CONF_UPSTREAM_MANUAL = """http {
    upstream backend {
        zone backend 64k;
        keepalive 32;
        # 手动备注
        server 10.0.0.1:80;
    }

    server {
        listen 8080;
        location /api {
            proxy_pass http://backend;
        }
    }
}
"""

CONF_COMMENT_BELONGING_TO_PREV = """http {
    server {
        listen 8080;
        location /a {
            proxy_pass http://127.0.0.1:8001;
        }
        # /a 的尾部备注

        location /b {
            proxy_pass http://127.0.0.1:8002;
        }
    }
}
"""

CONF_COMMENT_ATTACHED = """http {
    server {
        listen 8080;
        location /a {
            proxy_pass http://127.0.0.1:8001;
        }
        # /c 的说明注释
        location /c {
            proxy_pass http://127.0.0.1:8002;
        }
    }
}
"""


class ProxyMgrTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nm-proxymgr-")
        self.conf = os.path.join(self.tmp, "nginx.conf")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, content, newline="\n"):
        with open(self.conf, "w", encoding="utf-8", newline="") as f:
            f.write(content if newline == "\n" else content.replace("\n", newline))

    def read(self):
        with open(self.conf, "r", encoding="utf-8") as f:
            return f.read()

    def read_bytes(self):
        with open(self.conf, "rb") as f:
            return f.read()

    def mgr(self):
        return ProxyManager(self.conf)

    def ok(self, m, result):
        """断言操作成功并提交（真实调用链里由 server.py 在 -t 通过后提交）。"""
        self.assertTrue(result["ok"], result)
        m.commit()
        return result


class TestBlockBoundaries(ProxyMgrTestBase):
    def test_quoted_brace_does_not_end_block(self):
        self.write(CONF_QUOTED_BRACE)
        blocks = parse_proxies(self.read())
        self.assertEqual([b.path for b in blocks], ["/api", "/other"])
        self.assertEqual(blocks[0].targets, ["http://127.0.0.1:8001"])

    def test_count_braces_ignores_quoted(self):
        lines = ['    location /a {', '        add_header X-E "}";', "    }"]
        self.assertEqual(_count_braces(lines, 0), 2)

    def test_brace_on_next_line(self):
        self.write(CONF_BRACE_NEXT_LINE)
        blocks = parse_proxies(self.read())
        self.assertEqual([b.path for b in blocks], ["/nl"])
        self.assertEqual(blocks[0].targets, ["http://127.0.0.1:8003"])
        self.assertEqual(self.read().split("\n")[blocks[0].end].strip(), "}")

    def test_regex_location_quantifier_keeps_full_path(self):
        self.write(CONF_REGEX_QUANTIFIER)
        blocks = parse_proxies(self.read())
        self.assertEqual([b.path for b in blocks], ["^/a{2}$"])
        self.assertEqual(blocks[0].targets, ["http://127.0.0.1:8004"])

    def test_insert_point_ignores_stream_block(self):
        self.write(CONF_WITH_STREAM_LAST)
        m = self.mgr()
        self.ok(m, m.add("/new", "http://127.0.0.1:8002"))
        content = self.read()
        # 新块必须在 http 的 server 内（stream 块之前），而不是 stream 的 server 里
        self.assertLess(content.index("location /new"), content.index("stream {"))
        self.assertLess(content.index("listen 8080"), content.index("location /new"))
        self.assertIn("proxy_pass 127.0.0.1:9001;", content)  # stream 块原样保留

    def test_enable_stub_status_ignores_stream_block(self):
        self.write(CONF_WITH_STREAM_LAST)
        m = self.mgr()
        self.ok(m, m.enable_stub_status())
        content = self.read()
        self.assertLess(content.index("location /nginx_status"), content.index("stream {"))

    def test_enable_stub_status_existing_location_reports_already(self):
        """已存在同名 location 时必须返回 already=True 且一个字节都不改。

        already 就是 API.md 里 POST /api/metrics/enable 的契约字段，界面据此提示
        「已存在、未做改动」。旧实现返回自造的 unchanged 字段、没有消费方，界面于是把
        「什么都没做」提示成「配置已写入并通过校验」。
        """
        self.write("""worker_processes 1;

http {
    server {
        listen 8080;
        location /nginx_status { stub_status; allow 127.0.0.1; deny all; }
    }
}
""")
        before = self.read()
        m = self.mgr()
        r = m.enable_stub_status()
        self.assertTrue(r.get("ok"), r)
        self.assertTrue(r.get("already"), "已存在同名 location 时必须回 already=True：%r" % (r,))
        self.assertEqual(self.read(), before, "already 分支不得改动配置")
        self.assertNotIn("backup", r)

    def test_enable_stub_status_writes_when_absent(self):
        """没有同名 location 时才真的写入，且不返回 already。"""
        self.write(CONF_WITH_STREAM_LAST)
        m = self.mgr()
        r = m.enable_stub_status()
        self.assertTrue(r.get("ok"), r)
        self.assertFalse(r.get("already"), r)
        self.assertIn("location /nginx_status", self.read() if m.content == self.read() else m.content)


class TestDuplicateDetection(ProxyMgrTestBase):
    def test_second_add_on_fresh_instance_refused(self):
        self.write(CONF_WITH_STREAM_LAST)
        m1 = self.mgr()
        self.ok(m1, m1.add("/dup", "http://127.0.0.1:8001"))
        m2 = self.mgr()  # 新实例 = server.py 每请求新建
        r = m2.add("/dup", "http://127.0.0.1:8009")
        self.assertFalse(r["ok"], r)
        self.assertIn("已存在", r["error"])
        self.assertEqual(self.read().count("location /dup"), 1)
        self.assertNotIn("8009", self.read())

    def test_second_add_same_instance_refused(self):
        self.write(CONF_WITH_STREAM_LAST)
        m = self.mgr()
        self.ok(m, m.add("/dup", "http://127.0.0.1:8001"))
        self.assertFalse(m.add("/dup", "http://127.0.0.1:8009")["ok"])
        self.assertEqual(self.read().count("location /dup"), 1)


class TestSingleLineBlocks(ProxyMgrTestBase):
    def test_single_line_location_parsed(self):
        self.write(CONF_SINGLE_LINE)
        blocks = parse_proxies(self.read())
        self.assertEqual([b.path for b in blocks], ["/one"])
        self.assertTrue(blocks[0].single_line)
        self.assertEqual(blocks[0].targets, ["http://127.0.0.1:8005"])

    def test_single_line_location_switch_and_edit_refused(self):
        self.write(CONF_SINGLE_LINE)
        before = self.read_bytes()
        m = self.mgr()
        for r in (m.switch("/one", "http://127.0.0.1:8005"),
                  m.update_targets("/one", ["http://127.0.0.1:8010"]),
                  m.pool_add("http://127.0.0.1:8011"),
                  m.pool_set_alias("http://127.0.0.1:8005", "别名"),
                  m.pool_remove("http://127.0.0.1:8005")):
            self.assertFalse(r["ok"], r)
            self.assertIn("单行写法", r["error"])
        self.assertEqual(self.read_bytes(), before)

    def test_single_line_location_remove_supported(self):
        self.write(CONF_SINGLE_LINE)
        m = self.mgr()
        self.ok(m, m.remove("/one"))
        self.assertNotIn("location /one", self.read())

    def test_single_line_upstream_parsed_and_rewritten(self):
        self.write(CONF_SINGLE_LINE_UPSTREAM)
        ups = parse_upstreams(self.read())
        self.assertEqual([u["name"] for u in ups], ["be"])
        self.assertEqual([s["address"] for s in ups[0]["servers"]], ["10.0.0.1:80"])
        self.assertEqual(ups[0]["servers"][0]["params"], ["weight=2"])

        m = self.mgr()
        self.ok(m, m.upstream_save("be", "round_robin", [{"address": "10.0.0.2:80"}]))
        content = self.read()
        self.assertIn("server 10.0.0.2:80;", content)
        self.assertNotIn("10.0.0.1:80", content)
        self.assertEqual([s["address"] for s in parse_upstreams(content)[0]["servers"]],
                         ["10.0.0.2:80"])


class TestUpstreamGuards(ProxyMgrTestBase):
    def test_manual_directives_not_silently_dropped(self):
        self.write(CONF_UPSTREAM_MANUAL)
        before = self.read_bytes()
        m = self.mgr()
        r = m.upstream_save("backend", "round_robin", [{"address": "10.0.0.9:80"}])
        self.assertFalse(r["ok"], r)
        self.assertIn("未建模", r["error"])
        self.assertEqual(self.read_bytes(), before)  # 一个字节都不许改

    def test_invalid_extra_param_refused(self):
        self.write(CONF_SINGLE_LINE_UPSTREAM)
        before = self.read_bytes()
        m = self.mgr()
        r = m.upstream_save("be", "round_robin",
                            [{"address": "10.0.0.2:80", "extra": ["a;b"]}])
        self.assertFalse(r["ok"], r)
        self.assertEqual(self.read_bytes(), before)

    def test_valid_extra_param_kept(self):
        self.write(CONF_SINGLE_LINE_UPSTREAM)
        m = self.mgr()
        self.ok(m, m.upstream_save("be", "least_conn",
                                   [{"address": "10.0.0.2:80", "extra": ["weight=2"]}]))
        content = self.read()
        self.assertIn("least_conn;", content)
        self.assertIn("server 10.0.0.2:80 weight=2;", content)
        self.assertNotIn("10.0.0.1:80", content)
        self.assertEqual(content.count("upstream be"), 1)  # 不能追加出第二个同名 upstream


class TestTargetsActive(ProxyMgrTestBase):
    """「编辑备选」里的单选必须真的决定激活行（曾经只在前端生效，保存后被忽略）。"""

    CONF = """http {
    server {
        listen 8080;
        location /api {
            proxy_pass http://127.0.0.1:8001;
            #proxy_pass http://127.0.0.1:8002;
        }
    }
}
"""

    def test_active_argument_wins(self):
        self.write(self.CONF)
        m = self.mgr()
        self.ok(m, m.update_targets("/api", ["http://127.0.0.1:8001", "http://127.0.0.1:8002"],
                                    "http://127.0.0.1:8002"))
        content = self.read()
        self.assertIn("proxy_pass http://127.0.0.1:8002;", content)
        # 8001 变成注释行（不再生效）
        self.assertIn("#proxy_pass http://127.0.0.1:8001;", content)
        self.assertEqual(parse_proxies(content)[0].active, "http://127.0.0.1:8002")

    def test_active_omitted_keeps_current(self):
        self.write(self.CONF)
        m = self.mgr()
        self.ok(m, m.update_targets("/api", ["http://127.0.0.1:8001", "http://127.0.0.1:8002",
                                             "http://127.0.0.1:8003"]))
        content = self.read()
        self.assertEqual(parse_proxies(content)[0].active, "http://127.0.0.1:8001")

    def test_active_not_in_list_falls_back_to_first(self):
        self.write(self.CONF)
        m = self.mgr()
        r = m.update_targets("/api", ["http://127.0.0.1:8001"], "http://127.0.0.1:9999")
        self.assertTrue(r["ok"], r)          # active 不在列表：退回第一条，不报错
        self.assertEqual(parse_proxies(self.read())[0].active, "http://127.0.0.1:8001")

    def test_invalid_active_refused(self):
        self.write(self.CONF)
        before = self.read_bytes()
        m = self.mgr()
        r = m.update_targets("/api", ["http://127.0.0.1:8001"], "not-a-url")
        self.assertFalse(r["ok"], r)
        self.assertEqual(self.read_bytes(), before)


class TestRemoveComments(ProxyMgrTestBase):
    def test_comment_of_previous_block_kept(self):
        self.write(CONF_COMMENT_BELONGING_TO_PREV)
        m = self.mgr()
        self.ok(m, m.remove("/b"))
        content = self.read()
        self.assertNotIn("location /b", content)
        self.assertIn("# /a 的尾部备注", content)   # 跨空行的注释属于上一个块，不能删
        self.assertIn("location /a", content)

    def test_attached_comment_removed(self):
        self.write(CONF_COMMENT_ATTACHED)
        m = self.mgr()
        self.ok(m, m.remove("/c"))
        content = self.read()
        self.assertNotIn("location /c", content)
        self.assertNotIn("# /c 的说明注释", content)
        self.assertIn("location /a", content)


class TestWriteSafety(ProxyMgrTestBase):
    def test_crlf_preserved(self):
        self.write(CONF_WITH_STREAM_LAST, newline="\r\n")
        raw = self.read_bytes()
        self.assertEqual(raw.count(b"\r\n"), raw.count(b"\n"))
        m = self.mgr()
        self.ok(m, m.add("/new", "http://127.0.0.1:8007"))
        data = self.read_bytes()
        # 写回后仍是纯 CRLF：不能把用户的 Windows 配置静默改成 LF
        self.assertEqual(data.count(b"\n"), data.count(b"\r\n"))
        self.assertIn(b"location /new", data)

    def test_lf_stays_lf(self):
        self.write(CONF_WITH_STREAM_LAST)
        m = self.mgr()
        self.ok(m, m.add("/new", "http://127.0.0.1:8007"))
        self.assertNotIn(b"\r\n", self.read_bytes())

    def test_no_tmp_leftover(self):
        self.write(CONF_WITH_STREAM_LAST)
        m = self.mgr()
        self.ok(m, m.add("/new", "http://127.0.0.1:8007"))
        self.ok(m, m.remove("/new"))
        self.assertEqual([n for n in os.listdir(self.tmp) if ".tmp-" in n], [])

    def test_path_and_target_injection_refused(self):
        self.write(CONF_WITH_STREAM_LAST)
        before = self.read_bytes()
        m = self.mgr()
        for bad_path in ("/a;b", "/a#b", '/a"b', "/a}b"):
            self.assertFalse(m.add(bad_path, "http://127.0.0.1:8001")["ok"], bad_path)
        for bad_target in ("http://127.0.0.1:8001/x;y", "http://127.0.0.1:8001/#x",
                           "http://127.0.0.1:8001/x y"):
            self.assertFalse(m.add("/ok", bad_target)["ok"], bad_target)
        self.assertEqual(self.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
