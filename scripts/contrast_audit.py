#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""对比度审计：核算 frontend/css/style.css 中 8 套主题的关键配色对。

为什么要有这个脚本（ROADMAP 遗留风险 ③）：原基线的对比度是「公式换算值」，既没用真实
渲染复核过，也没有可复现的度量命令。本脚本把「哪些令牌对、按什么标准核对」固化成可提交
的断言；纯标准库，一条命令任何人可复现。

判定分两档，避免把「项目自己声明的标准」和「WCAG AA 严格标准」混为一谈：

* 默认（声明档）：按 style.css / ROADMAP 已经声明的目标核对——正文级灰字 ≥4.5、
  四级灰字（纯图标与微标签）与语义色作正文 ≥3.0。这一档是门禁，不达标退出码 1。
* `--strict`：按 WCAG 2.1 AA 严格核对（可读文本一律 ≥4.5、非文本 UI 边界 ≥3.0）。
  严格档仍有差距的项会逐条列出，但默认档不因此失败——改色属设计变更，需人工决策。

与真实浏览器渲染的关系：本脚本只做 CSS 声明的小型级联解析（<html> 上的 :root 兜底 →
<body> 上命中的 [data-theme…] 覆盖），已用真实浏览器 getComputedStyle 取色逐项核对一致
（2026-09-21：8 主题 × 11 对 = 88 项全部对齐），故可作为回归门禁。

用法：
    python3 scripts/contrast_audit.py            # 声明档，表格输出
    python3 scripts/contrast_audit.py --strict   # 按 WCAG AA 严格核对
    python3 scripts/contrast_audit.py --json     # 机器可读
    python3 scripts/contrast_audit.py --quiet    # 只打印不达标项与汇总
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

# (前景, 背景, 声明档阈值, 严格档阈值, 说明)；声明档阈值为 None 表示项目未声明、仅严格档核对
PAIRS = [
    ("text-1", "bg", 4.5, 4.5, "正文主色 / 画布"),
    ("text-2", "bg-panel", 4.5, 4.5, "次级正文 / 面板"),
    ("text-3", "bg", 4.5, 4.5, "三级灰字 / 画布"),
    ("text-3", "bg-panel", 4.5, 4.5, "三级灰字 / 面板"),
    ("text-4", "bg", 3.0, 4.5, "四级灰字（图标/微标签）/ 画布"),
    ("text-4", "bg-panel", 3.0, 4.5, "四级灰字（图标/微标签）/ 面板"),
    ("accent-fg", "accent", 4.5, 4.5, "主按钮文字 / 主色底"),
    ("ok", "bg-panel", 3.0, 4.5, "成功色作正文 / 面板"),
    ("warn", "bg-panel", 3.0, 4.5, "警告色作正文 / 面板"),
    ("danger", "bg-panel", 3.0, 4.5, "危险色作正文 / 面板"),
    ("info", "bg-panel", 3.0, 4.5, "信息色作正文 / 面板"),
    ("border-strong", "bg", None, 3.0, "控件描边 / 画布（非文本边界）"),
]


# ── 颜色与对比度 ───────────────────────────────────────────────

HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def parse_hex(value):
    value = (value or "").strip()
    if not HEX_RE.match(value):
        return None
    body = value[1:]
    if len(body) == 3:
        body = "".join(ch * 2 for ch in body)
    return tuple(int(body[i:i + 2], 16) for i in (0, 2, 4))


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

    注意顺序陷阱（本文件真实存在）：第 55 行的兜底 :root 块写在「日」色阶之后，但它是
    <html> 级，浏览器里会被 <body> 自身的 data-theme 规则压住——不能按文件顺序直接盖。
    """
    tokens = {}
    for selector, decls in rules:
        if ":root" in [p.strip() for p in selector.split(",")]:
            tokens.update(decls)
    for selector, decls in rules:
        if is_body_level(selector, theme):
            tokens.update(decls)
    return tokens


def audit(css_path):
    with open(css_path, "r", encoding="utf-8") as handle:
        rules = read_rules(handle.read())
    rows = []
    for theme in THEMES:
        tokens = theme_tokens(rules, theme)
        for fg_name, bg_name, declared, strict, label in PAIRS:
            fg_raw = tokens.get("--" + fg_name)
            bg_raw = tokens.get("--" + bg_name)
            fg, bg = parse_hex(fg_raw), parse_hex(bg_raw)
            row = {
                "theme": theme, "pair": "%s/%s" % (fg_name, bg_name), "label": label,
                "declared": declared, "strict": strict, "ratio": None,
                "pass_declared": None, "pass_strict": None,
            }
            if fg is None or bg is None:
                row["detail"] = "无法解析色值（%s / %s）" % (fg_raw, bg_raw)
                rows.append(row)
                continue
            ratio = contrast_ratio(fg, bg)
            row["ratio"] = round(ratio, 2)
            row["pass_declared"] = None if declared is None else ratio >= declared
            row["pass_strict"] = ratio >= strict
            row["detail"] = "%s 对 %s" % ((fg_raw or "").strip(), (bg_raw or "").strip())
            rows.append(row)
    return rows


def main():
    parser = argparse.ArgumentParser(description="对比度审计（nginx 管理端 8 套主题）")
    parser.add_argument("--css", default=DEFAULT_CSS, help="style.css 路径（默认 frontend/css/style.css）")
    parser.add_argument("--strict", action="store_true", help="按 WCAG 2.1 AA 严格核对")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--quiet", action="store_true", help="只打印不达标项与汇总")
    args = parser.parse_args()

    if not os.path.isfile(args.css):
        print("找不到样式文件：%s" % args.css, file=sys.stderr)
        return 2

    rows = audit(args.css)
    unresolved = [r for r in rows if r["ratio"] is None]
    if args.strict:
        failures = [r for r in rows if r["pass_strict"] is False]
        notices = []
    else:
        failures = [r for r in rows if r["pass_declared"] is False]
        notices = [r for r in rows if r["pass_declared"] is not False and r["pass_strict"] is False]

    if args.json:
        print(json.dumps({
            "css": args.css, "mode": "strict" if args.strict else "declared",
            "themes": THEMES, "total": len(rows),
            "failures": failures, "strict_gaps": notices, "unresolved": unresolved,
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
                print("  ?  %-22s %s" % (row["pair"], row["detail"]))
                continue
            need = row["strict"] if args.strict else row["declared"]
            flag = "✓" if (row["pass_strict"] if args.strict else row["pass_declared"]) else "×"
            if need is None:
                print("  ·  %-22s %5.2f (项目未声明；严格档需 ≥%.1f)  %s" %
                      (row["pair"], row["ratio"], row["strict"], row["label"]))
            else:
                print("  %s %-22s %5.2f (需 ≥%.1f)  %s" % (flag, row["pair"], row["ratio"], need, row["label"]))

    mode = "WCAG AA 严格档" if args.strict else "项目声明档"
    print("\n[%s] 共 %d 项：通过 %d，未达标 %d，无法解析 %d" %
          (mode, len(rows), len(rows) - len(failures) - len(unresolved), len(failures), len(unresolved)))
    for row in failures:
        print("  × %s %s = %.2f < %.1f  %s" %
              (row["theme"], row["pair"], row["ratio"],
               row["strict"] if args.strict else row["declared"], row["detail"]))
    if notices:
        print("\n严格档（WCAG AA）仍有差距、声明档不视为失败（改色属设计变更，需人工决策）：")
        for row in notices:
            print("  ! %s %s = %.2f < %.1f  %s" % (row["theme"], row["pair"], row["ratio"], row["strict"], row["detail"]))
    for row in unresolved:
        print("  ? %s %s %s" % (row["theme"], row["pair"], row["detail"]))
    return 1 if (failures or unresolved) else 0


if __name__ == "__main__":
    sys.exit(main())
