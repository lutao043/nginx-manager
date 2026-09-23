"""nginxctl 单元回归：直接调用 NginxController，覆盖 nginx -t 的解析契约。

重点锁定 nginx -t 失败输出为多行（[alert] 前缀行 + 出错行 + test failed 汇总）时，
仍能解析出 errFile / errLine —— 编辑器的「跳转到第 N 行」依赖这两个字段。
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from helpers import INVALID_DIRECTIVE, VALID_CONF, write_nginx_stub  # noqa: E402
from nginxctl import NginxController, _ps_process_name  # noqa: E402

POSIX = os.name != "nt"
requires_posix = unittest.skipUnless(POSIX, "nginx 替身脚本依赖 POSIX shell")


@requires_posix
class TestConfigParseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nm-nginxctl-")
        self.conf_dir = os.path.join(self.tmp, "conf")
        os.makedirs(self.conf_dir, exist_ok=True)
        self.stub = write_nginx_stub(self.tmp)
        self.ctl = NginxController(self.stub, self.conf_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, content):
        with open(os.path.join(self.conf_dir, "nginx.conf"), "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

    def test_success(self):
        self._write(VALID_CONF)
        ok, result = self.ctl.test_config()          # 返回 (ok, {ok, output, ...})
        self.assertTrue(ok)
        self.assertTrue(result["ok"])
        self.assertNotIn("errLine", result)

    def test_failure_reports_file_and_line(self):
        """第 3 行插入非法指令：即使输出是多行，也必须定位到第 3 行。"""
        self._write(VALID_CONF.replace("worker_processes  1;",
                                       "worker_processes  1;\n\n%s;" % INVALID_DIRECTIVE))
        ok, result = self.ctl.test_config()
        self.assertFalse(ok)
        self.assertFalse(result["ok"])
        self.assertEqual(result.get("errLine"), 3)
        self.assertTrue(result.get("errFile", "").endswith("nginx.conf"))

    def test_failure_on_last_line(self):
        content = VALID_CONF + "\n%s;\n" % INVALID_DIRECTIVE
        self._write(content)
        _ok, result = self.ctl.test_config()
        self.assertEqual(result.get("errLine"), content.count("\n"))

    def test_missing_binary(self):
        ctl = NginxController(os.path.join(self.tmp, "nope-nginx"), self.conf_dir)
        ok, result = ctl.test_config()
        self.assertFalse(ok)
        self.assertFalse(result["ok"])
        self.assertIn("不存在", result["output"])

    def test_version_parsed(self):
        self.assertEqual(self.ctl.get_version(), "1.30.4")

    def test_test_cmd_uses_list_args(self):
        """命令一律列表参数（shell=False），不得出现拼接字符串。"""
        cmd = self.ctl._test_cmd()
        self.assertIsInstance(cmd, list)
        self.assertEqual(cmd[0], self.stub)
        self.assertIn("-t", cmd)
        self.assertIn("-c", cmd)
        self.assertTrue(cmd[-1].endswith("nginx.conf"))


@requires_posix
class StubArgParseTest(unittest.TestCase):
    """替身脚本参数解析回归：路径含 -v 不得把 -t 误判成 -v 版本查询。

    回归背景：旧实现用 `case "$*" in *-v*)` 做子串匹配，而 tempfile.mkdtemp 生成的
    目录名可能含 -v（如 nginx-manager-test-v88pf896），于是 `nginx -t` 被当作版本查询，
    返回版本横幅并变成假失败 —— 测试套件曾因此间歇性失败（G1 门禁不能容忍）。
    """

    def test_dash_v_in_path_does_not_hijack_test(self):
        tmp = tempfile.mkdtemp(prefix="nm-v-check-")   # 前缀自带 -v，稳定复现旧缺陷
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        conf_dir = os.path.join(tmp, "conf")
        os.makedirs(conf_dir, exist_ok=True)
        stub = write_nginx_stub(tmp)
        with open(os.path.join(conf_dir, "nginx.conf"), "w", encoding="utf-8", newline="\n") as f:
            f.write(VALID_CONF)

        ctl = NginxController(stub, conf_dir)
        ok, result = ctl.test_config()
        self.assertTrue(ok, "路径含 -v 时 -t 被误判为版本查询：%s" % result)
        self.assertIn("test is successful", result["output"])
        self.assertEqual(ctl.get_version(), "1.30.4")


class SafeRelTest(unittest.TestCase):
    """路径校验：绝对路径与穿越必须被拒（服务端 _safe_rel 的语义镜像，纯函数级）。"""

    def test_traversal_forms_rejected(self):
        from server import Handler  # noqa: E402  （导入即注册，不启动服务）

        h = Handler.__new__(Handler)   # 不经过 socket，只测纯函数
        for bad in ["", "/etc/passwd", "../x.conf", "a/../../b.conf", "..", "./../x"]:
            self.assertIsNone(h._safe_rel(bad), "应拒绝: %r" % bad)
        for good in ["nginx.conf", "conf.d/site.conf", "a/b/c.conf"]:
            self.assertEqual(h._safe_rel(good), good)


# 带 configure 参数的替身：只用于 prefix/pid 推断用例（真实 -V 输出形状）
CONFIGURE_STUB = """#!/bin/sh
for a in "$@"; do
  case "$a" in
    -v|-V)
      echo "nginx version: nginx/1.30.4" >&2
      echo "built by clang 15.0.0" >&2
      echo "configure arguments: --prefix={prefix} --conf-path={prefix}/conf/nginx.conf --pid-path={pidpath}" >&2
      exit 0
      ;;
  esac
done
conf=""
want_conf=0
for a in "$@"; do
  case "$a" in
    -c) want_conf=1 ;;
    *) if [ "$want_conf" = "1" ]; then conf="$a"; want_conf=0; fi ;;
  esac
done
echo "nginx: configuration file $conf test is successful" >&2
exit 0
"""


@requires_posix
class PathResolutionTest(unittest.TestCase):
    """prefix 与 pid 文件路径推断。

    回归背景：confDir 的父目录在发行版布局下是错的（confDir=/etc/nginx → prefix=/etc），
    而 pid 文件常被发行版配置写成 /run/nginx.pid —— 取错会导致「在运行却报未运行」
    或干脆操作到别的实例。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nm-cfgpath-")
        self.conf_dir = os.path.join(self.tmp, "etc", "nginx")
        os.makedirs(self.conf_dir, exist_ok=True)
        self.stub = write_nginx_stub(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _conf(self, text):
        with open(os.path.join(self.conf_dir, "nginx.conf"), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)

    def _configure_stub(self, prefix, pid_path):
        path = os.path.join(self.tmp, "nginx-cfg")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(CONFIGURE_STUB.format(prefix=prefix, pidpath=pid_path))
        os.chmod(path, 0o755)
        return path

    def test_prefix_falls_back_to_parent(self):
        """替身（无 configure 输出）时沿用旧启发式，行为不劣化。"""
        ctl = NginxController(self.stub, self.conf_dir)
        self.assertEqual(ctl.prefix, os.path.dirname(self.conf_dir))

    def test_prefix_from_compile_flags(self):
        ctl = NginxController(self._configure_stub("/usr/local/nginx", "/usr/local/nginx/logs/nginx.pid"),
                              self.conf_dir)
        self.assertEqual(ctl.prefix, "/usr/local/nginx")
        self.assertEqual(ctl.get_version(), "1.30.4")

    def test_pid_directive_wins(self):
        ctl = NginxController(self.stub, self.conf_dir)
        self._conf("pid /run/nginx.pid;\n")
        self.assertEqual(ctl.pid_file_path(), "/run/nginx.pid")
        self._conf("pid        logs/nginx.pid;\n")
        self.assertEqual(ctl.pid_file_path(), os.path.join(ctl.prefix, "logs", "nginx.pid"))

    def test_pid_falls_back_to_compile_flag(self):
        ctl = NginxController(self._configure_stub("/usr/local/nginx", "/var/run/custom.pid"),
                              self.conf_dir)
        self._conf("worker_processes 1;\n")
        self.assertEqual(ctl.pid_file_path(), "/var/run/custom.pid")

    def test_signal_cmd_passes_main_conf(self):
        """-s 系列必须带 -c：不带时 nginx 读默认 conf 的 pid，会操作到另一个实例。"""
        ctl = NginxController(self.stub, self.conf_dir)
        cmd = ctl._signal_cmd("quit")
        self.assertIn("-s", cmd)
        self.assertIn("quit", cmd)
        self.assertIn("-c", cmd)
        self.assertTrue(cmd[-1].endswith("nginx.conf"))

    def test_stale_pid_file_cleanup(self):
        """空 / 非数字 / 已死 / 被无关进程占用的 pid 文件都要清掉。

        最后一种最危险：pid 被系统复用给别的进程时，UI 会显示错误的 PID，
        而且这个 pid 会被当成本实例的 master。
        """
        ctl = NginxController(self.stub, self.conf_dir)
        self._conf("pid logs/nginx.pid;\n")
        pid_file = ctl.pid_file_path()
        os.makedirs(os.path.dirname(pid_file), exist_ok=True)
        for content in ("", "not-a-pid\n", "999999999\n", "%d\n" % os.getpid()):
            with open(pid_file, "w", encoding="utf-8", newline="\n") as f:
                f.write(content)
            ctl._clean_stale_pid_file()
            self.assertFalse(os.path.exists(pid_file),
                             "残留 pid 文件未清理: %r" % content)

    def test_not_running_without_pid_file(self):
        ctl = NginxController(self.stub, self.conf_dir)
        self._conf("pid logs/nginx.pid;\n")
        self.assertFalse(ctl.is_running())


class PsProcessNameTest(unittest.TestCase):
    """`ps -o comm=` 的进程名解析（macOS 返回的是完整命令行，不是可执行名）。

    取错会导致：活着的 nginx 被判成「不是 nginx」→ 界面显示未运行、停止/重载被拒，
    点启动还会先把它的 pid 文件当残留清掉，再尝试起第二个实例。
    """

    def test_macos_full_command_line(self):
        out = "nginx: master process /opt/homebrew/bin/nginx -c /tmp/site/nginx.conf -p /tmp/site\n"
        self.assertEqual(_ps_process_name(out), "nginx")

    def test_linux_bare_name(self):
        self.assertEqual(_ps_process_name("nginx\n"), "nginx")

    def test_other_process_on_macos(self):
        self.assertEqual(_ps_process_name("sleep 30\n"), "sleep")
        self.assertEqual(_ps_process_name("/usr/bin/python3 -u server.py --port 8310\n"), "python3")

    def test_empty_output(self):
        self.assertEqual(_ps_process_name("  \n"), "")


@requires_posix
class PidIsNginxTest(unittest.TestCase):
    """`_pid_is_nginx` 必须认得出「命令行最后一个路径段不含 nginx」的活实例。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nm-pidname-")
        self.conf_dir = os.path.join(self.tmp, "conf")
        os.makedirs(self.conf_dir, exist_ok=True)
        self.stub = write_nginx_stub(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _with_ps(self, out):
        with mock.patch("nginxctl._run", return_value=(0, out, "")):
            return NginxController._pid_is_nginx(4242)

    def test_macos_started_with_dash_p(self):
        """回归：`nginx -c ... -p /tmp/nm-pidtest` 的 comm 输出里最后一段不含 nginx。"""
        self.assertTrue(self._with_ps(
            "nginx: master process ./nginx-1.30.4/sbin/nginx -c /tmp/nm-pidtest/conf/nginx.conf -p /tmp/nm-pidtest\n"))

    def test_linux_style(self):
        self.assertTrue(self._with_ps("nginx\n"))

    def test_non_nginx_process_is_rejected(self):
        self.assertFalse(self._with_ps("python3 -m http.server 8000\n"))

    def test_ps_unavailable_treated_as_unknown(self):
        with mock.patch("nginxctl._run", return_value=(1, "", "")):
            self.assertTrue(NginxController._pid_is_nginx(4242))


@requires_posix
class StubStatusScanTest(unittest.TestCase):
    """stub_status 定位：路径取最近包围 location，端口配对其所在 server。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nm-stubscan-")
        self.conf_dir = os.path.join(self.tmp, "conf")
        os.makedirs(self.conf_dir, exist_ok=True)
        self.stub = write_nginx_stub(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _conf(self, text):
        with open(os.path.join(self.conf_dir, "nginx.conf"), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return NginxController(self.stub, self.conf_dir)

    def test_port_paired_with_owning_server(self):
        """第一个 listen 是 8080，但 stub_status 在 9091 那台 server 上 —— 必须取 9091。"""
        ctl = self._conf("""http {
    server {
        listen 8080;
        location / {
            return 200 "ok";
        }
    }
    server {
        listen 127.0.0.1:9091;
        location /nginx_status {
            stub_status;
            allow 127.0.0.1;
            deny all;
        }
    }
}
""")
        stub = ctl.find_stub_status()
        self.assertEqual(stub["path"], "/nginx_status")
        self.assertEqual(stub["port"], 9091)
        self.assertEqual(ctl.detect_listen_port(), 8080)

    def test_nested_location_takes_inner_path(self):
        ctl = self._conf("""http {
    server {
        listen 8080;
        location /outer {
            location = /inner {
                stub_status;
            }
        }
    }
}
""")
        self.assertEqual(ctl.find_stub_status()["path"], "/inner")

    def test_quoted_brace_does_not_break_scan(self):
        """server 块内的引号字符串含 } 时，不能把块提前闭合、丢掉 listen 端口。"""
        ctl = self._conf("""http {
    server {
        listen [::]:8443;
        add_header X-End "}";
        location /nginx_status {
            stub_status;
        }
    }
}
""")
        stub = ctl.find_stub_status()
        self.assertEqual(stub["port"], 8443)

    def test_no_stub_status(self):
        ctl = self._conf("http {\n    server {\n        listen 80;\n    }\n}\n")
        self.assertIsNone(ctl.find_stub_status())

    def test_single_line_location_is_found(self):
        """单行写法 `location /x { stub_status; ... }` 与多行写法等价，必须同样识别。

        逐行匹配（`^\\s*stub_status\\s*;`）会整块漏掉这种形态：配置里明明有状态页，
        界面却一直报「未开启统计」，实时指标永久不可用；而且写入端认得同名 location，
        点「开启统计」还会提示成功却没改任何东西。
        """
        ctl = self._conf("""http {
    server {
        listen 127.0.0.1:53816;
        root /srv/www;
        location / { index index.html; }
        location /nginx_status { stub_status; allow 127.0.0.1; deny all; }
    }
}
""")
        stub = ctl.find_stub_status()
        self.assertIsNotNone(stub, "单行写法的 stub_status 未被识别")
        self.assertEqual(stub["path"], "/nginx_status")
        self.assertEqual(stub["port"], 53816, "端口必须取该 location 所属 server 的 listen")

    def test_single_line_server_block_is_found(self):
        ctl = self._conf("http { server { listen 8443; location /nginx_status { stub_status; } } }\n")
        stub = ctl.find_stub_status()
        self.assertIsNotNone(stub, "整行的 server/location 未被识别")
        self.assertEqual(stub["port"], 8443)
        self.assertEqual(ctl.detect_listen_port(), 8443)

    def test_block_head_on_next_line_keeps_path(self):
        """块头与 `{` 分行（`location /x` 换行再 `{`）时，路径不能丢。"""
        ctl = self._conf("""http {
    server {
        listen 8081;
        location /nginx_status
        {
            stub_status;
        }
    }
}
""")
        stub = ctl.find_stub_status()
        self.assertIsNotNone(stub)
        self.assertEqual(stub["path"], "/nginx_status")
        self.assertEqual(stub["port"], 8081)

    def test_stub_status_inside_nested_inline_block_uses_inner_path(self):
        ctl = self._conf("""http {
    server {
        listen 9000;
        location /outer { location = /inner { stub_status; } }
    }
}
""")
        stub = ctl.find_stub_status()
        self.assertEqual(stub["path"], "/inner")
        self.assertEqual(stub["port"], 9000)


@requires_posix
class ErrorLogLocateTest(unittest.TestCase):
    """错误日志定位：配置里声明的路径优先，不能一律拿 <prefix>/logs/error.log 顶替。

    真实 nginx 端到端走查发现：conf 里写了绝对路径的 error_log，界面仍展示 prefix 下那份
    （两份都存在时尤其容易看错），与访问日志的「配置优先」口径不一致。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nm-errlog-")
        self.conf_dir = os.path.join(self.tmp, "conf")
        os.makedirs(os.path.join(self.conf_dir, "logs"), exist_ok=True)
        self.stub = write_nginx_stub(self.tmp)
        # prefix 下的兜底日志（前缀目录的父目录 = prefix）
        self.prefix_log = os.path.join(self.tmp, "logs", "error.log")
        os.makedirs(os.path.dirname(self.prefix_log), exist_ok=True)
        with open(self.prefix_log, "w", encoding="utf-8") as f:
            f.write("prefix log\n")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _ctl(self, conf):
        with open(os.path.join(self.conf_dir, "nginx.conf"), "w", encoding="utf-8", newline="\n") as f:
            f.write(conf)
        return NginxController(self.stub, self.conf_dir)

    def test_config_declared_absolute_path_wins(self):
        custom = os.path.join(self.tmp, "elsewhere", "error.log")
        os.makedirs(os.path.dirname(custom), exist_ok=True)
        with open(custom, "w", encoding="utf-8") as f:
            f.write("declared log\n")
        ctl = self._ctl("error_log %s warn;\nworker_processes 1;\n" % custom)
        self.assertEqual(ctl.locate_error_log(), custom)

    def test_relative_path_resolved_against_prefix(self):
        rel_dir = os.path.join(self.tmp, "logs")
        custom = os.path.join(rel_dir, "custom-error.log")
        with open(custom, "w", encoding="utf-8") as f:
            f.write("relative log\n")
        ctl = self._ctl("error_log logs/custom-error.log;\n")
        self.assertEqual(ctl.locate_error_log(), custom)

    def test_non_file_targets_skipped(self):
        """stderr / syslog: / memory: 不是文件，跳过后退回 prefix 兜底路径。"""
        for target in ("stderr", "syslog:server=unix:/dev/log", "memory:32m"):
            ctl = self._ctl("error_log %s;\n" % target)
            self.assertEqual(ctl.locate_error_log(), self.prefix_log, "目标 %r 未跳过" % target)

    def test_missing_declared_file_falls_back(self):
        ctl = self._ctl("error_log %s;\n" % os.path.join(self.tmp, "not-there.log"))
        self.assertEqual(ctl.locate_error_log(), self.prefix_log)

    def test_no_log_at_all_returns_none(self):
        os.remove(self.prefix_log)
        ctl = self._ctl("worker_processes 1;\n")
        self.assertIsNone(ctl.locate_error_log())


if __name__ == "__main__":
    unittest.main()
