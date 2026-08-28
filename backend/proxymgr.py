# -*- coding: utf-8 -*-
"""proxymgr.py — nginx 反向代理管理（解析/修改 nginx.conf 中的 location+proxy_pass 块）

设计约定（与 API.md 契约一致）：
  - 一个代理 = 一个 `location <path> { ... }` 块，块内必须有 proxy_pass 指令。
  - 激活目标 = 块内唯一未注释的 `proxy_pass <url>;` 行。
  - 备选目标 = 块内 `#proxy_pass <url>;` 注释行（切换 = 互换注释状态）。
  - 目标地址池 = 全部代理 proxy_pass 目标的并集（含注释备选），直接读写配置文件：
    池条目别名存于 proxy_pass 行尾注释（`proxy_pass http://a; # 别名`）。
  - 只识别包含 proxy_pass 的 location 块；静态资源 location 不进入代理列表。

修改一律"整文件文本替换 + nginx -t 校验"，失败恢复原文（调用方负责备份/校验编排）。
"""
from __future__ import annotations

import os
import re
from typing import List, Optional

# 行尾别名注释：`proxy_pass http://a; # 别名`（'#' 前须有空白，避免误伤 URL 中的 '#'）
PROXY_PASS_RE = re.compile(r"^(\s*)(#\s*)?proxy_pass\s+(.+?);\s*(?:#\s*(.*?))?\s*$")
LOCATION_RE = re.compile(r"^(\s*)location\s+(.+?)\s*\{")


class ProxyBlock:
    """一个代理 location 块的行级模型。"""

    def __init__(self, path: str, start: int, end: int):
        self.path = path          # location 路径
        self.start = start        # location 行索引（含）
        self.end = end            # 结束 } 行索引（含）
        self.pp_lines: List[int] = []      # proxy_pass 行索引（含注释）
        self.pp_active: Optional[int] = None  # 激活行索引
        self.pp_values: dict = {}          # 行索引 -> url
        self.pp_comments: dict = {}        # 行索引 -> 行尾别名注释

    @property
    def active(self) -> Optional[str]:
        return self.pp_values.get(self.pp_active) if self.pp_active is not None else None

    @property
    def targets(self) -> List[str]:
        """按配置顺序返回全部目标地址。"""
        return [self.pp_values[i] for i in sorted(self.pp_lines)]

    def alias_of(self, url: str) -> str:
        """返回该 url 的行尾别名注释（多行同 url 时取第一个非空）。"""
        for idx in sorted(self.pp_lines):
            if self.pp_values.get(idx) == url and self.pp_comments.get(idx):
                return self.pp_comments[idx]
        return ""


def _strip_inline_comment(line: str) -> str:
    """去掉行内注释：仅当 '#' 前为空白或行首时才视为注释起点，
    避免误伤 proxy_pass URL 中的 '#'（如 http://host/path#frag）。"""
    out = []
    for i, ch in enumerate(line):
        if ch == "#" and (i == 0 or line[i - 1] in " \t"):
            break
        out.append(ch)
    return "".join(out)


def _count_braces(lines: List[str], start: int) -> int:
    """从 start 行开始统计大括号平衡，返回块结束行索引（含）。"""
    depth = 0
    for i in range(start, len(lines)):
        stripped = _strip_inline_comment(lines[i])
        depth += stripped.count("{") - stripped.count("}")
        if depth <= 0:
            return i
    return len(lines) - 1


def parse_proxies(content: str) -> List[ProxyBlock]:
    """解析配置文本，返回所有包含 proxy_pass 的 location 块（按出现顺序）。"""
    lines = content.split("\n")
    blocks: List[ProxyBlock] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("#"):
            i += 1
            continue
        m = LOCATION_RE.match(line)
        if not m:
            i += 1
            continue
        path_expr = m.group(2).strip()
        # 提取 location 路径：跳过 = ~ ~* ^~ 修饰符
        parts = path_expr.split()
        if parts and parts[0] in ("=", "~", "~*", "^~"):
            path = parts[1] if len(parts) > 1 else ""
        else:
            path = parts[0] if parts else ""
        end = _count_braces(lines, i)
        # 块内扫描 proxy_pass
        block = ProxyBlock(path, i, end)
        for j in range(i + 1, end):
            pm = PROXY_PASS_RE.match(lines[j])
            if pm:
                url = pm.group(3).strip()
                block.pp_lines.append(j)
                block.pp_values[j] = url
                if pm.group(4):
                    block.pp_comments[j] = pm.group(4).strip()
                if not pm.group(2):  # 未注释 → 激活
                    block.pp_active = j
        if block.pp_lines:
            blocks.append(block)
        i = end + 1
    return blocks


# ---------- 修改操作 ----------

_URL_RE = re.compile(
    r"^(?:https?://[a-zA-Z0-9._\-]+(?::\d{1,5})?(?:/[^\s{}]*)?|unix:/[^\s{}]+)$"
)


def _normalize_target(target: str) -> Optional[str]:
    """校验目标地址（新增/编辑备选、池条目时使用，须为 nginx 合法 proxy_pass 参数）。
    规则：
    - 必须 http:// 或 https:// 或 unix:/ 开头；
    - host 为合法主机名/IPv4，可带 :端口（1-5 位数字）；
    - 可带路径；拒绝含空白 / {} / 任意乱输字符串。
    注意：裸 ip:port（无 http://）不是合法 proxy_pass，注释里历史遗留的
    裸地址可以显示/尝试切换，但会被 nginx -t 拦截并回滚。"""
    t = target.strip()
    if not t or re.search(r"\s", t) or "{" in t or "}" in t:
        return None
    if not _URL_RE.fullmatch(t):
        return None
    return t


def _normalize_path(path: str) -> Optional[str]:
    p = path.strip()
    if not p.startswith("/") or re.search(r"\s", p) or "{" in p or "}" in p:
        return None
    return p


def _sanitize_alias(alias: str) -> str:
    """别名仅作为行尾注释文本：压平所有空白字符，防止破坏行结构。"""
    return " ".join((alias or "").split())


# 池去重口径下的默认端口（省略视为同一地址）
_POOL_DEFAULT_PORTS = {"http": "80", "https": "443"}


def _pool_key(target: str) -> str:
    """地址池去重的规范化键，消除等价写法导致的重复条目：
    - scheme/host 不区分大小写（HTTP://A ↔ http://a）；
    - 省略默认端口（http :80 / https :443）；
    - 路径保留原大小写，仅去掉末尾 '/'（http://a:8000/ ↔ http://a:8000）；
    - unix: socket 路径整体小写后比较。"""
    t = (target or "").strip()
    m = re.match(r"^(https?)://([^/?#]+)([^#]*)$", t, re.IGNORECASE)
    if not m:
        return t.lower()
    scheme = m.group(1).lower()
    hostport = m.group(2).lower()
    path = (m.group(3) or "").rstrip("/")
    if ":" in hostport:
        host, port = hostport.rsplit(":", 1)
    else:
        host, port = hostport, ""
    if _POOL_DEFAULT_PORTS.get(scheme) == port:
        port = ""
    hostport = host + (":" + port if port else "")
    return f"{scheme}://{hostport}{path}"


def _pp_line(indent: str, commented: bool, url: str, alias: str = "") -> str:
    """渲染一行 proxy_pass（可带注释前缀与行尾别名注释）。"""
    prefix = "#" if commented else ""
    suffix = f" # {alias}" if alias else ""
    return f"{indent}{prefix}proxy_pass {url};{suffix}"


def _render_switch(lines: List[str], block: ProxyBlock, target: str) -> bool:
    """把 target 切换为激活（取消其注释、注释掉原激活行）。返回是否成功。"""
    target_line = None
    for idx in block.pp_lines:
        if block.pp_values[idx] == target:
            target_line = idx
            break
    if target_line is None:
        return False
    old_active = block.pp_active
    for idx in block.pp_lines:
        m = PROXY_PASS_RE.match(lines[idx])
        if not m:
            continue
        indent, commented, url = m.group(1), bool(m.group(2)), m.group(3).strip()
        alias = block.pp_comments.get(idx, "")
        if idx == target_line:
            lines[idx] = _pp_line(indent, False, url, alias)
        elif idx == old_active and idx != target_line:
            lines[idx] = _pp_line(indent, True, url, alias)
    return True


def _render_targets(lines: List[str], block: ProxyBlock, targets: List[str]) -> Optional[str]:
    """按新 targets 列表重写块内 proxy_pass 行（激活项不注释，其余注释）。
    若原激活目标不在新列表，则激活第一个。返回新的激活 url；失败返回 None。"""
    active_url = block.active
    if active_url not in targets:
        active_url = targets[0]

    # 删除块内所有旧 proxy_pass 行，换成新行（沿用块内原 proxy_pass 行的缩进）
    new_lines = []
    inserted = False
    indent = "    "
    if block.pp_lines:
        m = PROXY_PASS_RE.match(lines[block.pp_lines[0]])
        if m and m.group(1):
            indent = m.group(1)
    for j in range(block.start, block.end + 1):
        if j in block.pp_lines:
            if not inserted:
                for url in targets:
                    alias = block.alias_of(url)
                    new_lines.append(_pp_line(indent, url != active_url, url, alias))
                inserted = True
            # 跳过旧行
            continue
        new_lines.append(lines[j])
    if not inserted:
        return None
    lines[block.start : block.end + 1] = new_lines
    return active_url


def _block_text(block: ProxyBlock, lines: List[str]) -> str:
    return "\n".join(lines[block.start : block.end + 1])


def _find_insert_point(lines: List[str]) -> Optional[int]:
    """找最后一个顶层 server 块的结束 } 行索引（代理追加到该块内）。"""
    server_starts = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("#"):
            i += 1
            continue
        if re.match(r"^\s*server\s*\{", line):
            server_starts.append(i)
        i += 1
    if not server_starts:
        return None
    last_start = server_starts[-1]
    return _count_braces(lines, last_start)


def _proxy_template(path: str, target: str) -> str:
    return (
        f"        location {path} {{\n"
        f"            proxy_pass {target};\n"
        f"            proxy_set_header Host $host;\n"
        f"            proxy_set_header X-Real-IP $remote_addr;\n"
        f"            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
        f"        }}\n"
    )


class ProxyManager:
    """对单个配置文件做代理增删改；所有操作返回 (ok, result)。"""

    def __init__(self, conf_path: str):
        self.conf_path = conf_path
        self.content = self._read()
        self.blocks: List[ProxyBlock] = []

    def _read(self) -> str:
        with open(self.conf_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def reload(self) -> None:
        self.content = self._read()
        self._refresh()

    def _refresh(self) -> None:
        """从当前内存 content 重新解析 blocks（不读磁盘）。"""
        self.blocks = parse_proxies(self.content)

    def _write(self, content: str) -> None:
        with open(self.conf_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(content)

    def list_proxies(self) -> List[dict]:
        self.reload()
        out = []
        for b in self.blocks:
            # 无激活 proxy_pass 的块（如 alias 静态目录）不是代理，跳过
            if b.active is None:
                continue
            out.append({
                "path": b.path,
                "active": b.active,
                "targets": b.targets,
                "proxyHeaders": self._has_headers(b),
            })
        return out

    def _has_headers(self, block: ProxyBlock) -> bool:
        for j in range(block.start, block.end + 1):
            line = self.content.split("\n")[j]
            if "proxy_set_header" in line:
                return True
        return False

    # ---- 操作：调用方负责备份与 nginx -t 编排，失败时调用 restore() ----

    def add(self, path: str, target: str) -> dict:
        path = _normalize_path(path)
        target = _normalize_target(target)
        if not path or not target:
            return {"ok": False, "error": "path 或 target 非法"}
        lines = self.content.split("\n")
        # 查重
        for b in self.blocks:
            if b.path == path:
                return {"ok": False, "error": f"代理已存在: {path}"}
        insert_at = _find_insert_point(lines)
        if insert_at is None:
            return {"ok": False, "error": "未找到 server 块，无法添加代理"}
        block = _proxy_template(path, target).rstrip("\n")
        lines.insert(insert_at, block)
        self.content = "\n".join(lines)
        self._refresh()
        return {"ok": True, "proxy": {"path": path, "active": target, "targets": [target], "proxyHeaders": True}}

    def switch(self, path: str, target: str) -> dict:
        """切换激活目标。target 必须已是该代理备选列表中的值（含历史遗留的
        裸地址）；写入后由调用方 nginx -t 校验，失败自动回滚。"""
        target = target.strip()
        if not target:
            return {"ok": False, "error": "target 不能为空"}
        self._refresh()
        lines = self.content.split("\n")
        block = next((b for b in self.blocks if b.path == path), None)
        if block is None:
            return {"ok": False, "error": f"代理不存在: {path}"}
        if target not in block.targets:
            return {"ok": False, "error": f"目标不在备选列表中: {target}"}
        if not _render_switch(lines, block, target):
            return {"ok": False, "error": "切换失败"}
        self.content = "\n".join(lines)
        return {"ok": True}

    def update_targets(self, path: str, targets: List[str]) -> dict:
        norm, seen_keys = [], set()
        for t in targets:
            nt = _normalize_target(t)
            if not nt:
                return {"ok": False, "error": f"target 非法: {t}"}
            k = _pool_key(nt)
            if k in seen_keys:  # 同一地址（含等价写法）只保留一条
                continue
            seen_keys.add(k)
            norm.append(nt)
        if not norm:
            return {"ok": False, "error": "targets 不能为空"}
        self._refresh()
        lines = self.content.split("\n")
        block = next((b for b in self.blocks if b.path == path), None)
        if block is None:
            return {"ok": False, "error": f"代理不存在: {path}"}
        new_active = _render_targets(lines, block, norm)
        if new_active is None:
            return {"ok": False, "error": "更新备选失败"}
        self.content = "\n".join(lines)
        return {"ok": True, "active": new_active, "targets": norm, "path": path}

    def remove(self, path: str) -> dict:
        self._refresh()
        lines = self.content.split("\n")
        block = next((b for b in self.blocks if b.path == path), None)
        if block is None:
            return {"ok": False, "error": f"代理不存在: {path}"}
        # 删除整个 location 块，并连带清理前导空行；
        # 若紧邻的上一行是该块的说明注释（# 开头且缩进不小于 location 行），一并删除。
        del_start = block.start
        while del_start > 0 and lines[del_start - 1].strip() == "":
            del_start -= 1
        if del_start > 0:
            prev = lines[del_start - 1]
            loc_indent = len(lines[block.start]) - len(lines[block.start].lstrip())
            if prev.lstrip().startswith("#") and (len(prev) - len(prev.lstrip())) >= loc_indent:
                del_start -= 1
        del lines[del_start : block.end + 1]
        self.content = "\n".join(lines)
        return {"ok": True}

    def restore(self, content: str) -> None:
        """恢复到给定原文（校验失败回滚用）。"""
        self.content = content
        self._write(content)
        self.reload()

    # ---- 目标地址池（与配置文件合一：池 = 全部 proxy_pass 目标并集，增删改查直接写 conf）----

    def pool_targets(self) -> List[dict]:
        """返回地址池：全部代理 proxy_pass 目标（激活+注释备选）按出现顺序去重。
        去重按 _pool_key 规范化口径（等价写法合并为一条，取首个写法展示）；
        别名取行尾注释（同地址任意等价行中第一个非空别名）。"""
        self._refresh()
        out: List[dict] = []
        index: dict = {}
        for b in self.blocks:
            for idx in b.pp_lines:
                url = b.pp_values[idx]
                alias = b.pp_comments.get(idx, "")
                key = _pool_key(url)
                item = index.get(key)
                if item is None:
                    item = {"target": url, "alias": alias}
                    index[key] = item
                    out.append(item)
                elif alias and not item["alias"]:
                    item["alias"] = alias
        return out

    def pool_add(self, target: str, alias: str = "") -> dict:
        """池新增：校验后把 target 追加为所有代理块的注释备选行（不改变激活目标）。
        已存在同一地址（含等价写法）时拒绝。"""
        target = (target or "").strip()
        alias = _sanitize_alias(alias)
        if _normalize_target(target) is None:
            return {"ok": False, "error": f"target 非法: {target}"}
        self._refresh()
        key = _pool_key(target)
        for b in self.blocks:
            for url in b.pp_values.values():
                if _pool_key(url) == key:
                    return {"ok": False, "error": f"目标已在池中（存在等价写法 {url}）: {target}"}
        if not self.blocks:
            return {"ok": False, "error": "当前配置中没有代理，无法添加目标地址（请先添加代理）"}
        lines = self.content.split("\n")
        # 依块尾倒序插入，避免行号偏移；新行放在每块最后一个 proxy_pass 行之后
        for b in sorted(self.blocks, key=lambda x: x.end, reverse=True):
            last_pp = max(b.pp_lines)
            m = PROXY_PASS_RE.match(lines[last_pp])
            indent = m.group(1) if m and m.group(1) else "    "
            lines.insert(last_pp + 1, _pp_line(indent, True, target, alias))
        self.content = "\n".join(lines)
        return {"ok": True}

    def pool_set_alias(self, target: str, alias: str) -> dict:
        """池改别名：重写该地址（含全部等价写法行）的行尾注释（别名留空即清除）。"""
        target = (target or "").strip()
        alias = _sanitize_alias(alias)
        self._refresh()
        key = _pool_key(target)
        hits = [(b, idx) for b in self.blocks for idx in b.pp_lines if _pool_key(b.pp_values[idx]) == key]
        if not hits:
            return {"ok": False, "error": f"目标不在池中: {target}"}
        lines = self.content.split("\n")
        for _, idx in hits:
            m = PROXY_PASS_RE.match(lines[idx])
            if not m:
                continue
            indent, commented, url = m.group(1), bool(m.group(2)), m.group(3).strip()
            lines[idx] = _pp_line(indent, commented, url, alias)
        self.content = "\n".join(lines)
        return {"ok": True}

    def pool_remove(self, target: str) -> dict:
        """池删除：从所有代理块移除该地址（含全部等价写法）的 proxy_pass 行；
        若在某个代理中处于激活状态（含等价写法）则拒绝，需先切换。"""
        target = (target or "").strip()
        self._refresh()
        key = _pool_key(target)
        idxs: List[int] = []
        for b in self.blocks:
            active_here = b.active is not None and _pool_key(b.active) == key
            for idx in b.pp_lines:
                if _pool_key(b.pp_values[idx]) == key:
                    if active_here:
                        return {"ok": False,
                                "error": f"目标在代理 {b.path} 中处于激活状态，请先切换其他目标后再删除"}
                    idxs.append(idx)
        if not idxs:
            return {"ok": False, "error": f"目标不在池中: {target}"}
        lines = self.content.split("\n")
        for idx in sorted(idxs, reverse=True):
            del lines[idx]
        self.content = "\n".join(lines)
        return {"ok": True}

    def commit(self) -> None:
        """写回磁盘。"""
        self._write(self.content)
        self.reload()
