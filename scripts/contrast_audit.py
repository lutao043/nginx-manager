#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比度审计：核算 frontend/css/style.css 中 8 套主题的关键配色对。

为什么要有这个脚本（ROADMAP 遗留风险 ③）：原基线的对比度是「公式换算值」，既没用真实
渲染复核过，也没有可复现的度量命令。本脚本把「哪些令牌对、按什么标准核对」固化成可提交
的断言；纯标准库，一条命令任何人可复现。

判定标准：**WCAG 2.1 AA**（可读文本 ≥4.5、非文本控件边界 ≥3.0），不达标退出码 1。
2026-09-22 之前本脚本分「项目声明档 / 严格档」两档，声明档把 11px 微标签与语义色作正文
只按 3.0 要求；现已把 AA 作为唯一门禁，两档阈值统一，`--strict` 保留为兼容别名。

配色对按「同一条 CSS 规则里真的写在一起」的邻接来定，不靠想当然：
* `--text-*` 出现在画布（透明/继承）与面板/内嵌面上 → 四个灰字档对画布与面板都核对；
* `--border-strong` 只用在 `--bg-raised`（输入框/按钮/徽章）、`--bg-sunken`（编辑器/日志/
  diff）、`--bg-panel`（弹窗/卡片）与无背景（画布）上 → 四个表面逐个核对（最严的是
  `--bg-sunken`：浅色主题里它最暗，深色主题里最严的是 `--bg-raised`）；
* 语义色（ok/warn/danger/info）作正文时主要落在自己的 `-soft` 芯片上，芯片要按「叠在面板」
  与「叠在内嵌面」两种底座分别核对——芯片比纯白面板更严，只核对面板会漏判。

与真实浏览器渲染的关系：本脚本只做 CSS 声明的小型级联解析（<html> 上的 :root 兜底 →
<body> 上命中的 [data-theme…] 覆盖），已用真实浏览器 getComputedStyle 取色逐项核对一致
（2026-09-21 首次 88 项；2026-09-22 调色后重核），故可作为回归门禁。

用法：
    python3 scripts/contrast_audit.py            # 表格输出；不达标退出码 1
    python3 scripts/contrast_audit.py --quiet    # 只打印不达标项与汇总
    python3 scripts/contrast_audit.py --json     # 机器可读
    python3 scripts/contrast_audit.py --strict   # 兼容别名（与默认档同一标准）
    python3 scripts/contrast_audit.py --css <路径>
"""
import argparse
import json
import os
import re
import sys

DEFAULT_CSS = os.path.join("frontend", "css", "style.css")

THEMES = [
    "emerald-dark", "ocean-dark", "amber-dark", "rose-dark",
    "emerald-light", "ocean-light", "amber-light", "rose-light",
]

# (前景, 背景, 背景叠加底座或 None, 阈值, 说明)
PAIRS = [
    ("text-1", "bg", None, 4.5, "正文主色 / 画布"),
    ("text-2", "bg-panel", None, 4.5, "次级正文 / 面板"),
    ("text-3", "bg", None, 4.5, "三级灰字 / 画布"),
    ("text-3", "bg-panel", None, 4.5, "三级灰字 / 面板"),
    ("text-4", "bg", None, 4.5, "四级灰字（微标签）/ 画布"),
    ("text-4", "bg-panel", None, 4.5, "四级灰字（微标签）/ 面板"),
    ("accent-fg", "accent", None, 4.5, "主按钮文字 / 主色底"),
    ("accent-fg", "accent-hi", None, 4.5, "主按钮文字 / 主色悬停底"),
    ("border-strong", "bg", None, 3.0, "控件轮廓 / 画布（非文本边界）"),
    ("border-strong", "bg-panel", None, 3.0, "控件轮廓 / 面板（非文本边界）"),
    ("border-strong", "bg-raised", None, 3.0, "控件轮廓 / 抬升面（非文本边界）"),
    ("border-strong", "bg-sunken", None, 3.0, "控件轮廓 / 内嵌面（非文本边界）"),
    ("ok", "bg-panel", None, 4.5, "成功色作正文 / 面板"),
    ("ok", "ok-soft", "bg-panel", 4.5, "成功色作正文 / 芯片（叠面板）"),
    ("ok", "ok-soft", "bg-sunken", 4.5, "成功色作正文 / 芯片（叠内嵌面）"),
    ("warn", "bg-panel", None, 4.5, "警告色作正文 / 面板"),
    ("warn", "warn-soft", "bg-panel", 4.5, "警告色作正文 / 芯片（叠面板）"),
    ("warn", "warn-soft", "bg-sunken", 4.5, "警告色作正文 / 芯片（叠内嵌面）"),
    ("danger", "bg-panel", None, 4.5, "危险色作正文 / 面板"),
    ("danger", "danger-soft", "bg-panel", 4.5, "危险色作正文 / 芯片（叠面板）"),
    ("danger", "danger-soft", "bg-sunken", 4.5, "危险色作正文 / 芯片（叠内嵌面）"),
    ("info", "bg-panel", None, 4.5, "信息色作正文 / 面板"),
    ("info", "info-soft", "bg-panel", 4.5, "信息色作正文 / 芯片（叠面板）"),
    ("info", "info-soft", "bg-sunken", 4.5, "信息色作正文 / 芯片（叠内嵌面）"),
]


# ── 颜色与对比度 ───────────────────────────────────────────────

HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
RGBA_RE = re.compile(r"^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$")


def parse_hex(value):
    value = (value or "").strip()
    if not HEX_RE.match(value):
        return None
    body = value[1:]
    if len(body) == 3:
        body = "".join(ch * 2 for ch in body)
    return tuple(int(body[i:i + 2], 16) for i in (0, 2, 4))


def parse_rgba(value):
    m = RGBA_RE.match((value or "").strip())
    if not m:
        return None
    r, g, b = (int(round(float(m.group(i)))) for i in (1, 2, 3))
    alpha = float(m.group(4)) if m.group(4) is not None else 1.0
    return (r, g, b, alpha)


def composite(rgba, base):
    """把 rgba 叠到不透明底色上，返回实际呈现的 RGB。"""
    r, g, b, a = rgba
    return tuple(round(c * a + d * (1 - a)) for c, d in zip((r, g, b), base))


def relative_luminance(rgb):
    out = []
    for channel in rgb:
        c = channel / 255.0
        out.append(c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4)
    return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]


def contrast_ratio(fg, bg):
    lf, lb = relative_luminance(fg), relative_luminance(bg)
    hi, lo = max(lf, lb), min(lf, lb)
    return (hi + 0.05) / (lo + 0.05)


# ── 极简 CSS 级联解析（只认本文件用到的选择器形态）──────────────

RULE_RE = re.compile(r"([^{}]+)\{([^{}]*)\}", re.S)
DECL_RE = re.compile(r"(--[A-Za-z0-9-]+)\s*:\s*([^;]+)", re.S)


def read_rules(css_text):
    """返回 [(selector, {token: value})]；忽略注释与 @ 规则。"""
    text = re.sub(r"/\*.*?\*/", "", css_text, flags=re.S)
    rules = []
    for selector, body in RULE_RE.findall(text):
        selector = selector.strip()
        if not selector or selector.startswith("@"):
            continue
        tokens = {}
        for name, value in DECL_RE.findall(body):
            tokens[name] = value.strip()
        if tokens:
            rules.append((selector, tokens))
    return rules


def is_body_level(selector, theme):
    """本文件两种层级：`:root` 落在 <html>（对 <body> 只能是继承值），
    `[data-theme…]` 落在 <body>（自身声明恒胜过继承）。返回 True 表示 body 级。"""
    for part in [p.strip() for p in selector.split(",")]:
        if part == ":root":
            continue
        if part == '[data-theme*="dark"]' and theme.endswith("-dark"):
            return True
        if part == '[data-theme*="light"]' and theme.endswith("-light"):
            return True
        if part == '[data-theme="%s"]' % theme:
            return True
    return False


def theme_tokens(rules, theme):
    """级联：先用 <html> 上的 :root 兜底，再用 <body> 上命中的 [data-theme…] 按文件顺序覆盖。

    注意顺序陷阱（本文件真实存在）：兜底 :root 块写在「日」色阶之后，但它是 <html> 级，
    浏览器里会被 <body> 自身的 data-theme 规则压住——不能按文件顺序直接盖。
    """
    tokens = {}
    for selector, decls in rules:
        if ":root" in [p.strip() for p in selector.split(",")]:
            tokens.update(decls)
    for selector, decls in rules:
        if is_body_level(selector, theme):
            tokens.update(decls)
    return tokens


def resolve_background(tokens, bg_name, over_name):
    """解析背景：纯 hex 直接给出；`rgba(…)` 按 over 底座合成（芯片类背景）。"""
    raw = tokens.get("--" + bg_name)
    rgb = parse_hex(raw)
    if rgb is not None:
        return rgb, (raw or "").strip()
    rgba = parse_rgba(raw)
    if rgba is None:
        return None, str(raw)
    base = parse_hex(tokens.get("--" + (over_name or "")))
    if base is None:
        return None, "%s（缺少可合成的底座）" % raw
    return composite(rgba, base), "%s 叠 %s" % (raw.strip(), tokens.get("--" + over_name))


def audit(css_path):
    with open(css_path, "r", encoding="utf-8") as handle:
        rules = read_rules(handle.read())
    rows = []
    for theme in THEMES:
        tokens = theme_tokens(rules, theme)
        for fg_name, bg_name, over, minimum, label in PAIRS:
            fg_raw = tokens.get("--" + fg_name)
            fg = parse_hex(fg_raw)
            bg, bg_detail = resolve_background(tokens, bg_name, over)
            row = {
                "theme": theme,
                "pair": "%s/%s" % (fg_name, bg_name) if not over else "%s/%s@%s" % (fg_name, bg_name, over),
                "label": label, "min": minimum, "ratio": None, "pass": None, "detail": "",
            }
            if fg is None or bg is None:
                row["detail"] = "无法解析色值（%s / %s）" % (fg_raw, bg_detail)
                rows.append(row)
                continue
            ratio = contrast_ratio(fg, bg)
            row["ratio"] = round(ratio, 2)
            row["pass"] = ratio >= minimum
            row["detail"] = "%s 对 %s" % ((fg_raw or "").strip().lower(), bg_detail.lower())
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description="对比度审计（nginx 管理端 8 套主题，WCAG AA）")
    parser.add_argument("--css", default=DEFAULT_CSS, help="style.css 路径（默认 frontend/css/style.css）")
    parser.add_argument("--strict", action="store_true",
                        help="兼容别名：2026-09-22 起默认档即 WCAG AA 严格档")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--quiet", action="store_true", help="只打印不达标项与汇总")
    args = parser.parse_args()

    if not os.path.isfile(args.css):
        print("找不到样式文件：%s" % args.css, file=sys.stderr)
        return 2

    rows = audit(args.css)
    unresolved = [r for r in rows if r["ratio"] is None]
    failures = [r for r in rows if r["pass"] is False]

    if args.json:
        print(json.dumps({
            "css": args.css, "mode": "wcag-aa", "themes": THEMES, "total": len(rows),
            "failures": failures, "unresolved": unresolved,
            "min_ratio": min([r["ratio"] for r in rows if r["ratio"] is not None] or [0]),
        }, ensure_ascii=False, indent=1))
        return 1 if (failures or unresolved) else 0

    if not args.quiet:
        current = None
        for row in rows:
            if row["theme"] != current:
                current = row["theme"]
                print("\n── %s ──" % current)
            if row["ratio"] is None:
                print("  ?  %-24s %s" % (row["pair"], row["detail"]))
                continue
            flag = "✓" if row["pass"] else "×"
            print("  %s %-24s %5.2f (需 ≥%.1f)  %s" % (flag, row["pair"], row["ratio"], row["min"], row["label"]))

    print("\n[WCAG AA] 共 %d 项：通过 %d，未达标 %d，无法解析 %d" %
          (len(rows), len(rows) - len(failures) - len(unresolved), len(failures), len(unresolved)))
    for row in failures:
        print("  × %s %s = %.2f < %.1f  %s" % (row["theme"], row["pair"], row["ratio"], row["min"], row["detail"]))
    for row in unresolved:
        print("  ? %s %s %s" % (row["theme"], row["pair"], row["detail"]))
    return 1 if (failures or unresolved) else 0


if __name__ == "__main__":
    sys.exit(main())
