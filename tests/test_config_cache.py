"""配置读取 / 派生扫描缓存的回归：锁定「缓存只按内容身份失效」。

设计口径：缓存键是文件的 ``(mtime_ns, size, ino)``（外加 glob 扫过的目录的 mtime），
不是时间窗。因此有两类必须同时成立的断言：

1. **有效性**（本轮性能优化的核心）：配置未变时，重复调用不得再打开任何配置文件
   —— 空闲轮询（1s 一次的日志跟随）原本每秒都要把所有配置重读重扫一遍。
2. **正确性**（这批优化的安全边界）：配置一变就必须立即重新解析，包括 glob 目录里
   新增/删除被 include 的文件这种「没有任何已知文件被改动」的情形。宁可多失效一次，
   也绝不返回与磁盘不一致的日志路径 / stub_status。

第 2 类是本文件的主要分量：缓存写的是一份安全关键的数据（日志路径、stub_status 位置），
读错的后果是把「别的文件」当成当前日志展示，或去错误的端口抓指标。
"""
import builtins
import contextlib
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from helpers import write_nginx_stub  # noqa: E402
from nginxctl import NginxController  # noqa: E402
from proxymgr import ProxyManager  # noqa: E402
import proxymgr  # noqa: E402


@contextlib.contextmanager
def count_conf_opens(conf_dir):
    """统计 conf_dir 之下被打开的文件次数（``os.stat`` / ``os.scandir`` 不计）。

    这正是「轮询是否把全部配置文件重读一遍」的直接度量：命中缓存的稳态路径应当为 0。
    """
    counts = {"n": 0}
    real_open = builtins.open
    root = os.path.abspath(conf_dir) + os.sep

    def tracking_open(file, *args, **kwargs):
        try:
            name = os.fspath(file)
        except TypeError:
            name = None
        if isinstance(name, str) and os.path.abspath(name).startswith(root):
            counts["n"] += 1
        return real_open(file, *args, **kwargs)

    builtins.open = tracking_open
    try:
        yield counts
    finally:
        builtins.open = real_open


@contextlib.contextmanager
def count_scans():
    """统计「重复扫描配置」的两种工作单元：

    - ``lines``：``_strip_config_comment`` 调用次数。四个派生扫描（错误/访问日志路径、
      stub_status、listen 端口）都逐行调用它，是「是否又把所有配置文件逐行重扫一遍」的指标。
    - ``graph``：``_resolve_include_graph`` 调用次数，即「是否又重走了一遍 include 图
      （glob/scandir）」。

    只测「打开文件次数」抓不住这两类重复劳动：内容缓存命中后重扫不产生任何 open。
    """
    counts = {"lines": 0, "graph": 0}
    # 必须存取 __dict__ 里的描述符本身：若取 class 属性（已解包成普通函数）再赋回去，
    # 静态方法就退化成了实例方法（调用时会多传 self）
    strip_descriptor = NginxController.__dict__["_strip_config_comment"]
    real_strip = NginxController._strip_config_comment
    real_graph = NginxController._resolve_include_graph

    def wrapped_strip(line):
        counts["lines"] += 1
        return real_strip(line)

    def wrapped_graph(self):
        counts["graph"] += 1
        return real_graph(self)

    NginxController._strip_config_comment = staticmethod(wrapped_strip)
    NginxController._resolve_include_graph = wrapped_graph
    try:
        yield counts
    finally:
        NginxController._strip_config_comment = strip_descriptor
        NginxController._resolve_include_graph = real_graph


@contextlib.contextmanager
def count_parses():
    """统计 ``parse_proxies`` 的执行次数（代理改动路径的冗余解析度量）。"""
    counts = {"n": 0}
    real = proxymgr.parse_proxies

    def wrapped(*a, **kw):
        counts["n"] += 1
        return real(*a, **kw)

    proxymgr.parse_proxies = wrapped
    try:
        yield counts
    finally:
        proxymgr.parse_proxies = real


MAIN_CONF = (
    "worker_processes  1;\n"
    "error_log  logs/error.log;\n"
    "events { worker_connections 1024; }\n"
    "http {\n"
    "    access_log logs/access.log;\n"
    "    include conf.d/*.conf;\n"
    "}\n"
)

APP_CONF = "server {\n    listen 8081;\n    server_name a.local;\n}\n"


class ConfigCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nm-cache-")
        self.conf_dir = os.path.join(self.tmp, "conf")
        self.conf_d = os.path.join(self.conf_dir, "conf.d")
        os.makedirs(self.conf_d, exist_ok=True)
        self.stub = write_nginx_stub(self.tmp)
        self.ctl = NginxController(self.stub, self.conf_dir)
        self.main = os.path.join(self.conf_dir, "nginx.conf")
        self.write("nginx.conf", MAIN_CONF)
        self.write("conf.d/app.conf", APP_CONF)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, content):
        fp = os.path.join(self.conf_dir, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

    def _derived_snapshot(self):
        return (self.ctl.collect_included_files(), self.ctl.find_error_log_paths(),
                self.ctl.find_access_log_paths(), self.ctl.find_stub_status(),
                self.ctl.detect_listen_port())

    # ---- 1. 有效性：未变则不再打开任何配置文件 ----

    def test_steady_state_reads_no_config_files(self):
        """配置未变时，重复调用不得再打开任何配置文件（本轮优化的核心断言之一）。"""
        self._derived_snapshot()  # 预热：建立缓存
        with count_conf_opens(self.conf_dir) as counted:
            for _ in range(5):
                self._derived_snapshot()
        self.assertEqual(counted["n"], 0,
                         "稳态路径仍在重读配置文件（缓存失效了）：%d 次打开" % counted["n"])

    def test_steady_state_rescans_no_lines(self):
        """配置未变时，重复调用既不得逐行重扫、也不得重走 include 图（核心断言之二）。

        与上一条互补：只测「打开文件次数」抓不住「内容缓存命中后仍每次重扫全部行 /
        重走 glob」，而那正是空闲轮询（1s 一次）原本的主要 CPU 开销。
        """
        self._derived_snapshot()
        with count_scans() as counted:
            for _ in range(5):
                self._derived_snapshot()
        self.assertEqual(counted["lines"], 0,
                         "稳态路径仍在逐行重扫配置：%d 行次" % counted["lines"])
        self.assertEqual(counted["graph"], 0,
                         "稳态路径仍在重走 include 图（glob/scandir）：%d 次" % counted["graph"])

    def test_cache_is_actually_used_on_first_call(self):
        """反向断言：冷调用**必须**真的去扫配置、走 include 图。

        否则「稳态 0 次扫描」可能只是因为扫描函数根本没被走到（例如被短路成空结果），
        那样上面两条断言就成了自我实现的空话。
        """
        ctl = NginxController(self.stub, self.conf_dir)
        with count_scans() as counted:
            ctl.find_error_log_paths()
        self.assertGreater(counted["lines"], 0, "冷调用没有扫描配置——断言本身失效")
        self.assertGreater(counted["graph"], 0, "冷调用没有解析 include 图——断言本身失效")

    def test_repeated_calls_return_identical_results(self):
        """未变配置的重复调用必须逐项一致（缓存不得改变结果形状/顺序）。"""
        first = self._derived_snapshot()
        second = self._derived_snapshot()
        self.assertEqual(first, second)
        self.assertIn(self.main, first[0])
        self.assertIn(os.path.join(self.conf_d, "app.conf"), first[0])

    # ---- 2. 正确性：变了必须立刻看见 ----

    def test_main_conf_rewrite_invalidates_include_set(self):
        """主配置改写后，include 集合立即更新（日志跟随用例会中途改写配置）。"""
        before = self.ctl.collect_included_files()
        self.assertIn(os.path.join(self.conf_d, "app.conf"), before)

        self.write("nginx.conf", MAIN_CONF.replace("include conf.d/*.conf;", ""))
        after = self.ctl.collect_included_files()
        self.assertNotIn(os.path.join(self.conf_d, "app.conf"), after,
                         "主配置已去掉 include，缓存却仍返回旧集合")
        self.assertEqual(after, [self.main])

    def test_new_file_in_glob_dir_is_discovered(self):
        """glob 目录里**新增**被 include 的文件必须被发现（只有目录 mtime 会变）。"""
        self.ctl.collect_included_files()
        self.write("conf.d/late.conf", APP_CONF)
        files = self.ctl.collect_included_files()
        self.assertIn(os.path.join(self.conf_d, "late.conf"), files,
                      "glob 目录里新增的 include 文件未被发现——只看文件签名会漏掉这种情形")

    def test_deleted_file_in_glob_dir_is_dropped(self):
        """glob 目录里**删除**文件后，不得继续返回已不存在的路径。"""
        self.ctl.collect_included_files()
        os.remove(os.path.join(self.conf_d, "app.conf"))
        files = self.ctl.collect_included_files()
        self.assertNotIn(os.path.join(self.conf_d, "app.conf"), files)

    def test_error_log_directive_change_is_seen(self):
        """改 error_log 指令后，候选路径立即跟着变（否则会把别的文件当当前日志）。"""
        self.ctl.find_error_log_paths()
        outside = os.path.join(self.tmp, "outside", "site-error.log")
        self.write("nginx.conf", MAIN_CONF.replace("error_log  logs/error.log;",
                                                   "error_log  %s;" % outside))
        paths = self.ctl.find_error_log_paths()
        self.assertEqual(paths[0], os.path.normpath(os.path.abspath(outside)),
                         "error_log 改了，缓存却仍返回旧路径")

    def test_access_log_directive_change_is_seen(self):
        self.ctl.find_access_log_paths()
        outside = os.path.join(self.tmp, "outside", "site-access.log")
        self.write("nginx.conf", MAIN_CONF.replace("access_log logs/access.log;",
                                                   "access_log %s;" % outside))
        self.assertEqual(self.ctl.find_access_log_paths()[0],
                         os.path.normpath(os.path.abspath(outside)))

    def test_stub_status_change_is_seen(self):
        """stub_status 位置（路径 + 所在 server 的端口）必须随配置更新。"""
        self.assertIsNone(self.ctl.find_stub_status(), "初始配置没有 stub_status")
        # location 写成多行（单行 location 不是 nginx 常见写法，扫描器按块栈追踪）
        self.write("conf.d/app.conf",
                   APP_CONF.replace("listen 8081;", "listen 8099;").replace(
                       "}", "    location /st {\n        stub_status;\n    }\n}", 1))
        hit = self.ctl.find_stub_status()
        self.assertIsNotNone(hit, "新增的 stub_status 未被发现")
        self.assertEqual(hit["path"], "/st")
        self.assertEqual(hit["port"], 8099, "端口必须与 stub_status 所在 server 配对")

    def test_stub_status_removal_is_seen(self):
        """去掉 stub_status 后同样要立刻反映（不能继续返回旧位置去抓指标）。"""
        self.write("conf.d/app.conf",
                   APP_CONF.replace("}", "    location /st {\n        stub_status;\n    }\n}", 1))
        self.assertIsNotNone(self.ctl.find_stub_status())
        self.write("conf.d/app.conf", APP_CONF)
        self.assertIsNone(self.ctl.find_stub_status())

    def test_include_file_edit_is_seen(self):
        """include 文件自身被改写后，派生结果也要更新（不只主配置才触发失效）。"""
        self.ctl.find_access_log_paths()
        self.write("conf.d/app.conf", APP_CONF + "access_log /tmp/from-include.log;\n")
        self.assertIn(os.path.normpath("/tmp/from-include.log"),
                      self.ctl.find_access_log_paths())

    def test_explicit_invalidate_keeps_results_correct(self):
        """显式失效（配置写入/回滚后调用）之后，结果仍与磁盘一致。"""
        expected = self._derived_snapshot()
        self.ctl.invalidate_config_cache()
        self.assertEqual(self._derived_snapshot(), expected)

    def test_main_conf_created_after_first_lookup_is_picked_up(self):
        """主配置一开始不存在时不得把「空结果」长期缓存。

        回归：watch 集为空只可能是主配置读不到（能读到就必进 watch），而空签名恒等于
        自身——若把它当成有效缓存，就成了「启动时还没有 nginx.conf、之后创建也永远
        看不见」，配置树一直空着。"""
        empty_dir = os.path.join(self.tmp, "no-conf-yet")
        os.makedirs(empty_dir, exist_ok=True)
        ctl = NginxController(self.stub, empty_dir)
        self.assertEqual(ctl.collect_included_files(), [], "初始没有主配置，应为空")
        ctl.find_error_log_paths()          # 顺带把派生缓存也预热

        main = os.path.join(empty_dir, "nginx.conf")
        with open(main, "w", encoding="utf-8", newline="\n") as f:
            f.write(MAIN_CONF)
        self.assertEqual(ctl.collect_included_files(), [main],
                         "主配置后创建却仍返回空：空 watch 被当成有效缓存了")


class ProxyManagerCacheTest(unittest.TestCase):
    """ProxyManager 的 stat 门控与内容版本号：必须「去冗余」但不「藏外部改动」。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nm-pm-cache-")
        self.conf = os.path.join(self.tmp, "nginx.conf")
        self.two = (
            "http {\n"
            "    server {\n"
            "        location /a/ {\n"
            "            proxy_pass http://127.0.0.1:8001/;\n"
            "            #proxy_pass http://127.0.0.1:8009/;  # 备选\n"
            "        }\n"
            "        location /b/ {\n"
            "            proxy_pass http://127.0.0.1:8002/;\n"
            "        }\n"
            "    }\n"
            "}\n"
        )
        self._write(self.two)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, content):
        with open(self.conf, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

    def test_repeated_reads_do_not_reparse(self):
        """同一实例反复读列表只应解析一次（原来每次都无条件重读+重解析）。"""
        pm = ProxyManager(self.conf)
        with count_parses() as counted:
            for _ in range(5):
                pm.list_proxies()
        self.assertLessEqual(counted["n"], 1,
                             "重复读列表发生了 %d 次解析（期望 0~1 次）" % counted["n"])

    def test_reload_still_sees_external_edit(self):
        """stat 门控不得藏住外部改动：文件被外部改写后必须读到新内容。"""
        pm = ProxyManager(self.conf)
        self.assertEqual(len(pm.list_proxies()), 2)
        self._write(self.two.replace("location /b/ {", "location /c/ {"))
        paths = [p["path"] for p in pm.list_proxies()]
        self.assertIn("/c/", paths, "外部改写未被发现——stat 门控把改动藏住了")
        self.assertNotIn("/b/", paths)

    def test_commit_then_list_reflects_removal(self):
        """回归：删块 + commit 之后，同一实例的列表必须已不含被删代理。

        曾经把 commit() 的 reload() 换成「只前移签名」而漏掉重新解析，导致 blocks 比
        content 旧一拍，_has_headers 用旧行号索引新内容 → IndexError（服务端 500）。
        """
        pm = ProxyManager(self.conf)
        self.assertTrue(pm.remove("/a/")["ok"])
        pm.commit()
        self.assertEqual([p["path"] for p in pm.list_proxies()], ["/b/"])

    def test_commit_writes_and_stays_consistent(self):
        """commit 之后内存与磁盘一致（写回的就是内存内容）。"""
        pm = ProxyManager(self.conf)
        # switch 要求目标已在备选列表中（真实 API 会先自动补备选），故切到已有的注释备选
        self.assertTrue(pm.switch("/a/", "http://127.0.0.1:8009/")["ok"])
        pm.commit()
        with open(self.conf, "r", encoding="utf-8") as f:
            on_disk = f.read()
        self.assertIn("proxy_pass http://127.0.0.1:8009/;", on_disk)
        active = next(p["active"] for p in pm.list_proxies() if p["path"] == "/a/")
        self.assertEqual(active, "http://127.0.0.1:8009/")
        fresh = ProxyManager(self.conf)
        self.assertEqual(pf(pm), pf(fresh),
                         "同一实例与新建实例读出的代理列表应一致（内存与磁盘未脱节）")

    def test_pool_and_upstream_reads_share_one_parse(self):
        """池 / upstream 列表重复读取同样不应重复解析。"""
        pm = ProxyManager(self.conf)
        with count_parses() as counted:
            pm.pool_targets()
            pm.upstream_list()
            pm.pool_targets()
        self.assertLessEqual(counted["n"], 1, "重复读取发生了 %d 次解析" % counted["n"])


def pf(pm):
    """代理列表的稳定快照（用于跨实例比较）。"""
    return sorted((p["path"], p["active"], tuple(p["targets"])) for p in pm.list_proxies())


if __name__ == "__main__":
    unittest.main()
