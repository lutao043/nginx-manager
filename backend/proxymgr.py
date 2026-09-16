# -*- coding: utf-8 -*-
"""proxymgr.py — nginx 反向代理管理（解析/修改 nginx.conf 中的 location+proxy_pass 块）

设计约定（与 API.md 契约一致）：
  - 一个代理 = 一个 `location <path> { ... }` 块，块内必须有 proxy_pass 指令。
  - 激活目标 = 块内唯一未注释的 `proxy_pass <url>;` 行。
  - 备选目标 = 块内 `#proxy_pass <url>;` 注释行（切换 = 互换注释状态）。
  - 目标地址池 = 全部代理 proxy_pass 目标的并集（含注释备选），直接读写配置文件：
    池条目别名存于 proxy_pass 行尾注释（`proxy_pass http://a; # 别名`）。
  - 只识别包含 proxy_pass 的 location 块；静态资源 location 不进入代理列表。
  - 场景模板只影响新建块：standard（默认三行头）/ websocket / sse / upload；
    列表展示时按块内特征指令反向嗅探模板类型。
  - upstream 管理：解析/重写 nginx.conf `http{}` 内的 `upstream <name> { }` 块，
    保存按规范形式整块重写（地址 → weight → backup → down → 其余参数原样保留）。
  - stub_status 一键开启：向最后一个 server 块写入受管理的只读本机 location 块。

修改一律"整文件文本替换 + nginx -t 校验"，失败恢复原文（调用方负责备份/校验编排）。
"""
from __future__ import annotations

import os
import re
from typing import List, Optional

# 行尾别名注释：`proxy_pass http://a; # 别名`（'#' 前须有空白，避免误伤 URL 中的 '#'）
PROXY_PASS_RE = re.compile(r"^(\s*)(#\s*)?proxy_pass\s+(.+?);\s*(?:#\s*(.*?))?\s*$")
LOCATION_RE = re.compile(r"^(\s*)location\s+(.+?)\s*\{")
UPSTREAM_RE = re.compile(r"^(\s*)upstream\s+([A-Za-z0-9_\-]+)\s*\{")
HTTP_RE = re.compile(r"^(\s*)http\s*\{")


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


# ---------- upstream 解析 ----------

def parse_upstreams(content: str) -> List[dict]:
    """解析配置文本中全部 upstream 块（按出现顺序）。
    返回 [{name, indent, start, end, method, servers}]，servers 为
    [{address, params}]（params 为原始参数词列表，解析交给 _server_info）。"""
    lines = content.split("\n")
    blocks: List[dict] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.lstrip().startswith("#"):
            i += 1
            continue
        m = UPSTREAM_RE.match(line)
        if not m:
            i += 1
            continue
        end = _count_braces(lines, i)
        block = {"name": m.group(2), "indent": m.group(1) or "", "start": i, "end": end,
                 "method": "round_robin", "servers": []}
        for j in range(i + 1, end):
            s = _strip_inline_comment(lines[j]).strip()
            if not s or s.startswith("#"):
                continue
            if s in ("least_conn;", "ip_hash;"):
                block["method"] = s[:-1]
                continue
            sm = re.match(r"^server\s+(.+?);\s*$", s)
            if sm:
                parts = sm.group(1).split()
                if parts:
                    block["servers"].append({"address": parts[0], "params": parts[1:]})
        blocks.append(block)
        i = end + 1
    return blocks


def _server_info(srv: dict) -> dict:
    """把 server 行参数词拆解为结构化信息（weight/backup/down + 其余原样保留）。"""
    info = {"weight": 1, "backup": False, "down": False, "extra": []}
    for p in srv.get("params", []):
        if p.startswith("weight="):
            try:
                info["weight"] = int(p.split("=", 1)[1])
            except ValueError:
                info["extra"].append(p)
        elif p == "backup":
            info["backup"] = True
        elif p == "down":
            info["down"] = True
        else:
            info["extra"].append(p)
    return info


def _render_upstream_block(indent: str, name: str, method: str, servers: List[dict]) -> str:
    """按规范形式渲染 upstream 块：地址 → weight（≠1 才写）→ backup → down → 其余参数。"""
    inner = indent + " " * 4
    lines = [f"{indent}upstream {name} {{"]
    if method and method != "round_robin":
        lines.append(f"{inner}{method};")
    for s in servers:
        bits = [f"server {s['address']}"]
        if s["weight"] != 1:
            bits.append(f"weight={s['weight']}")
        if s["backup"]:
            bits.append("backup")
        if s["down"]:
            bits.append("down")
        bits.extend(s.get("extra", []))
        lines.append(f"{inner}" + " ".join(bits) + ";")
    lines.append(f"{indent}}}")
    return "\n".join(lines)


def _find_http_block(lines: List[str]) -> Optional[tuple]:
    """找第一个顶层 http 块，返回 (start, end, indent)；不存在返回 None。"""
    i = 0
    while i < len(lines):
        if lines[i].lstrip().startswith("#"):
            i += 1
            continue
        m = HTTP_RE.match(lines[i])
        if m:
            return i, _count_braces(lines, i), m.group(1) or ""
        i += 1
    return None


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


# 场景模板：追加在标准三行 proxy_set_header 之后的附加指令（与 API.md 契约一致）
PROXY_TEMPLATES = {
    "standard": [],
    "websocket": [
        "proxy_http_version 1.1;",
        "proxy_set_header Upgrade $http_upgrade;",
        'proxy_set_header Connection "upgrade";',
        "proxy_read_timeout 300s;",
    ],
    "sse": [
        "proxy_buffering off;",
        "proxy_cache off;",
        "proxy_set_header X-Accel-Buffering no;",
        "proxy_read_timeout 3600s;",
    ],
    "upload": [
        "client_max_body_size 1024m;",
        "proxy_request_buffering off;",
        "proxy_read_timeout 300s;",
        "proxy_send_timeout 300s;",
    ],
}

# upstream 名称：proxy_pass http://<name> 直接引用，须为合法标识
_UPSTREAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_\-]*$")
# upstream server 地址：host:port / IP / [ipv6]:port / unix:/path，拒绝空白与结构性字符
_SERVER_ADDR_RE = re.compile(r"^(?:unix:/[^\s;{}#]+|[A-Za-z0-9_.\-]+(?::\d{1,5})?|\[[0-9a-fA-F:]+\](?::\d{1,5})?)$")
_EXTRA_PARAM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_=\.\-/]*$")


def _normalize_upstream_name(name: str) -> Optional[str]:
    n = (name or "").strip()
    return n if _UPSTREAM_NAME_RE.fullmatch(n) else None


def _normalize_server_address(addr: str) -> Optional[str]:
    a = (addr or "").strip()
    return a if _SERVER_ADDR_RE.fullmatch(a) else None


def _normalize_upstream_servers(raw) -> Optional[List[dict]]:
    """校验前端提交的 servers 列表；非法返回 None（含地址重复/weight 越界/参数词非法）。"""
    if not isinstance(raw, list) or not raw:
        return None
    out: List[dict] = []
    seen = set()
    for s in raw:
        if not isinstance(s, dict):
            return None
        addr = _normalize_server_address(str(s.get("address", "")))
        if not addr or addr in seen:
            return None
        seen.add(addr)
        try:
            w = int(s.get("weight", 1) or 1)
        except (TypeError, ValueError):
            return None
        if not 1 <= w <= 100:
            return None
        extra = [str(x) for x in (s.get("extra") or []) if _EXTRA_PARAM_RE.fullmatch(str(x))]
        out.append({"address": addr, "weight": w, "backup": bool(s.get("backup")),
                    "down": bool(s.get("down")), "extra": extra})
    return out


def _proxy_template(path: str, target: str, template: str = "standard") -> str:
    extra_lines = PROXY_TEMPLATES.get(template) or []
    text = (
        f"        location {path} {{\n"
        f"            proxy_pass {target};\n"
        f"            proxy_set_header Host $host;\n"
        f"            proxy_set_header X-Real-IP $remote_addr;\n"
        f"            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;\n"
    )
    for ln in extra_lines:
        text += f"            {ln}\n"
    text += "        }\n"
    return text


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
        lines = self.content.split("\n")
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
                "template": self._detect_template(b, lines),
            })
        return out

    def _has_headers(self, block: ProxyBlock) -> bool:
        lines = self.content.split("\n")
        for j in range(block.start, block.end + 1):
            if "proxy_set_header" in lines[j]:
                return True
        return False

    @staticmethod
    def _detect_template(block: ProxyBlock, lines: List[str]) -> str:
        """按块内特征指令嗅探模板类型（仅用于列表展示，与 PROXY_TEMPLATES 键对应）。"""
        joined = "\n".join(_strip_inline_comment(lines[j]) for j in range(block.start, block.end + 1))
        if re.search(r"proxy_set_header\s+Upgrade\s", joined):
            return "websocket"
        if re.search(r"proxy_buffering\s+off", joined):
            return "sse"
        if re.search(r"client_max_body_size", joined):
            return "upload"
        return "standard"

    # ---- 操作：调用方负责备份与 nginx -t 编排，失败时调用 restore() ----

    def add(self, path: str, target: str, template: str = "standard") -> dict:
        path = _normalize_path(path)
        target = _normalize_target(target)
        if template not in PROXY_TEMPLATES:
            return {"ok": False, "error": f"模板不支持: {template}"}
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
        block = _proxy_template(path, target, template).rstrip("\n")
        lines.insert(insert_at, block)
        self.content = "\n".join(lines)
        self._refresh()
        return {"ok": True, "proxy": {"path": path, "active": target, "targets": [target],
                                      "proxyHeaders": True, "template": template}}

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

    # ---- 负载均衡 upstream（与配置文件合一：直接读写 nginx.conf http{} 内的 upstream 块）----

    def upstream_list(self) -> List[dict]:
        self._refresh()
        ups = parse_upstreams(self.content)
        # 引用统计：代理目标（激活+备选）中 http://<name> 的 location 路径
        used: dict = {}
        for b in self.blocks:
            for idx in b.pp_lines:
                m = re.match(r"^https?://([^/?#]+)", b.pp_values[idx], re.IGNORECASE)
                if m:
                    used.setdefault(m.group(1).lower(), set()).add(b.path)
        return [{
            "name": u["name"],
            "method": u["method"],
            "servers": [{**_server_info(s), "address": s["address"]} for s in u["servers"]],
            "usedBy": sorted(used.get(u["name"].lower(), set())),
        } for u in ups]

    def upstream_save(self, name: str, method: str, servers) -> dict:
        """新建或整块重写 upstream。返回 {ok, error?}；内容改动落在 self.content，由调用方 commit。"""
        name = _normalize_upstream_name(name)
        if not name:
            return {"ok": False, "error": "upstream 名称非法（字母/数字/下划线/连字符，且不以数字开头）"}
        if method not in ("round_robin", "least_conn", "ip_hash"):
            return {"ok": False, "error": f"调度算法不支持: {method}"}
        norm = _normalize_upstream_servers(servers)
        if norm is None:
            return {"ok": False, "error": "服务器列表非法（至少 1 条，地址合法且不重复，weight 为 1~100 整数）"}
        lines = self.content.split("\n")
        existing = next((u for u in parse_upstreams(self.content) if u["name"] == name), None)
        indent = existing["indent"] if existing else "    "
        text = _render_upstream_block(indent, name, method, norm)
        if existing:
            lines[existing["start"]:existing["end"] + 1] = text.split("\n")
        else:
            http = _find_http_block(lines)
            if http is None:
                return {"ok": False, "error": "未找到 http 块，无法写入 upstream"}
            lines.insert(http[1], text)
        self.content = "\n".join(lines)
        return {"ok": True}

    def upstream_remove(self, name: str) -> dict:
        name = (name or "").strip()
        self._refresh()
        # 引用检查：任何代理的激活/备选目标指向该 upstream 时拒绝删除
        for b in self.blocks:
            for idx in b.pp_lines:
                m = re.match(r"^https?://([^/?#]+)", b.pp_values[idx], re.IGNORECASE)
                if m and m.group(1).lower() == name.lower():
                    return {"ok": False, "error": f"upstream 正在被代理 {b.path} 引用，请先切换或删除该代理"}
        u = next((x for x in parse_upstreams(self.content) if x["name"] == name), None)
        if u is None:
            return {"ok": False, "error": f"upstream 不存在: {name}"}
        lines = self.content.split("\n")
        del lines[u["start"]:u["end"] + 1]
        self.content = "\n".join(lines)
        return {"ok": True}

    # ---- stub_status 一键开启 ----

    def enable_stub_status(self, path: str = "/nginx_status") -> dict:
        """向最后一个 server 块写入受管理的 stub_status location（仅允许本机访问）。
        已存在同名 location 时不重复写入（ok + unchanged）。"""
        path = _normalize_path(path)
        if not path:
            return {"ok": False, "error": "路径非法"}
        lines = self.content.split("\n")
        for line in lines:
            m = LOCATION_RE.match(line)
            if m and m.group(2).split()[-1] == path:
                return {"ok": True, "unchanged": True}
        insert_at = _find_insert_point(lines)
        if insert_at is None:
            return {"ok": False, "error": "未找到 server 块，无法开启状态页"}
        block = (
            f"        location {path} {{\n"
            f"            stub_status;\n"
            f"            allow 127.0.0.1;\n"
            f"            deny all;\n"
            f"        }}"
        )
        lines.insert(insert_at, block)
        self.content = "\n".join(lines)
        return {"ok": True}
