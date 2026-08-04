# -*- coding: utf-8 -*-
"""proxymgr.py — nginx 反向代理管理（解析/修改 nginx.conf 中的 location+proxy_pass 块）

设计约定（与 API.md 契约一致）：
  - 一个代理 = 一个 `location <path> { ... }` 块，块内必须有 proxy_pass 指令。
  - 激活目标 = 块内唯一未注释的 `proxy_pass <url>;` 行。
  - 备选目标 = 块内 `#proxy_pass <url>;` 注释行（切换 = 互换注释状态）。
  - 只识别包含 proxy_pass 的 location 块；静态资源 location 不进入代理列表。

修改一律"整文件文本替换 + nginx -t 校验"，失败恢复原文（调用方负责备份/校验编排）。
"""
from __future__ import annotations

import os
import re
from typing import List, Optional

PROXY_PASS_RE = re.compile(r"^(\s*)(#\s*)?proxy_pass\s+(.+?);\s*$")
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

    @property
    def active(self) -> Optional[str]:
        return self.pp_values.get(self.pp_active) if self.pp_active is not None else None

    @property
    def targets(self) -> List[str]:
        """按配置顺序返回全部目标地址。"""
        return [self.pp_values[i] for i in sorted(self.pp_lines)]


def _count_braces(lines: List[str], start: int) -> int:
    """从 start 行开始统计大括号平衡，返回块结束行索引（含）。"""
    depth = 0
    for i in range(start, len(lines)):
        line = lines[i]
        stripped = line.split("#", 1)[0]  # 去掉行内注释（简单处理）
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
                if not pm.group(2):  # 未注释 → 激活
                    block.pp_active = j
        if block.pp_lines:
            blocks.append(block)
        i = end + 1
    return blocks


# ---------- 修改操作 ----------

def _normalize_target(target: str) -> Optional[str]:
    t = target.strip()
    if not t or re.search(r"\s", t):
        return None
    if not (t.startswith("http://") or t.startswith("https://") or t.startswith("unix:")):
        return None
    return t


def _normalize_path(path: str) -> Optional[str]:
    p = path.strip()
    if not p.startswith("/") or re.search(r"\s", p) or "{" in p or "}" in p:
        return None
    return p


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
        indent, commented, url = m.group(1), m.group(2), m.group(3).strip()
        if idx == target_line:
            lines[idx] = f"{indent}proxy_pass {url};"
        elif idx == old_active and idx != target_line:
            lines[idx] = f"{indent}#proxy_pass {url};"
    return True


def _render_targets(lines: List[str], block: ProxyBlock, targets: List[str]) -> Optional[str]:
    """按新 targets 列表重写块内 proxy_pass 行（激活项不注释，其余注释）。
    若原激活目标不在新列表，则激活第一个。返回新的激活 url；失败返回 None。"""
    active_url = block.active
    if active_url not in targets:
        active_url = targets[0]

    # 删除块内所有旧 proxy_pass 行，换成新行
    new_lines = []
    inserted = False
    insert_pos = None
    for j in range(block.start, block.end + 1):
        if j in block.pp_lines:
            if not inserted:
                indent = "    "
                for url in targets:
                    commented = "#" if url != active_url else ""
                    new_lines.append(f"{indent}{commented}proxy_pass {url};")
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
        target = _normalize_target(target)
        if not target:
            return {"ok": False, "error": "target 非法"}
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
        norm = []
        for t in targets:
            nt = _normalize_target(t)
            if not nt:
                return {"ok": False, "error": f"target 非法: {t}"}
            if nt not in norm:
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
        # 连带删除 location 前的空行/缩进（保留其他内容）
        del_start = block.start
        while del_start > 0 and lines[del_start - 1].strip() == "":
            del_start -= 1
        del lines[del_start : block.end + 1]
        self.content = "\n".join(lines)
        return {"ok": True}

    def restore(self, content: str) -> None:
        """恢复到给定原文（校验失败回滚用）。"""
        self.content = content
        self._write(content)
        self.reload()

    def commit(self) -> None:
        """写回磁盘。"""
        self._write(self.content)
        self.reload()
