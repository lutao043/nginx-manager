"""nginxctl 单元回归：直接调用 NginxController，覆盖 nginx -t 的解析契约。

重点锁定 nginx -t 失败输出为多行（[alert] 前缀行 + 出错行 + test failed 汇总）时，
仍能解析出 errFile / errLine —— 编辑器的「跳转到第 N 行」依赖这两个字段。
"""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))

from helpers import INVALID_DIRECTIVE, VALID_CONF, write_nginx_stub  # noqa: E402
from nginxctl import NginxController  # noqa: E402

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


class SafeRelTest(unittest.TestCase):
    """路径校验：绝对路径与穿越必须被拒（服务端 _safe_rel 的语义镜像，纯函数级）。"""

    def test_traversal_forms_rejected(self):
        from server import Handler  # noqa: E402  （导入即注册，不启动服务）

        h = Handler.__new__(Handler)   # 不经过 socket，只测纯函数
        for bad in ["", "/etc/passwd", "../x.conf", "a/../../b.conf", "..", "./../x"]:
            self.assertIsNone(h._safe_rel(bad), "应拒绝: %r" % bad)
        for good in ["nginx.conf", "conf.d/site.conf", "a/b/c.conf"]:
            self.assertEqual(h._safe_rel(good), good)


if __name__ == "__main__":
    unittest.main()
