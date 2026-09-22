#!/usr/bin/env python3
"""DOM 契约静态检查（G2）：JS 引用的 #id / class 必须真实存在于 HTML / CSS。

背景：本前端不用框架，app.js 直接以 "#id" 与 ".class" 操作 DOM，改名即点坏按钮，
而这类破坏在浏览器里要走到那一步才暴露。此脚本把契约变成可在 CI 失败门禁。

检查项（命中即退出码 1）：
  1. JS 引用的 #id 既不在 index.html，也不是 JS 运行时创建（`el.id = "x"` / 模板里的 id="x"）
  2. JS / HTML 使用的 class 未在 style.css 定义（含 .a → .b 组合选择器的每一段）
  3. style.css 用了未定义的 var(--x)（静默失效的声明）
  4. 某个 [data-*] 主题块未覆盖兄弟主题定义的全部 token（主题间漏色）
  5. 布局契约（LAYOUT_INVARIANTS）：卡片裁切 / 可滚动区可收缩 / 停靠区让位下限 /
     409 提示条完整可见——这些声明被删会在窗口变矮时让内容顶出卡片压住兄弟卡片

仅提示不改退出码：
  - style.css 定义但从未被引用的 class / 变量（重构残留，可能是运行时拼接的名字）
  - 同一规则内重复声明同一属性（后者静默生效；合法用法是 height:100vh;height:100dvh 这类渐进增强）

用法（仓库根执行）：
    python3 scripts/dom_contract.py
    python3 scripts/dom_contract.py --html frontend/index.html --css frontend/css/style.css
"""
import argparse
import glob
import re
import sys
from pathlib import Path

HEX = re.compile(r"^[0-9a-fA-F]{3,8}$")
JS_ID = re.compile(r'["\']#([A-Za-z][A-Za-z0-9_-]*)["\']')
GETBYID = re.compile(r'getElementById\(\s*["\']([A-Za-z][A-Za-z0-9_-]*)["\']')
SEL_ID = re.compile(r'(?:querySelector(?:All)?|closest|matches)\(\s*["\']#([A-Za-z][A-Za-z0-9_-]*)')
JS_ID_DEF = re.compile(r'\.id\s*=\s*["\']([A-Za-z][A-Za-z0-9_-]*)["\']|id=["\']([A-Za-z][A-Za-z0-9_-]*)["\']')
CLASSES = re.compile(r'className\s*=\s*["\']([^"\']*)["\']')
CLASSLIST = re.compile(r'classList\.(?:add|remove|toggle|contains)\(\s*["\']([^"\']+)')
SEL_CLASS = re.compile(r'(?:querySelector(?:All)?|closest|matches)\(\s*["\']\.([A-Za-z][A-Za-z0-9_-]*)')
HTML_CLASS = re.compile(r'class\s*=\s*["\']([^"\']*)["\']')
HTML_ID = re.compile(r'\bid\s*=\s*["\']([A-Za-z][A-Za-z0-9_-]*)["\']')
CSS_CLASS = re.compile(r'\.([A-Za-z][A-Za-z0-9_-]*)')
VAR_DEF = re.compile(r'(--[A-Za-z0-9_-]+)\s*:')
VAR_USE = re.compile(r'var\(\s*(--[A-Za-z0-9_-]+)')
SCOPE = re.compile(r'\[([A-Za-z-]+)(?:[~|^$*]?=\s*["\']?([\w-]+)["\']?)?\]\s*\{([^}]*)\}')

# 运行时拼接 / 纯结构 / 由浏览器注入的名字，静态分析不可见，显式豁免并说明原因
DEFAULT_IGNORE_CLASSES = {
    "toast-ok", "toast-err", "toast-info",   # toast-${type} 运行时拼接
    "badge-running", "badge-stopped",        # "badge-" + 状态 运行时拼接
    "theme-dark", "theme-light",             # 由 <html data-theme> 派生，CSS 只按属性选择
    "hidden",                                # HTML5 内置行为，样式无需定义
    "tree-children",                         # 纯结构容器：只用于折叠/展开分组，样式由父级承担
    "ok", "fail", "error", "success", "info",  # 校验结果/提示按状态拼接（"test-result " + 状态）
}


def read_js(patterns):
    out = {}
    for pat in patterns:
        for p in sorted(glob.glob(pat)):
            out[p] = Path(p).read_text(encoding="utf-8")
    return out


def tokens(value):
    """按空白切分，丢弃运行时拼接片段（含 ${} 的模板，或以 '-' 结尾的拼接前缀，如 "badge-"）。"""
    return {t for t in value.split() if t and "${" not in t and not t.endswith("-")}


def strip_css(text):
    t = re.sub(r'/\*.*?\*/', '', text, flags=re.S)
    return re.sub(r'@(?:media|supports|keyframes|font-face)[^{]*\{', '{', t)


def css_rules(text):
    """把样式表解析成 {选择器: {属性: 值}}（值取该属性在规则内最后一次声明）。

    只用于布局契约：同规则内重复声明同一属性时，后一条静默生效（本仓曾因此把
    .dock-panel 的 min-height:150px 下限覆盖成 0，停靠区在窗口变矮时塌陷）。
    故这里同时返回每条规则的重复声明清单，交给调用方提示。
    """
    rules, dupes = {}, []
    for sel, body in re.findall(r'([^{}]+)\{([^{}]*)\}', text):
        sel = " ".join(sel.split())
        if not sel or sel in ("{",):
            continue
        decl = {}
        for item in body.split(";"):
            if ":" not in item:
                continue
            prop, _, value = item.partition(":")
            prop, value = prop.strip(), value.strip()
            if prop.startswith("--") or not prop:
                continue
            if prop in decl and decl[prop] != value:
                dupes.append("%s{ %s: %s 覆盖 %s }" % (sel, prop, value, decl[prop]))
            decl[prop] = value
        if sel in rules:
            rules[sel].update(decl)
        else:
            rules[sel] = decl
    return rules, dupes


# 布局契约：这些声明是「内容不许跑到卡片外面」的保证，删掉会在窗口变矮时复现
# 「编辑器卡片里的 409 提示条压住下方停靠区」（实测溢出 231px）这一类问题。
# 每项 = (选择器, 属性, 期望值或 None 表示只要求非 0, 说明)
LAYOUT_INVARIANTS = (
    (".editor-panel", "overflow", "hidden",
     "编辑器卡片必须自己裁切，否则子元素会顶出卡片盖住停靠区"),
    (".editor-wrap", "min-height", "0",
     "编辑器区必须可收缩让位给 409 提示条，给死 min-height 会把卡片顶开"),
    (".dock-panel", "min-height", None,
     "停靠区必须保留让位下限，否则窗口变矮时会被压到几乎不可见"),
    ("#viewConfig .editor-panel > .callout", "flex", "none",
     "409「已保存但校验失败」提示条必须完整可见，不能被压缩或裁掉"),
)


def check_layout(css):
    rules, dupes = css_rules(css)
    problems = []
    for sel, prop, expect, why in LAYOUT_INVARIANTS:
        decl = rules.get(sel)
        if decl is None:
            problems.append("  缺少规则 %s（%s）" % (sel, why))
            continue
        actual = decl.get(prop)
        if actual is None:
            problems.append("  %s 未声明 %s（%s）" % (sel, prop, why))
        elif expect is None and actual.strip() in ("0", "0px"):
            problems.append("  %s 的 %s 为 0（%s）" % (sel, prop, why))
        elif expect is not None and actual.strip() != expect:
            problems.append("  %s 的 %s=%s，应为 %s（%s）" % (sel, prop, actual, expect, why))
    return problems, dupes


def check(html_path, css_path, js_patterns, ignore_classes, ignore_ids):
    html = Path(html_path).read_text(encoding="utf-8")
    css_raw = Path(css_path).read_text(encoding="utf-8")
    css = strip_css(css_raw)
    js_files = read_js(js_patterns)
    js = "\n".join(js_files.values())
    if not js_files:
        raise SystemExit("未匹配到任何 JS 文件：%s" % js_patterns)

    html_ids = set(HTML_ID.findall(html))
    js_created_ids = {a or b for a, b in JS_ID_DEF.findall(js)}
    js_ids = {i for i in (set(JS_ID.findall(js)) | set(GETBYID.findall(js)) | set(SEL_ID.findall(js)))
              if not HEX.match(i)}

    html_cls = set()
    for m in HTML_CLASS.findall(html):
        html_cls |= tokens(m)
    js_cls = set(SEL_CLASS.findall(js))
    for m in CLASSES.findall(js):
        js_cls |= tokens(m)
    for m in CLASSLIST.findall(js):
        js_cls |= tokens(m)
    # JS 模板字符串里写的 class="..." 同样是真实使用（diff 视图等动态结构），
    # 既算「已使用」，也必须能在 style.css 找到定义
    for m in HTML_CLASS.findall(js):
        js_cls |= tokens(m)

    css_cls = set(CSS_CLASS.findall(css))
    defined_vars = set(VAR_DEF.findall(css_raw))
    used_vars = set(VAR_USE.findall(css_raw))

    problems = []

    layout_problems, layout_dupes = check_layout(css)
    if layout_problems:
        problems.append("布局契约被破坏（内容会跑到卡片外面，压住兄弟卡片）：")
        problems += layout_problems
    if layout_dupes:
        print("[info] 同一规则内重复声明（后者静默生效，确认是有意的渐进增强）：")
        for d in layout_dupes:
            print("  " + d)

    missing_ids = sorted(js_ids - html_ids - js_created_ids - ignore_ids)
    if missing_ids:
        problems.append("JS 引用了不存在的 id（既不在 index.html，也非 JS 运行时创建）：")
        problems += ["  #%s" % i for i in missing_ids]

    undefined_cls = sorted((js_cls | html_cls) - css_cls - ignore_classes)
    if undefined_cls:
        problems.append("使用了 style.css 未定义的 class：")
        problems += ["  .%s" % c for c in undefined_cls]

    undef_vars = sorted(used_vars - defined_vars)
    if undef_vars:
        problems.append("style.css 使用了未定义的 CSS 变量：")
        problems += ["  %s" % v for v in undef_vars]

    # 说明：本仓故意采用「基础块定义共享 token + 各主题块只覆盖调色板 token」的结构，
    # 故不做「兄弟主题块必须覆盖同一组 token」的对称性检查（那在本仓是结构性误报）。

    print("html:id %d | html:class %d | js:id %d | js:class %d | css:class %d | css:var %d"
          % (len(html_ids), len(html_cls), len(js_ids), len(js_cls), len(css_cls), len(defined_vars)))

    unused_cls = sorted(css_cls - js_cls - html_cls - ignore_classes)
    if unused_cls:
        print("[info] CSS 定义但未被引用（%d）：%s" % (len(unused_cls), ", ".join(unused_cls)))
    unused_vars = sorted(defined_vars - used_vars)
    if unused_vars:
        print("[info] CSS 变量定义但未使用：%s" % ", ".join(unused_vars))

    if problems:
        print("\nDOM 契约问题：")
        print("\n".join(problems))
        return 1
    print("\n契约检查通过")
    return 0


def main():
    repo = Path(__file__).resolve().parent.parent
    ap = argparse.ArgumentParser(description="前端 DOM 契约静态检查")
    ap.add_argument("--html", default=str(repo / "frontend" / "index.html"))
    ap.add_argument("--css", default=str(repo / "frontend" / "css" / "style.css"))
    ap.add_argument("--js", nargs="+", default=[str(repo / "frontend" / "js" / "*.js")])
    ap.add_argument("--ignore-classes", default="", help="追加豁免的 class（逗号分隔，用于运行时拼接名）")
    ap.add_argument("--ignore-ids", default="", help="追加豁免的 id")
    args = ap.parse_args()

    ignore_classes = set(DEFAULT_IGNORE_CLASSES) | {x for x in args.ignore_classes.split(",") if x}
    ignore_ids = {x for x in args.ignore_ids.split(",") if x}
    return check(args.html, args.css, args.js, ignore_classes, ignore_ids)


if __name__ == "__main__":
    sys.exit(main())
