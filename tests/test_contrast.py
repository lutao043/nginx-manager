"""对比度门禁（ROADMAP 风险 ③）：8 套主题的关键配色对必须全部满足 WCAG AA。

`scripts/contrast_audit.py` 是纯标准库的实现（脚本本身可独立运行），这里把它接进双入口
测试，使 GitHub / Gitea 两侧 CI 都会拦下配色回退——此前它只是 ROADMAP 里的一条手跑命令，
改坏了没人拦。

覆盖的是「同一条 CSS 规则里真的写在一起」的邻接对：灰字四档对画布与面板、控件轮廓对
四个表面、语义色作正文对面板与自己的 -soft 芯片（两种底座）。别把这层保护删薄：
PAIRS 的条目集合被冻结在此，删/改一对就会失败。
"""
import importlib.util
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSS = os.path.join(REPO_ROOT, "frontend", "css", "style.css")
AUDIT_PY = os.path.join(REPO_ROOT, "scripts", "contrast_audit.py")

_spec = importlib.util.spec_from_file_location("contrast_audit", AUDIT_PY)
audit_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(audit_mod)

# 冻结的覆盖清单：新增主题/新增邻接时同步改这里（改小 = 门禁变薄，必须在评审里说明）
EXPECTED_PAIRS = {
    "text-1/bg", "text-2/bg-panel", "text-3/bg", "text-3/bg-panel",
    "text-4/bg", "text-4/bg-panel",
    "accent-fg/accent", "accent-fg/accent-hi",
    "border-strong/bg", "border-strong/bg-panel", "border-strong/bg-raised",
    "border-strong/bg-sunken",
    "ok/bg-panel", "ok/ok-soft@bg-panel", "ok/ok-soft@bg-sunken",
    "warn/bg-panel", "warn/warn-soft@bg-panel", "warn/warn-soft@bg-sunken",
    "danger/bg-panel", "danger/danger-soft@bg-panel", "danger/danger-soft@bg-sunken",
    "info/bg-panel", "info/info-soft@bg-panel", "info/info-soft@bg-sunken",
}
EXPECTED_THEMES = {
    "emerald-dark", "ocean-dark", "amber-dark", "rose-dark",
    "emerald-light", "ocean-light", "amber-light", "rose-light",
}


class ContrastTest(unittest.TestCase):
    def setUp(self):
        self.rows = audit_mod.audit(CSS)

    def test_every_pair_is_measured(self):
        """8 主题 × 冻结的配色对，一个不少、一个不漏解析。"""
        self.assertEqual(set(audit_mod.THEMES), EXPECTED_THEMES)
        self.assertEqual({r["pair"] for r in self.rows}, EXPECTED_PAIRS,
                         "配色对集合与冻结清单不一致（新增/删除都要同步 EXPECTED_PAIRS）")
        unresolved = [r for r in self.rows if r["ratio"] is None]
        self.assertEqual(unresolved, [], "有配色对无法解析色值：%s" % unresolved)
        self.assertEqual(len(self.rows), len(EXPECTED_PAIRS) * len(EXPECTED_THEMES))

    def test_all_pairs_meet_wcag_aa(self):
        failures = [r for r in self.rows if r["pass"] is False]
        self.assertEqual(
            [(r["theme"], r["pair"], r["ratio"], r["min"]) for r in failures], [],
            "以下配色对低于 WCAG AA 门槛：%s" % [
                "%s %s %.2f<%.1f" % (r["theme"], r["pair"], r["ratio"], r["min"]) for r in failures])

    def test_thresholds_are_wcag_aa(self):
        """阈值本身不得被调低来「让脚本变绿」：文本 4.5 / 非文本边界 3.0 是 AA 底线。"""
        for (_fg, _bg, _over, minimum, label) in audit_mod.PAIRS:
            self.assertIn(minimum, (3.0, 4.5), "出现非 AA 阈值：%s（%s）" % (minimum, label))
            if minimum == 3.0:
                self.assertIn(_fg, ("border-strong",), "只有非文本边界可用 3.0：%s（%s）" % (_fg, label))

    def test_script_gate_exit_codes(self):
        """命令行入口的退出码即门禁结论：达标 0，不达标 1。"""
        import subprocess
        import sys
        ok = subprocess.run([sys.executable, AUDIT_PY, "--quiet"], cwd=REPO_ROOT,
                            capture_output=True, text=True)
        self.assertEqual(ok.returncode, 0, "配色审计未通过：%s" % ok.stdout)


if __name__ == "__main__":
    unittest.main()
