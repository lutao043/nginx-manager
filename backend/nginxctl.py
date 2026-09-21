# -*- coding: utf-8 -*-
"""nginxctl.py — nginx 跨平台控制模块（Windows / macOS / Linux）

职责：
  - nginx -t 语法校验（含错误文件/行号解析）
  - start / stop / reload / restart 进程控制
  - 运行状态检测（进程 + 版本 + pid）
  - 配置文件 include 解析（生成配置树）
  - 错误日志定位与读取、访问日志候选定位与尾部读取
  - stub_status 检测 / listen 端口解析 / 实时连接指标抓取

设计约定：
  - 所有命令通过 subprocess 执行，参数一律用列表传递，不经过 shell（防注入）。
  - prefix 推断：优先 `nginx -V` 输出的编译期 --prefix=（发行版布局下 confDir 的父目录是错的，
    如 confDir=/etc/nginx 会推出 /etc）；取不到时退回「confDir 的父目录」启发式。
  - pid 文件路径：优先主配置里的 `pid` 指令（发行版常用 /run/nginx.pid），
    其次 `nginx -V` 的 --pid-path=，最后 <prefix>/logs/nginx.pid。
    -s 系列命令一律带 -c 主配置：不带 -c 时 nginx 会读默认 conf 路径的 pid，
    在自定义 confDir 下会操作到另一个实例。
  - 本模块不抛业务异常，方法返回 (ok: bool, data: dict) 或 (ok, message)，
    由 server.py 统一转 HTTP 响应。
"""
from __future__ import annotations

import collections
import glob
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from typing import List, Optional, Tuple

WIN = sys.platform.startswith("win")
MAC = sys.platform == "darwin"
LINUX = sys.platform.startswith("linux")

CONF_MAIN = "nginx.conf"


def _run(cmd: List[str], timeout: int = 15, cwd: Optional[str] = None) -> Tuple[int, str, str]:
    """执行命令，返回 (returncode, stdout, stderr)。编码按平台处理。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if WIN else 0,
        )
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except FileNotFoundError:
        return 127, "", f"无法执行命令: {' '.join(cmd)}（文件不存在）"
    except subprocess.TimeoutExpired:
        return 124, "", "命令执行超时"


def _strip_quoted(line: str) -> str:
    """去掉引号包裹的字符串内容（支持 \\ 转义），避免字符串里的 {} 影响块边界判断。"""
    out = []
    quote = ""
    i = 0
    while i < len(line):
        ch = line[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


class NginxController:
    """nginx 控制核心。构造后所有方法自动适配当前平台。"""

    def __init__(self, nginx_path: str, conf_dir: str):
        self.nginx_path = os.path.abspath(nginx_path)
        self.conf_dir = os.path.abspath(conf_dir)
        self.prefix = self._detect_prefix()

    # ---------- 路径推断 ----------

    _PID_RE = re.compile(r"^\s*pid\s+([^;]+);")

    def _compile_info(self) -> dict:
        """解析 `nginx -V` 的 configure 参数（--prefix= / --pid-path=）。

        按 exe 路径 + mtime 缓存：同一进程内只起一次子进程；替身脚本或权限不足时
        返回空字典，调用方退回启发式推算。
        """
        try:
            mtime = os.path.getmtime(self.nginx_path)
        except OSError:
            mtime = None
        cached = getattr(self, "_compile_cache", None)
        if cached and cached[0] == self.nginx_path and cached[1] == mtime:
            return cached[2]
        info: dict = {}
        if os.path.isfile(self.nginx_path):
            _code, out, err = _run([self.nginx_path, "-V"], timeout=10)
            text = (err or "") + (out or "")
            m = re.search(r"--prefix=(\S+)", text)
            if m:
                info["prefix"] = os.path.abspath(m.group(1))
            m = re.search(r"--pid-path=(\S+)", text)
            if m:
                info["pidPath"] = os.path.abspath(m.group(1))
        self._compile_cache = (self.nginx_path, mtime, info)
        return info

    def _detect_prefix(self) -> str:
        """prefix：nginx -V 的编译期 --prefix= 优先，取不到时用 confDir 的父目录。"""
        p = self._compile_info().get("prefix")
        if p:
            return p
        parent = os.path.dirname(self.conf_dir)
        # confDir 本身也可能是 prefix（如 /etc/nginx 下无子目录）
        return parent if parent and parent != self.conf_dir else self.conf_dir

    def pid_file_path(self) -> str:
        """pid 文件路径：主配置 pid 指令 → nginx -V 的 --pid-path → <prefix>/logs/nginx.pid。

        读配置而不是硬拼 <prefix>/logs：发行版配置常写 /run/nginx.pid，
        拼错路径会导致「在运行却报未运行」，或在 pgrep 兜底下误判到别的实例。
        """
        try:
            with open(self.main_conf_path(), "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = self._PID_RE.match(self._strip_config_comment(line))
                    if m:
                        raw = m.group(1).strip().strip("'\"")
                        if raw and raw.lower() != "off":
                            return raw if os.path.isabs(raw) else os.path.join(self.prefix, raw)
        except OSError:
            pass
        p = self._compile_info().get("pidPath")
        if p:
            return p
        return os.path.join(self.prefix, "logs", "nginx.pid")

    # ---------- 基础命令构造 ----------

    def _base_cmd(self) -> List[str]:
        return [self.nginx_path, "-p", self.prefix]

    def _test_cmd(self) -> List[str]:
        return self._base_cmd() + ["-t", "-c", self.main_conf_path()]

    def _signal_cmd(self, action: str) -> List[str]:
        """-s 系列命令：必须带 -c 主配置，否则 nginx 按默认 conf 路径找 pid，
        在自定义 confDir（如临时目录/多实例）下会操作到另一个实例或直接失败。"""
        return self._base_cmd() + ["-s", action, "-c", self.main_conf_path()]

    def main_conf_path(self) -> str:
        return os.path.join(self.conf_dir, CONF_MAIN)

    # ---------- 配置校验 ----------

    def test_config(self) -> Tuple[bool, dict]:
        """nginx -t 校验。返回 (ok, {ok, output, errFile?, errLine?})。
        校验失败时从输出解析出错文件与行号（如 `... in /path/nginx.conf:12`），
        供前端定位编辑器行；解析失败时缺省这两个字段。"""
        if not os.path.isfile(self.nginx_path):
            return False, {"ok": False, "output": f"nginx 可执行文件不存在: {self.nginx_path}"}
        code, _out, err = _run(self._test_cmd())
        output = (err or _out).strip()
        ok = code == 0 and "successful" in output
        result = {"ok": ok, "output": output}
        if code != 0:
            # nginx -t 失败输出常为多行：先是 [alert] 提示，中间才是含出错位置的一行，
            # 末尾还有 "configuration file ... test failed" 汇总；故逐行匹配取最后一次命中。
            # Windows 盘符路径含 ':'，用非贪婪匹配 + 行尾 :数字 锚定到行号。
            for line in output.splitlines():
                m = re.search(r"in\s+(.+?):(\d+)\s*$", line)
                if not m:
                    continue
                result["errFile"] = m.group(1).strip()
                try:
                    result["errLine"] = int(m.group(2))
                except ValueError:
                    pass
        return code == 0, result

    # ---------- 版本 / 进程 ----------

    def get_version(self) -> Optional[str]:
        """nginx -v 结果缓存：版本随 exe 固定，无需每次起子进程；exe 被替换（mtime 变化）时自动失效。"""
        try:
            mtime = os.path.getmtime(self.nginx_path)
        except OSError:
            mtime = None
        cached = getattr(self, "_version_cache", None)
        if cached and cached[0] == self.nginx_path and cached[1] == mtime:
            return cached[2]
        code, _out, err = _run(self._base_cmd() + ["-v"], timeout=10)
        output = (err or _out).strip()
        m = re.search(r"nginx/([\d.]+)", output)
        version = m.group(1) if m else None
        self._version_cache = (self.nginx_path, mtime, version)
        return version

    def _windows_nginx_procs(self) -> List[dict]:
        """Windows：通过 CIM 获取 nginx.exe 进程（含可执行路径，用于匹配多版本场景）。

        PowerShell 在部分环境（杀毒扫描/策略限制）下会卡到数十秒，两道防线：
        1. 超时收紧到 8s，快速降级 tasklist；
        2. 连续失败 2 次后熔断——本次运行内不再尝试 PowerShell，直接走 tasklist。
        """
        # 熔断命中：直接走降级路径
        if getattr(self, "_ps_broken", False):
            return self._tasklist_nginx_procs()
        ps_script = (
            "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
            "Get-CimInstance Win32_Process -Filter \"Name='nginx.exe'\" "
            "| Select-Object ProcessId,ExecutablePath | ConvertTo-Json -Compress"
        )
        try:
            code, out, err = _run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
                timeout=8,
            )
        except Exception:
            code, out, err = 1, "", ""
        if code != 0 or not out.strip():
            # 连续失败计数，达到 2 次熔断
            fails = getattr(self, "_ps_fail_count", 0) + 1
            self._ps_fail_count = fails
            if fails >= 2:
                self._ps_broken = True
            return self._tasklist_nginx_procs()
        self._ps_fail_count = 0
        try:
            import json

            data = json.loads(out)
            if isinstance(data, dict):
                data = [data]
            procs = []
            for p in data:
                pid = p.get("ProcessId")
                exe = p.get("ExecutablePath")
                procs.append({"pid": int(pid) if str(pid).isdigit() else None, "exePath": exe})
            return procs
        except Exception:
            return []

    @staticmethod
    def _tasklist_nginx_procs() -> List[dict]:
        """降级：tasklist 仅按文件名，拿不到 exePath（多版本精确匹配失效，退化为单实例判定）。"""
        code, out, _ = _run(["tasklist", "/FI", "IMAGENAME eq nginx.exe", "/FO", "CSV", "/NH"], timeout=15)
        procs = []
        for line in out.splitlines():
            parts = [p.strip().strip('"') for p in line.split('","')]
            if len(parts) >= 2 and parts[0].lower() == "nginx.exe":
                pid = parts[1] if parts[1].isdigit() else None
                procs.append({"pid": int(pid) if pid else None, "exePath": None})
        return procs

    def _posix_nginx_pid(self) -> Optional[int]:
        """类 Unix：只认 pid 文件里那个「确实是 nginx」的活跃进程。

        不再退回 pgrep：pgrep -f "nginx: master process" 是全机器范围的，
        多实例/多版本共存时会把别的 nginx 当成自己的（UI 显示错误的 PID，
        停止/重载作用到别的实例）；pid 文件才是本实例的权威来源。
        """
        pid_file = self.pid_file_path()
        try:
            with open(pid_file, "r", encoding="utf-8", errors="replace") as f:
                pid = int(f.read().strip())
        except (ValueError, OSError):
            return None
        if pid <= 0 or not self._pid_alive(pid):
            return None
        return pid if self._pid_is_nginx(pid) else None

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        if WIN:
            code, out, _ = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=10)
            return code == 0 and f'"{pid}"' in out
        try:
            os.kill(pid, 0)
            return True
        except (OSError, OverflowError, ValueError):
            return False

    @staticmethod
    def _pid_is_nginx(pid: int) -> bool:
        """pid 是否确为 nginx 进程。

        pid 文件残留（崩溃/被强杀/被复用）时，PID 可能已被系统分配给别的进程；
        直接把它当 nginx「运行中」会给出错误的 PID，信号也可能打到无关进程。
        ps 不可用时按 True 处理（宁可保留 pid 文件，也不要误删活进程的记录）。
        """
        if WIN:
            code, out, _ = _run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=10)
            return code == 0 and "nginx" in out.lower()
        code, out, _ = _run(["ps", "-p", str(pid), "-o", "comm="], timeout=10)
        if code != 0 or not out.strip():
            return True  # ps 不可用/无输出：信息不足，不做否定判断
        return "nginx" in os.path.basename(out.strip()).lower()

    def detect_process(self) -> Optional[dict]:
        """检测 nginx 是否在运行。返回 {running, pid, version, matched} 或 None（未配置时）。"""
        if not os.path.isfile(self.nginx_path):
            return {"running": False, "pid": None, "version": None, "matched": True}
        version = self.get_version()
        if WIN:
            procs = self._windows_nginx_procs()
            running_proc = None
            for p in procs:
                # 精确匹配可执行路径；多版本共存时只认配置指向的那个
                if p.get("exePath"):
                    if os.path.normcase(os.path.abspath(p["exePath"])) == os.path.normcase(self.nginx_path):
                        running_proc = p
                        break
            # 找不到精确路径匹配时（tasklist 降级模式无 exePath / 多实例共存）：
            # 只要存在 nginx.exe 进程就视为运行中。不要求恰好 1 个——多实例场景下
            # len(procs) > 1 是正常状态（旧 master 未退出 + 新 master 已启动），返回 False 会导致
            # restart() 跳过 stop 直接 start() → 实例越积越多。
            if running_proc is None and procs:
                running_proc = procs[0]
            return {
                "running": running_proc is not None,
                "pid": running_proc["pid"] if running_proc else None,
                "version": version,
                "matched": True,
            }
        else:
            pid = self._posix_nginx_pid()
            return {"running": pid is not None, "pid": pid, "version": version, "matched": True}

    # ---------- 进程控制 ----------

    def _clean_stale_pid_file(self) -> None:
        """清理残留的 nginx.pid：空 / 非数字 / pid 已死 / pid 已被非 nginx 进程占用。

        nginx 异常退出（崩溃/被强杀/断电）时会残留 pid 文件，导致下次启动或
        重载报「invalid PID number ""」；pid 被复用时更危险——它指向无关进程。
        删掉是安全的：nginx 启动时会自动重建。
        """
        pid_file = self.pid_file_path()
        if not os.path.isfile(pid_file):
            return
        try:
            with open(pid_file, "r", encoding="utf-8", errors="replace") as f:
                raw = f.read().strip()
        except OSError:
            return
        stale = False
        if not raw:
            stale = True
        else:
            try:
                pid = int(raw)
            except ValueError:
                stale = True
            else:
                stale = pid <= 0 or not self._pid_alive(pid) or not self._pid_is_nginx(pid)
        if stale:
            try:
                os.remove(pid_file)
            except OSError:
                pass

    def start(self) -> Tuple[bool, str]:
        """启动 nginx。关键：master 常驻不退出，必须用 Popen 分离启动，
        不能用 subprocess.run（会因不退出而超时，超时后连带杀掉 master 进程树）。"""
        if not os.path.isfile(self.nginx_path):
            return False, f"nginx 可执行文件不存在: {self.nginx_path}"
        if self.is_running():
            return False, "nginx 已在运行"
        self._clean_stale_pid_file()  # 清理空/损坏/失效 pid 文件
        # 启动前先校验配置，拿错误信息（比启动失败后再猜原因直观）
        code, result = self.test_config()
        if not result.get("ok"):
            return False, f"nginx -t 校验失败，未启动: {result.get('output', '')}"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if WIN else 0
        try:
            subprocess.Popen(
                self._base_cmd() + ["-c", self.main_conf_path()],
                cwd=self.prefix,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=flags,
            )
        except OSError as e:
            return False, f"nginx 启动失败: {e}"
        time.sleep(1.2)  # 给 master 一点启动时间
        info = self.detect_process()
        if info and info["running"]:
            return True, "nginx 已启动"
        return False, "nginx 启动失败：请检查端口占用或错误日志"

    def is_running(self) -> bool:
        info = self.detect_process()
        return bool(info and info.get("running"))

    def stop(self, wait: float = 10.0) -> Tuple[bool, str]:
        """仅用 -s quit（优雅退出），不使用 -s stop（强制停止）。

        -s stop 会立即终止 master + 所有 worker，可能丢失处理中的请求；
        且在多实例场景下容易误伤其他 nginx 进程。
        quit 只是「通知」：worker 处理完存量连接前进程仍在，所以这里轮询确认真的退出，
        而不是睡 1 秒就回报成功（否则用户以为停了，紧接着 start 报端口占用）。
        """
        if not self.is_running():
            return False, "nginx 未在运行"
        pid = None if WIN else self._posix_nginx_pid()
        code, _out, err = _run(self._signal_cmd("quit"), timeout=15)
        if code != 0:
            detail = (err or _out).strip()
            return False, "nginx 优雅退出失败" + (f": {detail}" if detail else "")
        deadline = time.time() + max(0.0, wait)
        while True:
            exited = (not self._pid_alive(pid)) if pid is not None else (not self.is_running())
            if exited:
                self._clean_stale_pid_file()
                return True, "nginx 已停止"
            if time.time() >= deadline:
                return False, f"已发送优雅退出信号，但 nginx 在 {wait:.0f} 秒内仍在运行（可能有长连接），请稍后重试"
            time.sleep(0.3)

    def reload(self) -> Tuple[bool, str]:
        if not self.is_running():
            return False, "nginx 未在运行"
        self._clean_stale_pid_file()  # 重载前清理，避免 nginx 读空 pid 报错
        code, _out, err = _run(self._signal_cmd("reload"), timeout=15)
        if code != 0:
            detail = (err or _out).strip()
            return False, f"nginx 重载失败" + (f": {detail}" if detail else "")
        return True, "nginx 配置已重载"

    def restart(self, wait: float = 10.0) -> Tuple[bool, str]:
        """先优雅退出并确认退出，再 start。

        退出确认不能省：quit 后立刻 start 会在旧 master 未退时起第二个实例
        （端口冲突或两个 master 并存）；quit 失败（例如本来没在运行）不阻断，
        交给 start 的检测与报错兜底。
        """
        _run(self._signal_cmd("quit"), timeout=15)
        deadline = time.time() + max(0.0, wait)
        while self.is_running():
            if time.time() >= deadline:
                return False, "旧 nginx 实例在 %d 秒内没有退出，已取消启动（避免同时存在两个实例）" % wait
            time.sleep(0.3)
        self._clean_stale_pid_file()  # 重启前清理
        return self.start()

    # ---------- 配置树 / include 解析 ----------

    def _resolve_include_pattern(self, pattern: str) -> List[str]:
        """把 include 指令的 pattern 展开为实际文件列表（支持绝对/相对路径与通配符）。"""
        pattern = pattern.strip().strip("'\"")
        if not pattern:
            return []
        candidates = []
        if os.path.isabs(pattern):
            candidates.append(pattern)
        else:
            # nginx 的 include 相对 prefix 解析；这里兼容 prefix 与 confDir 两种基准
            candidates.append(os.path.join(self.prefix, pattern))
            if self.conf_dir != self.prefix:
                candidates.append(os.path.join(self.conf_dir, pattern))
        files: List[str] = []
        seen: set = set()
        for base in candidates:
            if any(ch in pattern for ch in "*?["):
                for hit in glob.glob(base):
                    fp = os.path.abspath(hit)
                    if os.path.isfile(fp) and fp not in seen:
                        seen.add(fp)
                        files.append(fp)
            else:
                fp = os.path.abspath(base)
                if os.path.isfile(fp) and fp not in seen:
                    seen.add(fp)
                    files.append(fp)
        return files

    _INCLUDE_RE = re.compile(r"include\s+([^;]+);")

    def collect_included_files(self) -> List[str]:
        """递归解析 nginx.conf 的 include，返回所有被引用的配置文件绝对路径（含主配置）。"""
        result: List[str] = []
        visited: set = set()
        queue: List[str] = [self.main_conf_path()]

        def rel_ok(fp: str) -> bool:
            return fp.startswith(self.conf_dir) or fp.startswith(self.prefix)

        while queue:
            cur = queue.pop(0)
            cur = os.path.abspath(cur)
            if cur in visited or not os.path.isfile(cur):
                continue
            visited.add(cur)
            if rel_ok(cur) and cur not in result:
                result.append(cur)
            try:
                with open(cur, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            for m in self._INCLUDE_RE.finditer(content):
                pattern = m.group(1)
                for hit in self._resolve_include_pattern(pattern):
                    hit = os.path.abspath(hit)
                    if rel_ok(hit) and hit not in visited:
                        queue.append(hit)
        result.sort(key=lambda p: (p.count(os.sep), p.lower()))
        return result

    def build_config_tree(self) -> Tuple[List[dict], List[str]]:
        """返回 (tree, included)。tree 为配置目录下按目录分组的文件树。"""
        included_files = self.collect_included_files()
        if not included_files:
            return [], []

        # 相对路径集合
        rels = []
        for fp in included_files:
            if fp.startswith(self.conf_dir):
                rels.append(os.path.relpath(fp, self.conf_dir))
            else:
                # 在 confDir 之外（例如 prefix/conf.d），用相对 prefix 路径展示
                rels.append(os.path.relpath(fp, self.prefix))

        def make_node(rel: str, is_dir: bool) -> dict:
            return {"path": rel.replace("\\", "/"), "name": os.path.basename(rel) or rel, "isDir": is_dir, "children": []}

        # 构建目录树
        root: List[dict] = []

        def find_child(nodes: List[dict], name: str) -> Optional[dict]:
            for n in nodes:
                if n["name"] == name and n["isDir"]:
                    return n
            return None

        for rel in rels:
            parts = rel.split(os.sep)
            cur = root
            for i, part in enumerate(parts):
                is_last = i == len(parts) - 1
                if is_last:
                    node = make_node("/".join(parts[: i + 1]), False)
                    node.pop("children")
                    cur.append(node)
                else:
                    child = find_child(cur, part)
                    if child is None:
                        child = make_node("/".join(parts[: i + 1]), True)
                        cur.append(child)
                    cur = child["children"]
        return root, [r.replace("\\", "/") for r in rels]

    # ---------- 错误日志 ----------

    _ERROR_LOG_RE = re.compile(r"^\s*error_log\s+([^;]+);")

    def find_error_log_paths(self) -> List[str]:
        """候选错误日志路径：配置 error_log 指令解析（相对 prefix）+ 默认兜底路径，去重。

        与访问日志同口径：发行版常把 error_log 写到 /var/log/nginx 这类 prefix 之外的位置，
        只看 <prefix>/logs/error.log 会把「别的文件」当成当前日志展示。
        跳过 stderr / syslog: / memory: / off 等非文件目标。返回的路径可能尚不存在。
        """
        paths: List[str] = []

        def _push(p: str) -> None:
            p = os.path.normpath(os.path.abspath(p))
            if p not in paths:
                paths.append(p)

        for fp in self.collect_included_files():
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            for line in content.split("\n"):
                m = self._ERROR_LOG_RE.match(self._strip_config_comment(line))
                if not m:
                    continue
                first = m.group(1).strip().split()[0].strip("'\"") if m.group(1).strip() else ""
                if not first or first in ("off", "stderr") or first.startswith(("syslog:", "memory:")):
                    continue
                _push(first if os.path.isabs(first) else os.path.join(self.prefix, first))
        for d in (
            os.path.join(self.prefix, "logs", "error.log"),
            os.path.join(self.conf_dir, "logs", "error.log"),
            os.path.join(self.conf_dir, "error.log"),
        ):
            _push(d)
        return paths

    def locate_error_log(self) -> Optional[str]:
        """定位 error.log：配置里声明的路径优先，其次 prefix/logs、confDir/logs 兜底。"""
        for c in self.find_error_log_paths():
            if os.path.isfile(c):
                return c
        return None

    def read_error_log(self, lines: int = 200) -> Tuple[Optional[str], str]:
        log_path = self.locate_error_log()
        if not log_path:
            return None, ""
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.readlines()
            return log_path, "".join(all_lines[-max(1, min(lines, 5000)):])
        except OSError:
            return log_path, ""

    # ---------- 访问日志 ----------

    _ACCESS_LOG_RE = re.compile(r"^\s*access_log\s+([^;]+);")

    @staticmethod
    def _strip_config_comment(line: str) -> str:
        """去掉行内注释（'#' 前为空白或行首时才视为注释起点）。"""
        out = []
        for i, ch in enumerate(line):
            if ch == "#" and (i == 0 or line[i - 1] in " \t"):
                break
            out.append(ch)
        return "".join(out)

    def find_access_log_paths(self) -> List[str]:
        """候选访问日志路径：配置 access_log 指令解析（相对 prefix）+ 默认兜底路径，去重。
        跳过 off / syslog: / memory: 等非文件目标。返回的路径可能尚不存在（文件轮转前）。"""
        paths: List[str] = []

        def _push(p: str) -> None:
            p = os.path.normpath(os.path.abspath(p))
            if p not in paths:
                paths.append(p)

        for fp in self.collect_included_files():
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue
            for line in content.split("\n"):
                m = self._ACCESS_LOG_RE.match(self._strip_config_comment(line))
                if not m:
                    continue
                first = m.group(1).strip().split()[0].strip("'\"") if m.group(1).strip() else ""
                if not first or first == "off" or first.startswith(("syslog:", "memory:")):
                    continue
                _push(first if os.path.isabs(first) else os.path.join(self.prefix, first))
        for d in (
            os.path.join(self.prefix, "logs", "access.log"),
            os.path.join(self.conf_dir, "logs", "access.log"),
            os.path.join(self.conf_dir, "access.log"),
        ):
            _push(d)
        return paths

    def read_log_file(self, path: str, lines: int = 500) -> str:
        """读取日志文件尾部（deque 有界读取，避免大文件整体载入内存）。
        文件不存在/不可读时返回空字符串。"""
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                tail = collections.deque(f, maxlen=max(1, min(lines, 5000)))
            return "".join(tail)
        except OSError:
            return ""

    # ---------- stub_status 实时指标 ----------

    _STUB_RE = re.compile(r"^\s*stub_status\s*;")
    _LISTEN_RE = re.compile(r"^\s*listen\s+([^;]+);")
    _SERVER_HEAD_RE = re.compile(r"^\s*server\s*\{")
    _LOCATION_HEAD_RE = re.compile(r"^\s*location\s+(.+?)\s*(?:\{\s*)?$")

    @staticmethod
    def _location_path_of(expr: str) -> str:
        """location 表达式取路径部分（跳过 = ~ ~* ^~ 修饰符），与 proxymgr 解析口径一致。"""
        parts = expr.strip().split()
        if parts and parts[0] in ("=", "~", "~*", "^~"):
            return parts[1] if len(parts) > 1 else ""
        return parts[0] if parts else ""

    @classmethod
    def _port_of_token(cls, token: str) -> Optional[int]:
        """listen 参数取端口：`80` / `127.0.0.1:8080` / `[::]:80`；unix: 与纯地址返回 None。"""
        token = token.strip()
        if not token or token.startswith("unix:"):
            return None
        if token.startswith("["):  # [::]:80
            rest = token[token.find("]") + 1:] if "]" in token else ""
            port = rest.lstrip(":")
            return int(port) if port.isdigit() else None
        if ":" in token:
            token = token.rsplit(":", 1)[1]
        return int(token) if token.isdigit() else None

    def _scan_stub_status(self, lines: List[str]) -> Tuple[Optional[str], Optional[int]]:
        """单遍扫描一个配置文件，返回 stub_status 所在 location 路径与所属 server 的 listen 端口。

        用「块种类栈」跟踪上下文：stub_status 写在哪台 server，指标就该走那台 server 的
        listen 端口。旧实现把「配置里第一个 listen」和「stub_status 所在 location」分开取，
        多 server / 多端口配置下会抓到错误端口（表现为指标一直取不到）。
        """
        stack: List[dict] = []
        for raw in lines:
            s = _strip_quoted(self._strip_config_comment(raw))
            kind = None
            head = self._LOCATION_HEAD_RE.match(s)
            if head:
                kind = "location"
            elif self._SERVER_HEAD_RE.match(s):
                kind = "server"
            elif re.match(r"^\s*http\s*\{", s):
                kind = "http"
            elif re.match(r"^\s*stream\s*\{", s):
                kind = "stream"
            elif re.match(r"^\s*upstream\s", s):
                kind = "upstream"
            else:
                head = None
            lm = self._LISTEN_RE.match(s)
            if lm:
                port = self._port_of_token(lm.group(1).strip().split()[0])
                for frame in reversed(stack):
                    if frame["kind"] == "server":
                        if frame.get("listen") is None:
                            frame["listen"] = port
                        break
            if self._STUB_RE.match(s):
                path = port = None
                for frame in reversed(stack):
                    if frame["kind"] == "location" and path is None:
                        path = frame.get("path")
                    if frame["kind"] == "server":
                        port = frame.get("listen")
                        break
                return path, port
            for ch in s:
                if ch == "{":
                    frame = {"kind": kind or "block", "listen": None}
                    if kind == "location" and head:
                        frame["path"] = self._location_path_of(head.group(1))
                    stack.append(frame)
                    kind = None
                elif ch == "}":
                    if stack:
                        stack.pop()
        return None, None

    def find_stub_status(self) -> Optional[dict]:
        """在主配置与 include 文件中查找 stub_status 指令。

        返回 {path, file, port}（port 为所在 server 的 listen 端口，解析不到为 None）
        或 None。location 路径取最近一层包围 location，嵌套 location 场景也成立。
        """
        for fp in self.collect_included_files():
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.read().split("\n")
            except OSError:
                continue
            path, port = self._scan_stub_status(lines)
            if path:
                return {"path": path, "file": fp, "port": port}
        return None

    def detect_listen_port(self) -> Optional[int]:
        """从配置解析第一个 listen 端口（本机抓取 stub_status 用）。
        支持 `listen 80;` / `listen 127.0.0.1:8080;` / `listen [::]:80;`；
        unix: 监听与纯地址无端口形式跳过。找不到返回 None（调用方兜底 80）。"""
        for fp in self.collect_included_files():
            try:
                with open(fp, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.read().split("\n")
            except OSError:
                continue
            for line in lines:
                m = self._LISTEN_RE.match(self._strip_config_comment(line))
                if not m:
                    continue
                port = self._port_of_token(m.group(1).strip().split()[0])
                if port:
                    return port
        return None

    def fetch_stub_status(self, port: int, path: str, timeout: float = 2.0) -> Tuple[Optional[dict], Optional[str]]:
        """请求本机 stub_status 页面并解析标准输出格式。
        返回 (metrics, None) 或 (None, 失败原因)。"""
        url = f"http://127.0.0.1:{port}{path}"
        try:
            req = urllib.request.Request(url, headers={"Connection": "close"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read(65536).decode("utf-8", "replace")
        except Exception as e:
            return None, f"无法访问 {url}: {e}"
        metrics = {}
        m = re.search(r"Active connections:\s*(\d+)", text)
        if not m:
            return None, f"{url} 响应不是 stub_status 格式"
        metrics["active"] = int(m.group(1))
        m = re.search(r"^\s*(\d+)\s+(\d+)\s+(\d+)\s*$", text, re.MULTILINE)
        if m:
            metrics["accepts"], metrics["handled"], metrics["requests"] = (int(x) for x in m.groups())
        m = re.search(r"Reading:\s*(\d+)\s+Writing:\s*(\d+)\s+Waiting:\s*(\d+)", text)
        if m:
            metrics["reading"], metrics["writing"], metrics["waiting"] = (int(x) for x in m.groups())
        return metrics, None


# ---------- 便捷工厂 ----------

def create_controller(nginx_path: Optional[str], conf_dir: Optional[str]) -> Optional[NginxController]:
    """settings 齐备时创建控制器；缺配置返回 None（由 server 层引导配置）。"""
    if not nginx_path or not conf_dir:
        return None
    return NginxController(nginx_path, conf_dir)
