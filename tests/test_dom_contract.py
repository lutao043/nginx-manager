"""DOM 契约测试（G2）：把 scripts/dom_contract.py 的检查纳入 pytest / unittest 门禁。

要点：契约破坏必须在 CI 失败。故意把 index.html 里某个按钮的 id 改坏（如 btnSave → btnSaveX），
本用例与 CI 的 `python3 scripts/dom_contract.py` 步骤都必须报错。
"""
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "scripts", "dom_contract.py")
FRONTEND = os.path.join(REPO_ROOT, "frontend")


def load_module():
    spec = importlib.util.spec_from_file_location("dom_contract", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


dom_contract = load_module()


def run_check(out_dir):
    """对 out_dir 下的一份前端副本执行检查，返回 (exit_code, 输出)。"""
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = dom_contract.check(
            os.path.join(out_dir, "index.html"),
            os.path.join(out_dir, "css", "style.css"),
            [os.path.join(out_dir, "js", "*.js")],
            set(dom_contract.DEFAULT_IGNORE_CLASSES),
            set(),
        )
    return code, buf.getvalue()


class DomContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nm-dom-contract-")
        # 只复制前端三件套，检查器是纯静态分析，无需后端
        shutil.copytree(os.path.join(FRONTEND, "js"), os.path.join(self.tmp, "js"))
        os.makedirs(os.path.join(self.tmp, "css"))
        shutil.copy(os.path.join(FRONTEND, "index.html"), os.path.join(self.tmp, "index.html"))
        shutil.copy(os.path.join(FRONTEND, "css", "style.css"),
                    os.path.join(self.tmp, "css", "style.css"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _edit(self, rel, old, new, replace_all=False):
        path = os.path.join(self.tmp, rel)
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        self.assertIn(old, text, "待修改的锚点不存在：%s" % old)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text.replace(old, new) if replace_all else text.replace(old, new, 1))

    def test_current_frontend_satisfies_contract(self):
        code, out = run_check(self.tmp)
        self.assertEqual(code, 0, "当前前端应满足 DOM 契约：\n" + out)
        self.assertIn("契约检查通过", out)

    def test_broken_button_id_fails(self):
        """验收条款：故意改坏一个按钮 id，检查必须失败。"""
        self._edit("index.html", 'id="btnSave"', 'id="btnSaveX"')
        code, out = run_check(self.tmp)
        self.assertEqual(code, 1, "改坏 id 后应报错：\n" + out)
        self.assertIn("#btnSave", out)

    def test_broken_class_fails(self):
        """class 在 CSS 里被改名（全部出现处）后，检查必须失败。"""
        self._edit("css/style.css", "editor-wrap", "editor-wrap-renamed", replace_all=True)
        code, out = run_check(self.tmp)
        self.assertEqual(code, 1, "class 失去 CSS 定义后应报错：\n" + out)
        self.assertIn(".editor-wrap", out)

    def test_javascript_created_id_is_accepted(self):
        """JS 运行时创建的 id（app.js 里 `id = "btnJumpErr"`）不算契约破坏。"""
        code, out = run_check(self.tmp)
        self.assertEqual(code, 0, out)
        self.assertNotIn("#btnJumpErr", out)

    def test_removed_card_clip_fails(self):
        """布局契约：拿掉编辑器卡片的 overflow:hidden 必须失败。

        这条声明是「409 提示条顶出卡片压住停靠区」那个 bug 的修复要点之一，
        删掉它就会复现，所以它是门禁而不是风格偏好。
        """
        self._edit("css/style.css",
                   ".editor-panel{flex:1 1 auto;display:flex;flex-direction:column;min-height:260px;overflow:hidden}",
                   ".editor-panel{flex:1 1 auto;display:flex;flex-direction:column;min-height:260px}")
        code, out = run_check(self.tmp)
        self.assertEqual(code, 1, "卡片失去裁切后应报错：\n" + out)
        self.assertIn("布局契约被破坏", out)
        self.assertIn("overflow", out)

    def test_removed_editor_shrink_fails(self):
        """布局契约：编辑器区给死 min-height（不可收缩）必须失败。"""
        self._edit("css/style.css", "flex:1 1 auto;display:flex;min-height:0;min-width:0;",
                   "flex:1 1 auto;display:flex;min-height:150px;min-width:0;")
        code, out = run_check(self.tmp)
        self.assertEqual(code, 1, "编辑器区不可收缩后应报错：\n" + out)
        self.assertIn(".editor-wrap", out)

    def test_duplicate_property_in_rule_is_reported(self):
        """同一规则内重复声明同一属性只提示不失败。

        静默覆盖正是踩过的坑（.dock-panel 的 min-height:150px 被同规则后面的
        min-height:0 覆盖，停靠区在窗口变矮时塌陷），但要容忍 height:100vh;height:100dvh
        这类渐进增强写法，故降为提示。
        """
        self._edit("css/style.css", ".dock-panel{\n  flex:0 1 auto;height:clamp(220px,34%,380px);",
                   ".dock-panel{\n  min-height:300px;flex:0 1 auto;height:clamp(220px,34%,380px);")
        code, out = run_check(self.tmp)
        self.assertEqual(code, 0, "重复声明不应导致失败：\n" + out)
        self.assertIn("重复声明", out)
        self.assertIn("min-height", out)


if __name__ == "__main__":
    unittest.main()
